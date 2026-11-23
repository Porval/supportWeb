"""Parse platform and brand coupon declarations from the source-ledger format."""

from collections import defaultdict
from decimal import Decimal
from hashlib import sha256
from io import BytesIO
import posixpath
import re
from unicodedata import normalize
from zipfile import BadZipFile, ZipFile
from xml.etree import ElementTree as ET

from .coupon_workbook import _field
from .planning_workbooks import text, values
from .structured_evidence import StructuredProjection

MAX_GROUPS = 64
PLATFORMS = {'美团':'meituan', '淘宝闪购':'taobao_instant', '淘闪':'taobao_instant'}
PLATFORM_NAMES = {'meituan':'美团', 'taobao_instant':'淘宝闪购'}
NO_SIGNUP = '美团3方券-无需报名'
BOTH = '美团2方混补-10月30号后需要报名'
SIGNUP = '美团+淘闪三方混补需报名'
MAIN = (NO_SIGNUP, BOTH, SIGNUP)
BUSINESS_COLUMNS = {NO_SIGNUP:'BCDEFG', BOTH:'ABCDEFGHJK', SIGNUP:'BCDEFGLM'}

XLSX = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
NS = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
IDENTIFIER = re.compile(r'\d{8,11}')
TIER = re.compile(r'(\d+(?:\.\d{1,2})?)\s*(?:元\s*)?[-减]\s*(\d+(?:\.\d{1,2})?)')
PARTY = re.compile(r'(品补|品牌(?:补贴)?|品(?=\d)|平台(?:补贴)?|美团|淘宝闪购|淘闪|商家|门店|价探|商(?=\d))\s*[:：]?\s*(\d+(?:\.\d{1,2})?)')
ALIASES = {'品补':'brand','品牌补贴':'brand','品牌':'brand','品':'brand',
           '平台补贴':'platform','平台':'platform','美团':'platform','淘宝闪购':'platform','淘闪':'platform',
           '商家':'merchant','门店':'merchant','价探':'merchant','商':'merchant'}


def _xml(archive, filename, limit=8*1024*1024):
    if archive.getinfo(filename).file_size > limit:
        raise ValueError('oversized ledger XML')
    raw=archive.read(filename)
    if b'\x00' in raw or re.search(br'<!\s*(DOCTYPE|ENTITY)',raw,re.I):
        raise ValueError('unsafe workbook XML')
    return ET.fromstring(raw)


def _rows(archive, path, name, strings, take_columns):
    for row in _xml(archive,path).iter(NS+'row'):
        index=int(row.attrib['r'])
        locator={'kind':'xlsx','sheet':name,'row':index}
        selected=[]
        for cell in row.findall(NS+'c'):
            ref=cell.attrib['r']
            match=re.fullmatch(r'([A-Z]{1,3})[1-9][0-9]*',ref)
            if match is None or match[1] not in take_columns:
                continue
            datatype=cell.get('t','n')
            formula=cell.find(NS+'f')
            if formula is not None:
                raw='='+ (formula.text or '')
                src='formula'
            elif datatype=='inlineStr':
                raw=''.join(t.text or '' for t in cell.iter(NS+'t'))
                src='string'
            else:
                raw=cell.findtext(NS+'v') or ''
                if datatype=='s':
                    raw=strings[int(raw)] if raw else ''
                src='string' if datatype in ('s','str') else 'number'
            if raw.strip():
                if len(raw)>500:
                    raise ValueError('ledger declaration cell too long')
                selected.append({'column':match[1],'sourceType':src,'text':raw,'locator':locator|{'cell':ref}})
        if selected:
            yield {'locator':locator,'cells':selected}


def ledger_document(material_id, filename, mime, digest, data):
    """Scan every declaration row of one ledger and retain unique original facts."""
    if mime!=XLSX or not filename.lower().endswith('.xlsx') or len(data)>5*1024*1024:
        return None
    try:
        with ZipFile(BytesIO(data)) as archive:
            book=_xml(archive,'xl/workbook.xml',1024*1024)
            sheet_ids={row.attrib['name']:next(v for k,v in row.attrib.items() if k.endswith('}id'))
                       for row in book.find(NS+'sheets')}
            if not set(MAIN)<=sheet_ids.keys():
                return None
            if sha256(data).hexdigest()!=digest:
                return None
            rel={r.attrib['Id']:r.attrib['Target'] for r in _xml(archive,'xl/_rels/workbook.xml.rels',1024*1024)}
            strings=[''.join(t.text or '' for t in si.iter(NS+'t')) for si in _xml(archive,'xl/sharedStrings.xml')] if 'xl/sharedStrings.xml' in archive.namelist() else []
            compact, warnings = [], []
            for name in MAIN:
                target=rel[sheet_ids[name]]
                path=target.lstrip('/') if target.startswith('/') else posixpath.normpath('xl/'+target)
                if not re.fullmatch(r'xl/worksheets/[A-Za-z0-9_.-]+\.xml',path):
                    return None
                seen=set()
                actual=unique=0
                for row in _rows(archive,path,name,strings,BUSINESS_COLUMNS[name]):
                    cells=values(row)
                    if row['locator']['row']==1 and name!=SIGNUP:
                        compact.append(row)
                        continue
                    if not (text(cells,'B') and text(cells,'C')):
                        continue
                    actual+=1
                    if actual>10000:
                        return None
                    key=tuple(text(cells,col) for col in BUSINESS_COLUMNS[name])
                    if key not in seen:
                        seen.add(key)
                        compact.append(row)
                        unique+=1
                warnings.append({'code':'LEDGER_DECLARATIONS_COMPACTED','message':f'扫描 {actual} 行，保留 {unique} 组不同的活动声明；商品明细见原表。',
                                 'locator':{'kind':'xlsx','sheet':name,'row':1}})
            # A supplementary sheet title is a clue. It never creates or confirms an activity.
            for name in sheet_ids:
                if name.endswith('明细') and TIER.search(name):
                    target=rel[sheet_ids[name]]
                    path=target.lstrip('/') if target.startswith('/') else posixpath.normpath('xl/'+target)
                    if not re.fullmatch(r'xl/worksheets/[A-Za-z0-9_.-]+\.xml',path):
                        return None
                    header=next(_rows(archive,path,name,strings,'ABCDEFGHIJKL'),None)
                    if header:
                        compact.append(header)
            if len(compact)>500:
                return None
            return {'materialId':material_id,'sha256':digest,'kind':'xlsx','status':'parsed','errors':[],
                    'warnings':warnings,'parserVersion':'coupon-ledger-declarations/1.0',
                    'textBlocks':[],'tableRows':compact}
    except (OSError,BadZipFile,RuntimeError,ValueError,KeyError,IndexError,TypeError,ET.ParseError,OverflowError):
        return None


def is_coupon_ledger(documents):
    if len(documents)!=1:
        return False
    doc=documents[0]
    return doc.get('kind')=='xlsx' and doc.get('status')=='parsed' and set(MAIN)<={r['locator'].get('sheet') for r in doc['tableRows']}


def _terms(s):
    literal=normalize('NFKC',s)
    tiers=list(TIER.finditer(literal))
    for i,tier in enumerate(tiers):
        end=tiers[i+1].start() if i+1<len(tiers) else len(literal)
        shares=defaultdict(set)
        for party,amount in PARTY.findall(literal[tier.end():end]):
            shares[ALIASES[party]].add(Decimal(amount))
        if len(shares)==3 and (any(len(v)!=1 for v in shares.values())
                               or sum(next(iter(v)) for v in shares.values())!=Decimal(tier[2])):
            yield tier.group()


def reconcile_ledger(payload,result,metadata,lookup):
    if not is_coupon_ledger(payload['documents']):
        return result,metadata
    doc,=payload['documents']
    projection=StructuredProjection(payload,result,metadata,lookup)
    numbered, brand_only, detail = {}, {}, []
    for row in doc['tableRows']:
        sheet=row['locator'].get('sheet')
        cells=values(row)
        if sheet not in MAIN:
            detail.append((sheet,row))
            continue
        pos=row['locator']['row']
        if sheet==NO_SIGNUP:
            if pos==1:
                if not {'品牌名称','平台','活动时间','机制类型','活动额度（该商品参与档）'}<={c['text'] for c in row['cells']}:
                    return result,metadata
                continue
            brand,platform,scope,period,mechanism,terms=(text(cells,col) for col in 'BCDEFG')
            if not brand or not terms or platform not in PLATFORMS:
                return result,metadata
            key=(PLATFORMS[platform],brand,scope,period,mechanism)
            brand_only.setdefault(key,[]).append((sheet,row,cells,brand,period,terms))
        else:
            if sheet==BOTH and pos==1:
                if not {'品牌','报名id','平台','品牌补贴','价探补贴'}<={c['text'] for c in row['cells']}:
                    return result,metadata
                continue
            if sheet==BOTH:
                platform,identifier= text(cells,'F'),text(cells,'C')
                brand,period,terms=text(cells,'B'),text(cells,'E'),text(cells,'A')
            else:
                platform,identifier=text(cells,'C'),text(cells,'L')
                brand,period,terms=text(cells,'B'),text(cells,'E'),text(cells,'G')
            if platform not in PLATFORMS or not IDENTIFIER.fullmatch(identifier) or not brand:
                return result,metadata
            numbered.setdefault((PLATFORMS[platform],identifier),[]).append((sheet,row,cells,brand,period,terms))
    if not numbered or len(numbered)+len(brand_only)>MAX_GROUPS:
        return result,metadata

    warnings=[]; details_by_key=defaultdict(list)
    for sheet,row in detail:
        match=TIER.search(sheet or '')
        if match:
            brand=sheet[:match.start()].strip()
            tier=tuple(map(Decimal,match.groups()))
            eligible=[]
            for ident,items in numbered.items():
                if any(item[3]==brand and (m:=TIER.search(normalize('NFKC',item[5]))) and tuple(map(Decimal,m.groups()))==tier for item in items):
                    eligible.append(ident)
            if len(eligible)==1:
                details_by_key[eligible[0]].append((sheet,row))
            else:
                warnings.append({'code':'LEDGER_DETAIL_SCOPE_UNCONFIRMED',
                                 'message':f'{sheet}没有唯一活动编号，明细归属待核对。','locator':row['locator']})

    def material_refs(items, cols):
        return list(dict.fromkeys(projection.cell(doc, cells[col])
                                  for _,_,cells,*_ in items for col in cols if col in cells))
    def brands(items):
        entries = {}
        for _,_,cells,brand,*_ in items:
            entries.setdefault(brand,projection.cell(doc,cells['B']))
        return _field(list(entries),list(entries.values()))
    def plan_terms(items):
        return list(dict.fromkeys(t for *_,t in items if t))
    def dates(items):
        return list(dict.fromkeys(p for _,_,_,_,p,_ in items if p))
    def build_rule(items, primary):
        terms=plan_terms(items)
        periods=dates(items)
        summary='；'.join(terms)
        if periods:
            summary+='。原表活动期间：'+'、'.join(periods)
        if primary[0]==SIGNUP:
            state=list(dict.fromkeys(text(c,'M') for _,_,c,*_ in items if text(c,'M')))
            if state:
                summary+='。台账报名原文：'+'、'.join(state)
        return summary

    for key,items in brand_only.items():
        platform,brand,scope,period,mechanism=key
        refs=material_refs(items,'BCDEFG')
        terms=list(dict.fromkeys(term for text_value in plan_terms(items)
                 for term in re.split(r'/(?=\d)',text_value)))
        summary='；'.join(terms)+('。活动期间 '+period if period else '')
        conflicts=list(dict.fromkeys(x for term in terms for x in _terms(term)))
        if conflicts:
            warnings.append({'code':'COUPON_FUNDING_SPLIT_CONFLICT','message':f'{brand}的'+ '、'.join(conflicts)+'档承担金额合计不一致，请提报人确认。',
                             'locator':items[0][1]['locator']})
        typ=projection.user_field('type')
        if conflicts:
            projection.conflicts.append({'field':'rulesText','manualValue':None,'extractedValues':terms,'evidenceIds':refs})
        projection.add({'activityName':_field(f'{brand} {mechanism}',refs),
                        'type':typ if typ['value'] else _field('brand',refs),
                        'platformCode':_field(platform,refs),
                        'rulesText':_field(summary,refs,'conflict' if conflicts else 'explicit')},brands=brands(items))

    for key,items in numbered.items():
        platform,identifier=key
        primary=items[0]
        refs=material_refs(items,'ABCDEFGHJKLM')
        label=PLATFORM_NAMES[platform]
        typ=projection.user_field('type')
        both=all(item[0]==BOTH for item in items)
        terms=plan_terms(items)
        conflicts=[]
        if both:
            brand_amount=list(dict.fromkeys(text(c,'G') for _,_,c,*_ in items if text(c,'G')))
            merchant=list(dict.fromkeys(text(c,'H') for _,_,c,*_ in items if text(c,'H')))
            rule=build_rule(items,primary)
            rule+='。品牌承担：'+'、'.join(brand_amount)+'元；门店承担：'+'、'.join(merchant)+'元。'
            alt=list(dict.fromkeys(text(c,'J') for _,_,c,*_ in items if text(c,'J') and text(c,'J')!='同上'))
            if len(brand_amount)>1 or len(merchant)>1 or len(terms)>1:
                conflicts=terms or [identifier]
            if alt:
                rule+='运营补充原文另有记录，按原件核对。'
            title=f'{label}二方混补 '+ ('／'.join(terms) if terms else identifier)
        else:
            rule=build_rule(items,primary)
            conflicts=list(dict.fromkeys(x for term in terms for x in _terms(term)))
            title=f'{label}三方混补 '+identifier
        if details_by_key[key]:
            rule+='。关联同档明细：'+'、'.join(n for n,_ in details_by_key[key])
            refs.extend(projection.cell(doc,row['cells'][0]) for _,row in details_by_key[key])
        if conflicts:
            warnings.append({'code':'COUPON_FUNDING_SPLIT_CONFLICT',
                             'message':f'活动 {identifier} 的补贴或档位原文有冲突，请提报人确认。',
                             'locator':primary[1]['locator']})
        if conflicts:
            projection.conflicts.append({'field':'rulesText','manualValue':None,'extractedValues':terms,'evidenceIds':refs})
        id_column='C' if primary[0]==BOTH else 'L'
        platform_column='F' if primary[0]==BOTH else 'C'
        projection.add({'activityName':_field(title,refs),
                        'type':_field('platform',refs) if both else typ,
                        'platformCode':_field(platform,[projection.cell(doc,primary[2][platform_column])]),
                        'sourceActivityId':_field(identifier,[projection.cell(doc,primary[2][id_column])]),
                        'rulesText':_field(rule[:500],refs,'conflict' if conflicts else 'explicit')},brands=brands(items))
    return projection.finish(warnings=warnings)
