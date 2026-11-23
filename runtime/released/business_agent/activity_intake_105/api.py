"""Side-effect-free local material decoding and intake analysis. No model/network/DB."""
import csv
import hashlib
import io
import json
import posixpath
import re
import zipfile
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import PurePosixPath
from xml.etree import ElementTree as ET
from coupon_table import emit as recognize_coupon_table
from group_merge import merge_exact_groups

CONTRACT = 'activity-intake/1.0'
VERSION = '1.0.5'
PARSER = 'local_text_office/1.0.0'
MATCHER = 'unique_identifier/1.0.0'
FIELDS = ('activityName','type','platformCode','sourceActivityId','startAt','endAt','brandIds','rulesText','brandBenefit','remark','companyBearing')
FORM_FIELDS = FIELDS[:-1] + ('manualProductRows',)
DEFAULT_LIMITS = {'maxBytes': 5*1024*1024, 'maxTextChars': 200000, 'maxRows': 1000, 'maxCandidates': 20, 'maxZipEntries': 200, 'maxExpandedBytes': 20*1024*1024, 'maxEntryBytes': 5*1024*1024, 'maxEvidence': 1500}
ALIASES = {'活动名称':'activityName','活动名':'activityName','活动类型':'type','平台':'platformCode','平台活动编号':'sourceActivityId','活动编号':'sourceActivityId','开始时间':'startAt','结束时间':'endAt','品牌ID':'brandIds','优惠机制':'rulesText','品牌利益点':'brandBenefit','备注':'remark','公司承担':'companyBearing','内部商品码':'internalCode','内部码':'internalCode','SKU':'internalCode','UPC':'upc','条码':'upc','商品名称':'name','商品名':'name','规格':'spec','品牌':'brandName'}
ALIASES.update({k:k for k in FIELDS})
ALIASES.update({k:k for k in ('internalCode','upc','name','spec','brandName')})

class IntakeError(ValueError):
    def __init__(self, code, message='Invalid input'):
        self.code, self.message = code, message
        super().__init__(message)

def require(ok, code='INVALID_INPUT', message='Invalid input'):
    if not ok:
        raise IntakeError(code, message)

def limits_for(limits):
    require(limits is None or isinstance(limits, dict), 'INVALID_LIMITS')
    result = DEFAULT_LIMITS.copy()
    for k,v in (limits or {}).items():
        require(k in result and type(v) is int and 0 < v <= result[k], 'INVALID_LIMITS')
        result[k] = v
    return result

def sha(data):
    return hashlib.sha256(data).hexdigest()

def valid_sha(value):
    return isinstance(value,str) and re.fullmatch(r'[a-f0-9]{64}',value) is not None

def issue(code, message, locator=None):
    r = {'code':code,'message':message}
    if locator is not None: r['locator'] = locator
    return r

def xml(data):
    # Decode before scanning so UTF-16 declarations cannot bypass the guard.
    require(b'\x00' not in data and not re.search(br'<!\s*(DOCTYPE|ENTITY)',data,re.I), 'UNSAFE_XML')
    return ET.fromstring(data)

def tag(element):
    return element.tag.split('}')[-1]

def office_archive(data, lim):
    z = zipfile.ZipFile(io.BytesIO(data))
    infos = z.infolist()
    require(len(infos) <= lim['maxZipEntries'], 'LIMIT_EXCEEDED')
    require(len({i.filename for i in infos}) == len(infos), 'UNSAFE_ZIP')
    require(sum(i.file_size for i in infos) <= lim['maxExpandedBytes'], 'LIMIT_EXCEEDED')
    parts = {}
    for i in infos:
        name = i.filename
        require(not (i.flag_bits & 1) and not name.startswith('/') and '\\' not in name and '..' not in PurePosixPath(name).parts and not re.match(r'^[A-Za-z]:',name), 'UNSAFE_ZIP')
        require(i.file_size <= lim['maxEntryBytes'], 'LIMIT_EXCEEDED')
        require((i.external_attr >> 16) & 0o170000 != 0o120000, 'UNSAFE_ZIP')
        require(not re.search(r'(vbaProject|embeddings/|activeX/)',name,re.I), 'UNSUPPORTED_MATERIAL')
        body = z.read(i)  # CRC checked; never extracts to disk
        if name.endswith(('.xml','.rels')):
            root = xml(body)
            if name.endswith('.rels'):
                require(not any(e.attrib.get('TargetMode','').lower() == 'external' or re.match(r'^[a-z]+:',e.attrib.get('Target',''), re.I) for e in root), 'EXTERNAL_RELATIONSHIP')
            if name == '[Content_Types].xml':
                require(b'macroEnabled' not in body and b'vbaProject' not in body, 'UNSUPPORTED_MATERIAL')
        parts[name] = body
    z.close()
    return parts

def decode_material(material, limits=None):
    lim = limits_for(limits)
    require(isinstance(material,dict) and set(material) == {'materialId','filename','mediaType','sha256','contentBytes'})
    require(all(isinstance(material[k],str) and material[k] for k in ('materialId','filename','mediaType')) and valid_sha(material['sha256']))
    data = material['contentBytes']
    require(isinstance(data,bytes) and len(data) <= lim['maxBytes'], 'LIMIT_EXCEEDED')
    require(sha(data) == material['sha256'], 'HASH_MISMATCH')
    ext = material['filename'].rsplit('.',1)[-1].lower()
    out = dict(materialId=material['materialId'],sha256=material['sha256'],status='parsed',kind=ext,textBlocks=[],tableRows=[],errors=[],warnings=[],parserVersion=PARSER)
    def row(values, locator):
        out['tableRows'].append({'locator':locator,'cells':values})
    def cell(column,text,source,loc):
        return {'column':column,'text':text,'sourceType':source,'locator':loc}
    try:
        if ext not in ('txt','md','csv','tsv','xlsx','docx'):
            out['status'] = 'unsupported'
            out['errors'].append(issue('PARSER_UNAVAILABLE' if ext in ('png','jpg','jpeg','webp','pdf','doc','xls') else 'UNSUPPORTED_MATERIAL','此格式未实现本地解析，请补充文字或人工字段'))
            return out
        if ext in ('txt','md','csv','tsv'):
            text = data.decode('utf-8-sig', errors='strict')
            require('\x00' not in text, 'INVALID_ENCODING')
            require(len(text) <= lim['maxTextChars'], 'LIMIT_EXCEEDED')
            if ext in ('txt','md'):
                offset=0
                for n,line in enumerate(text.splitlines(keepends=True),1):
                    body=line.rstrip('\r\n')
                    if body: out['textBlocks'].append({'text':body,'locator':{'kind':'text','line':n,'start':offset,'end':offset+len(body)}})
                    offset += len(line)
            else:
                reader = csv.reader(io.StringIO(text,newline=''),delimiter=',' if ext=='csv' else '\t',strict=True)
                start=1
                for n,values in enumerate(reader,1):
                    if n > lim['maxRows']+1:
                        out['status']='partial';out['warnings'].append(issue('LIMIT_EXCEEDED','未覆盖剩余表格行'));break
                    loc={'kind':'csv','row':n,'lineStart':start,'lineEnd':reader.line_num}
                    row([cell(str(i),v,'string' if v else 'blank',dict(loc,column=i)) for i,v in enumerate(values,1)],loc)
                    start=reader.line_num+1
        else:
            parts=office_archive(data,lim)
            if ext=='docx':
                require('word/document.xml' in parts, 'INVALID_OFFICE')
                root=xml(parts['word/document.xml'])
                body=next(e for e in root.iter() if tag(e)=='body')
                pnum=0;tnum=0
                for e in body:
                    if tag(e)=='p':
                        pnum+=1
                        text=''.join(x.text or '' for x in e.iter() if tag(x)=='t')
                        if text: out['textBlocks'].append({'text':text,'locator':{'kind':'docx','paragraph':pnum}})
                    elif tag(e)=='tbl':
                        tnum+=1
                        for rnum,tr in enumerate((x for x in e if tag(x)=='tr'),1):
                            loc={'kind':'docx','table':tnum,'row':rnum}
                            row([cell(str(cnum), '\n'.join(''.join(t.text or '' for t in p.iter() if tag(t)=='t') for p in tc if tag(p)=='p'),'string',dict(loc,column=cnum)) for cnum,tc in enumerate((x for x in tr if tag(x)=='tc'),1)],loc)
            else:
                require('xl/workbook.xml' in parts and 'xl/_rels/workbook.xml.rels' in parts,'INVALID_OFFICE')
                rels={x.attrib['Id']:x.attrib['Target'] for x in xml(parts['xl/_rels/workbook.xml.rels'])}
                shared=[]
                if 'xl/sharedStrings.xml' in parts:
                    shared=[''.join(x.text or '' for x in e.iter() if tag(x)=='t') for e in xml(parts['xl/sharedStrings.xml'])]
                for sheet in (x for x in xml(parts['xl/workbook.xml']).iter() if tag(x)=='sheet'):
                    rid=next(v for k,v in sheet.attrib.items() if k.endswith('}id'))
                    target=rels[rid]
                    path=target.lstrip('/') if target.startswith('/') else posixpath.normpath('xl/'+target)
                    require(path.startswith('xl/worksheets/') and path in parts,'INVALID_OFFICE')
                    for r in (x for x in xml(parts[path]).iter() if tag(x)=='row'):
                        loc={'kind':'xlsx','sheet':sheet.attrib['name'],'row':int(r.attrib['r'])}
                        values=[]
                        for c in r:
                            if tag(c)!='c': continue
                            ref=c.attrib['r'];ct=c.attrib.get('t','n');v=next((x.text or '' for x in c if tag(x)=='v'),'');f=next((x for x in c if tag(x)=='f'),None)
                            cloc=dict(loc,cell=ref)
                            if f is not None:
                                value='='+ (f.text or '');source='formula';out['warnings'].append(issue('FORMULA_NOT_EVALUATED','公式仅保留原文，未计算',cloc))
                            elif ct=='s': value=shared[int(v)];source='string'
                            elif ct=='inlineStr': value=''.join(x.text or '' for x in c.iter() if tag(x)=='t');source='string'
                            elif ct=='str': value=v;source='string'
                            else: value=v;source='number' if v else 'blank'
                            values.append(cell(re.sub(r'\d','',ref),value,source,cloc))
                        row(values,loc)
            require(sum(len(b['text']) for b in out['textBlocks'])+sum(len(c['text']) for r in out['tableRows'] for c in r['cells']) <= lim['maxTextChars'],'LIMIT_EXCEEDED')
            if len(out['tableRows']) > lim['maxRows']+1:
                out['tableRows']=out['tableRows'][:lim['maxRows']+1];out['status']='partial';out['warnings'].append(issue('LIMIT_EXCEEDED','未覆盖剩余表格行'))
    except IntakeError as e:
        out.update(status='invalid',textBlocks=[],tableRows=[]);out['errors'].append(issue(e.code,e.message))
    except (UnicodeError,ValueError,KeyError,IndexError,StopIteration,ET.ParseError,zipfile.BadZipFile,RuntimeError,csv.Error,NotImplementedError):
        out.update(status='invalid',textBlocks=[],tableRows=[]);out['errors'].append(issue('INVALID_MATERIAL','编码、结构或文件内容不符合受支持格式'))
    return out


def normalize_field(key,value,catalog):
    if value is None or value=='' or value==[]: return None
    if key=='type': return {'品牌活动':'brand','平台活动':'platform','价探招商':'jiatan_sourcing'}.get(value,value) if value in ('brand','platform','jiatan_sourcing','品牌活动','平台活动','价探招商') else None
    if key=='platformCode': return {'美团':'meituan','淘宝闪购':'taobao_instant','淘宝':'taobao_instant','价探':'jiatan'}.get(value,value) if isinstance(value,str) else None
    if key in ('startAt','endAt'):
        if not isinstance(value,str) or not re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?(?:Z|[+-]\d\d:\d\d)',value): return None
        try: datetime.fromisoformat(value.replace('Z','+00:00'));return value
        except ValueError: return None
    if key=='brandIds':
        values=value if isinstance(value,list) else [x.strip() for x in value.split(',')]
        known={b['id'] for b in catalog['brands']}
        return list(dict.fromkeys(values)) if all(v in known for v in values) else None
    if key=='companyBearing':
        if isinstance(value,dict): value=value.get('state')
        return {'是':'yes','有':'yes','无':'no','否':'no','未知':'unknown','yes':'yes','no':'no','unknown':'unknown'}.get(value)
    return value if isinstance(value,str) else None


def analyze_intake(payload, limits=None, *, coupon_partition=None):
    lim=limits_for(limits)
    require(isinstance(payload,dict) and set(payload)=={'contractVersion','inputRevision','inputDigest','manualFields','documents','catalogSnapshot'})
    require(payload['contractVersion']==CONTRACT and type(payload['inputRevision']) is int and payload['inputRevision']>0 and valid_sha(payload['inputDigest']))
    catalog=payload['catalogSnapshot'];require(isinstance(catalog,dict) and set(catalog)=={'revision','brands','products'} and isinstance(catalog['products'],list) and isinstance(catalog['brands'],list))
    require(isinstance(catalog['revision'],str) and bool(catalog['revision']))
    products=catalog['products'];require(len(products)<=10000,'LIMIT_EXCEEDED')
    require(len(catalog['brands'])<=10000,'LIMIT_EXCEEDED')
    require(all(isinstance(b,dict) and set(b)=={'id','name'} and all(isinstance(b[k],str) and b[k] for k in ('id','name')) for b in catalog['brands']))
    brand_ids={b['id'] for b in catalog['brands']}
    require(len(brand_ids)==len(catalog['brands']))
    for p in products:
        require(isinstance(p,dict) and set(p)=={'id','internalCode','upc','name','spec','brandId'})
        require(all(isinstance(p[k],str) and p[k] for k in ('id','name','internalCode')))
        require(all(p[k] is None or isinstance(p[k],str) for k in ('upc','spec')))
        require(p['brandId'] is None or isinstance(p['brandId'],str) and p['brandId'] in brand_ids)
    require(len({p['id'] for p in products})==len(products))
    docs=payload['documents'];require(isinstance(docs,list) and len(docs)<=20,'LIMIT_EXCEEDED')
    doc_keys={'materialId','sha256','status','kind','textBlocks','tableRows','errors','warnings','parserVersion'}
    locator_keys={'kind','line','start','end','lineStart','lineEnd','row','column','sheet','cell','paragraph','table','fieldPath'}
    def check_locator(loc):
        require(isinstance(loc,dict) and 'kind' in loc and set(loc)<=locator_keys)
        require(loc['kind'] in ('text','csv','xlsx','docx','form'))
        for key,value in loc.items():
            require(isinstance(value,str) if key in ('kind','sheet','cell','fieldPath') else type(value) is int and value>=0)
    for d in docs:
        require(isinstance(d,dict) and set(d)==doc_keys and valid_sha(d['sha256']) and all(isinstance(d[k],str) for k in ('materialId','kind','parserVersion')) and d['status'] in ('parsed','partial','unsupported','invalid'))
        require(all(isinstance(d[k],list) for k in ('textBlocks','tableRows','errors','warnings')))
        require(len(d['tableRows'])<=lim['maxRows']+1 and len(d['textBlocks'])<=lim['maxTextChars'],'LIMIT_EXCEEDED')
        chars=0
        for block in d['textBlocks']:
            require(isinstance(block,dict) and set(block)=={'text','locator'} and isinstance(block['text'],str));check_locator(block['locator']);chars+=len(block['text'])
        for row in d['tableRows']:
            require(isinstance(row,dict) and set(row)=={'locator','cells'} and isinstance(row['cells'],list));check_locator(row['locator'])
            for cell in row['cells']:
                require(isinstance(cell,dict) and set(cell)=={'column','text','sourceType','locator'} and isinstance(cell['text'],str) and isinstance(cell['column'],str) and cell['sourceType'] in ('string','number','formula','blank'));check_locator(cell['locator']);chars+=len(cell['text'])
        require(chars<=lim['maxTextChars'],'LIMIT_EXCEEDED')
        for item in d['errors']+d['warnings']:
            require(isinstance(item,dict) and {'code','message'}<=set(item)<={'code','message','locator'} and isinstance(item['code'],str) and isinstance(item['message'],str))
            if 'locator' in item: check_locator(item['locator'])
    out={'contractVersion':CONTRACT,'engine':'deterministic_local_v1','modelUsed':False,'engineVersion':VERSION,'parserVersions':sorted({d['parserVersion'] for d in docs}),'matcherVersion':MATCHER,'promptVersion':None,'inputRevision':payload['inputRevision'],'inputDigest':payload['inputDigest'],'catalogRevision':catalog['revision'],'outcome':'needs_input','candidates':[],'evidence':[],'missingFields':[],'conflicts':[],'materialResults':docs,'warnings':[],'errors':[]}
    def evidence(mid,digest,loc,quote):
        require(len(out['evidence'])<lim['maxEvidence'],'LIMIT_EXCEEDED')
        eid='ev-'+str(len(out['evidence'])+1)
        out['evidence'].append(dict(evidenceId=eid,materialId=mid,sourceSha256=digest,locator=loc,quote=quote[:500]))
        return eid
    groups=[]
    def newgroup():
        g={'values':{},'rows':[]};groups.append(g);return g
    def add(g,key,value,eid):
        normalized=normalize_field(key,value,catalog)
        if normalized is not None: g['values'].setdefault(key,[]).append((normalized,eid))
        else: out['warnings'].append(issue('FIELD_UNRESOLVED','字段无法可靠解析：'+key))
    for d in docs:
        require(isinstance(d,dict) and valid_sha(d.get('sha256')) and isinstance(d.get('materialId'),str) and d.get('status') in ('parsed','partial','unsupported','invalid'))
        out['warnings'].extend(d['warnings']);out['warnings'].extend(d['errors'])
        if d['status'] not in ('parsed','partial'): continue
        g=None; text_product=None
        product_anchors=('internalCode','upc','name')
        def retain_unanchored_attributes(product):
            if product and not any(k in product['cells'] for k in product_anchors):
                first=next(iter(product['cells'].values()))
                out['warnings'].append(issue('PRODUCT_ATTRIBUTES_WITHOUT_ANCHOR','品牌或规格说明已保留，未提供商品标识或商品名，不生成商品行；适用范围待人工核对',first['locator']))
        for block in d['textBlocks']:
            text=block['text'];m=re.match(r'^\s*([^:：]+)[:：]\s*(.+?)\s*$',text)
            if not m: continue
            key=ALIASES.get(m[1].strip());value=m[2]
            if key not in FIELDS and key not in ('internalCode','upc','name','spec','brandName'): continue
            if value.strip().lower() in ('示例','示例行','example','sample','请填写','待填写') or value.startswith(('示例：','示例:','[填写','<填写','（填写')):
                out['warnings'].append(issue('TEMPLATE_EXAMPLE_SKIPPED','模板或示例字段未生成事实',block['locator']));continue
            if key in ('internalCode','upc','name','spec','brandName'):
                if text_product is None or key in text_product['cells']:
                    retain_unanchored_attributes(text_product)
                    text_product={'cells':{},'evidenceIds':[]}
                anchored=any(k in text_product['cells'] for k in product_anchors)
                eid=evidence(d['materialId'],d['sha256'],block['locator'],text)
                text_product['cells'][key]={'text':value,'sourceType':'string','locator':block['locator']}
                text_product['evidenceIds'].append(eid)
                # Attributes alone remain evidence, not a product relation. Buffer
                # them so a later explicit identifier/name can anchor this row.
                if key in product_anchors and not anchored:
                    if g is None: g=newgroup()
                    g['rows'].append(text_product)
                continue
            if key=='activityName' and g and 'activityName' in g['values']:
                retain_unanchored_attributes(text_product)
                g=None; text_product=None
            if g is None: g=newgroup()
            eid=evidence(d['materialId'],d['sha256'],block['locator'],text)
            add(g,key,value,eid)
        retain_unanchored_attributes(text_product)
        headers={};table_identity=None; table_groups={}
        remaining = recognize_coupon_table(d,d['tableRows'],catalog,newgroup,add,evidence,
                                           lambda code,message,locator: out['warnings'].append(issue(code,message,locator)),
                                           partition=coupon_partition)
        for r in remaining:
            identity=(r['locator'].get('sheet'),r['locator'].get('table'))
            if identity!=table_identity: headers={};table_identity=identity;table_groups={}
            # Vertical two-cell DOCX/CSV tables retain direct label/value evidence.
            if len(r['cells'])==2 and ALIASES.get(r['cells'][0]['text'].strip()) in FIELDS and r['cells'][1]['text'].strip() and ALIASES.get(r['cells'][1]['text'].strip()) is None:
                key=ALIASES[r['cells'][0]['text'].strip()];c=r['cells'][1]
                if key=='activityName' and g and 'activityName' in g['values']: g=None
                if g is None: g=newgroup()
                eid=evidence(d['materialId'],d['sha256'],c['locator'],c['text'])
                if c['sourceType']=='string': add(g,key,c['text'],eid)
                else: out['warnings'].append(issue('FIELD_NOT_TEXT','非文本字段未推断：'+key,c['locator']))
                continue
            mapped={c['column']:ALIASES.get(c['text'].strip()) for c in r['cells']}
            if any(k in ('activityName','internalCode','upc','name') for k in mapped.values()):
                headers={col:k for col,k in mapped.items() if k};continue
            cells={headers[c['column']]:c for c in r['cells'] if c['column'] in headers and c['text'].strip()}
            if not cells: continue
            if any(c['text'].strip().lower() in ('示例','示例行','example','sample','活动名称示例') or c['text'].startswith('示例：') for c in cells.values()):
                out['warnings'].append(issue('TEMPLATE_EXAMPLE_SKIPPED','示例行未生成活动',r['locator']));continue
            if 'activityName' in cells:
                signature=tuple((k,cells[k]['text']) for k in FIELDS if k in cells)
                if signature not in table_groups: table_groups[signature]=newgroup()
                g=table_groups[signature]
            if g is None: g=newgroup()
            ids=[]
            for k,c in cells.items():
                eid=evidence(d['materialId'],d['sha256'],c['locator'],c['text']);ids.append(eid)
                if k in FIELDS:
                    if c['sourceType'] in ('number','formula'):
                        out['warnings'].append(issue('FIELD_NOT_TEXT','非文本字段未推断：'+k,c['locator']))
                    else: add(g,k,c['text'],eid)
            if any(k in cells for k in ('internalCode','upc','name')):
                g['rows'].append({'cells':cells,'evidenceIds':ids})
    groups=merge_exact_groups(groups,docs,out['evidence'])
    manual=payload['manualFields'];manualvals={};manualrows=[];meids={}
    require(isinstance(manual,dict))
    if manual:
        require(isinstance(manual,dict) and set(manual)=={'materialId','sha256','fields'} and isinstance(manual['materialId'],str) and valid_sha(manual['sha256']) and isinstance(manual['fields'],dict))
        require(set(manual['fields']) <= set(FORM_FIELDS)|{'companyBearing'})
        require(re.fullmatch(r'FORM-.+-r'+str(payload['inputRevision']),manual['materialId']) is not None)
        # Validate raw values before normalization; hash binds the unchanged snapshot.
        for key,val in manual['fields'].items():
            if val is None: continue
            if key=='companyBearing':
                require(isinstance(val,dict) and set(val)=={'state'} and val['state'] in ('yes','no','unknown'))
            elif key=='brandIds':
                require(isinstance(val,list) and len(val)<=20 and all(isinstance(v,str) and v in brand_ids for v in val) and len(set(val))==len(val))
            elif key=='manualProductRows':
                require(isinstance(val,list) and len(val)<=lim['maxRows'],'LIMIT_EXCEEDED')
                ids=set()
                for mr in val:
                    require(isinstance(mr,dict) and {'rowId'}<=set(mr)<={'rowId','productId','rawInternalCode','rawUpc','rawName'})
                    require(isinstance(mr['rowId'],str) and re.fullmatch(r'manual-[a-fA-F0-9]{8}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{12}',mr['rowId']) is not None and mr['rowId'] not in ids)
                    ids.add(mr['rowId'])
                    require(all(v is None or isinstance(v,str) for k,v in mr.items() if k!='rowId'))
                    if mr.get('productId') is not None: require(any(p['id']==mr['productId'] for p in products),'UNKNOWN_PRODUCT')
            else:
                maximum={'activityName':200,'sourceActivityId':200,'rulesText':10000,'brandBenefit':5000,'remark':5000}.get(key,200)
                require(isinstance(val,str) and len(val)<=maximum)
                if key=='type': require(val in ('brand','platform','jiatan_sourcing'))
                if key=='platformCode': require(val in ('meituan','taobao_instant','jiatan'))
        # Hash binds exact canonical FORM snapshot, not a caller-invented quote.
        require(sha(json.dumps(manual['fields'],ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode())==manual['sha256'],'FORM_HASH_MISMATCH')
        for key,val in manual['fields'].items():
            if key=='manualProductRows': manualrows=val or [];continue
            v=normalize_field(key,val,catalog)
            if v is not None:
                manualvals[key]=v
                meids[key]=evidence(manual['materialId'],manual['sha256'],{'kind':'form','fieldPath':key},json.dumps(val,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False) if not isinstance(val,str) else val)
        require(isinstance(manualrows,list) and len(manualrows)<=lim['maxRows'],'LIMIT_EXCEEDED')
        if not groups and (manualvals or manualrows): newgroup()
    if len(groups)>lim['maxCandidates']:
        groups=groups[:lim['maxCandidates']];out['warnings'].append(issue('LIMIT_EXCEEDED','未覆盖剩余活动组'))
    totalrows=0
    evidence_by_id={item['evidenceId']:item for item in out['evidence']}
    for index,g in enumerate(groups,1):
        fields={};rowout=[]
        # Manual snapshot belongs to one candidate; never blindly copy one form across multiple activities.
        mv=manualvals if len(groups)==1 else {}
        if manualvals and len(groups)>1: out['warnings'].append(issue('MANUAL_GROUP_AMBIGUOUS','多活动时人工表单未自动分配，请逐组核对'))
        for key in FIELDS:
            values=g['values'].get(key,[]);unique=[]
            for value,_ in values:
                if value not in unique: unique.append(value)
            eids=list(dict.fromkeys(e for _,e in values))
            value=unique[0] if len(unique)==1 else None;status='explicit' if unique else 'missing'
            if key in mv:
                value=mv[key];eids.append(meids[key]);status='explicit'
            if len(unique)>1 or (key in mv and any(x!=mv[key] for x in unique)):
                status='conflict'
                if key!='brandIds' or 'brandDivergence' not in g:
                    out['conflicts'].append(dict(field=key,manualValue=mv.get(key),extractedValues=unique,evidenceIds=eids))
            if key=='companyBearing': value={'state':value or 'unknown','evidenceIds':eids}
            fields[key]=dict(value=value,status=status,evidenceIds=eids)
        if 'brandDivergence' in g:
            divergence=g['brandDivergence']
            manual_evidence=[meids['brandIds']] if 'brandIds' in mv else []
            out['conflicts'].append(dict(field='brandIds',manualValue=mv.get('brandIds'),
                extractedValues=divergence['extractedValues'],evidenceIds=divergence['evidenceIds']+manual_evidence))
            location=evidence_by_id[divergence['warningEvidenceId']]['locator']
            reason='完整品牌名单有差异' if divergence['completeScope'] else '材料所列品牌范围不明确'
            out['warnings'].append(issue('COUPON_BRAND_MAPPING_INCOMPLETE',reason+'，已保留共同品牌及待核对来源',location))
        if fields['startAt']['value'] and fields['endAt']['value'] and datetime.fromisoformat(fields['startAt']['value'].replace('Z','+00:00'))>datetime.fromisoformat(fields['endAt']['value'].replace('Z','+00:00')):
            fields['startAt']['status']=fields['endAt']['status']='conflict';out['warnings'].append(issue('DATE_RANGE_CONFLICT','开始晚于结束，须人工核对'))
        rows=g['rows'][:]
        if len(groups)==1:
            for mr in manualrows:
                require(isinstance(mr,dict) and isinstance(mr.get('rowId'),str) and set(mr)<={'rowId','productId','rawInternalCode','rawUpc','rawName'} and all(v is None or isinstance(v,str) for v in mr.values()))
                eid=evidence(manual['materialId'],manual['sha256'],{'kind':'form','fieldPath':'manualProductRows.'+mr['rowId']},json.dumps(mr,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False))
                rows.append({'manual':mr,'evidenceIds':[eid]})
        for r in rows:
            if totalrows>=lim['maxRows']:
                out['warnings'].append(issue('LIMIT_EXCEEDED','未覆盖剩余商品行'));break
            totalrows+=1
            rowout.append(match_row(r,products,catalog['brands'],out['warnings'],totalrows))
        if g['values'] or rowout or mv:
            out['candidates'].append(dict(candidateId='candidate-'+str(index),fields=fields,productRows=rowout))
        if not fields['activityName']['value']:
            out['missingFields'].append({'field':'activityName','reasonCode':'EXPLICIT_NAME_REQUIRED'})
        for key in FIELDS:
            if fields[key]['status']=='missing':
                item={'field':key,'reasonCode':'NOT_PROVIDED'}
                if item not in out['missingFields']: out['missingFields'].append(item)
    if any(c['fields']['activityName']['value'] for c in out['candidates']): out['outcome']='needs_confirmation'
    if not groups: out['missingFields'].append({'field':'activityName','reasonCode':'EXPLICIT_NAME_REQUIRED'})
    return out


def match_row(row,products,brands,warnings,index):
    cells=row.get('cells',{});mr=row.get('manual',{})
    def val(k): return cells.get(k,{}).get('text') or mr.get({'internalCode':'rawInternalCode','upc':'rawUpc','name':'rawName'}.get(k,k))
    code,upc,name=val('internalCode'),val('upc'),val('name')
    require(all(v is None or isinstance(v,str) for v in (code,upc,name)))
    result=dict(rowId=mr.get('rowId') or 'row-'+str(index),rawInternalCode=code,rawUpc=upc,rawName=name,matchKind='unmatched',matchedProductId=None,candidates=[],evidenceIds=row['evidenceIds'])
    unreliable=False
    for k in ('internalCode','upc'):
        if k in cells and cells[k]['sourceType']!='string':
            unreliable=True;warnings.append(issue('IDENTIFIER_CELL_NOT_TEXT','非文本标识不能用于精确匹配',cells[k]['locator']))
    if upc and (not isinstance(upc,str) or not re.fullmatch(r'\d{8,14}',upc)):
        unreliable=True;warnings.append(issue('INVALID_UPC','条码必须为8至14位文本数字，未转换或补零'))
    exact=[];conflict=False
    for k,v in (('internalCode',code),('upc',upc)):
        if not v: continue
        matches=[p for p in products if p.get(k)==v] if isinstance(v,str) else []
        if len(matches)>1: conflict=True
        if matches: exact+=matches
    unique={p['id']:p for p in exact}
    if len(unique)>1: conflict=True
    for p in unique.values():
        for k,v in (('internalCode',code),('upc',upc),('name',name),('spec',val('spec'))):
            if v and p.get(k) and re.sub(r'\s','',str(v)).casefold()!=re.sub(r'\s','',p[k]).casefold(): conflict=True
        brand=val('brandName')
        if brand and not any(b['id']==p.get('brandId') and b.get('name')==brand for b in brands): conflict=True
    selected=mr.get('productId')
    if selected:
        require(any(p['id']==selected for p in products),'UNKNOWN_PRODUCT')
        result.update(matchKind='manual_selected',matchedProductId=selected,candidates=[{'productId':selected,'reasons':['人工选择目录商品；不冒充自动匹配']}]);return result
    if unique and not conflict and not unreliable:
        pid=next(iter(unique));result.update(matchKind='exact',matchedProductId=pid,candidates=[{'productId':pid,'reasons':['唯一可靠标识且无显式矛盾']}]);return result
    candidates=[{'productId':p['id'],'reasons':['标识冲突，须人工确认' if conflict else '标识不可靠，须人工确认']} for p in unique.values()]
    if not candidates and isinstance(name,str):
        candidates=[{'productId':p['id'],'reasons':['商品名称近似，仅为候选']} for p in products if isinstance(p.get('name'),str) and SequenceMatcher(None,name.casefold(),p['name'].casefold()).ratio()>=0.6][:10]
    result.update(matchKind='conflict' if conflict else 'candidate' if candidates else 'unmatched',candidates=candidates[:10])
    return result
