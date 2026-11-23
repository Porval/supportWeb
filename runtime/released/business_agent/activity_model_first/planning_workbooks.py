"""Interpret explicit coupon-plan columns and the combined brand campaign workbook."""

from collections import Counter, defaultdict
import re
from unicodedata import normalize

from .coupon_workbook import UPC, _field
from .structured_evidence import StructuredProjection

TIER = re.compile(r'(\d+)\s*[-－减]\s*(\d+)')
ID = re.compile(r'(?<!\d)\d{8,20}(?!\d)')
PROYA_KEYS = {'品牌','产品条码','产品名称','品牌券【同享券】','联合券【同享券】','混补【专享券】'}
DIRECT_KEYS = {'商家upc','商家报名价','优惠券','消费者到手价','直营活动ID','代理活动ID','活动名称'}
MIX_KEYS = {'方案','活动','机制名称','机制范围','机制类型','优惠力度','覆盖渠道'}


def values(row):
    return {cell['column']:cell for cell in row['cells'] if cell['text'].strip()}


def labels(row):
    return {cell['text'].strip():cell['column'] for cell in row['cells'] if cell['text'].strip()}


def text(cells, column):
    return cells.get(column,{}).get('text','').strip()


def _sheet_rows(documents):
    if len(documents)!=1 or documents[0]['kind']!='xlsx' or documents[0]['status']!='parsed':
        return None, {}
    doc, = documents
    sheets=defaultdict(list)
    for row in doc['tableRows']:
        if values(row):
            sheets[row['locator'].get('sheet')].append(row)
    return doc,sheets


def plan_type(documents):
    doc,sheets=_sheet_rows(documents)
    if len(sheets)!=2:
        return None
    for name, rows in sheets.items():
        h=set(labels(rows[0]))
        if PROYA_KEYS<=h and '券生效时间说明' in sheets and len(rows)>=3:
            dates=sheets['券生效时间说明']
            if {'券类型','机制','生效时间'}<=set(labels(dates[0])):
                return 'matrix'
    first=next((rows for name,rows in sheets.items() if '美团神价' in name),None)
    second=next((rows for name,rows in sheets.items() if '美团混补' in name),None)
    if first and len(first)>3 and DIRECT_KEYS<=set(labels(first[2])) and second and MIX_KEYS<=set(labels(second[0])):
        return 'combined'
    return None


def _numbers(text_value):
    match = TIER.search(normalize('NFKC',text_value))
    return tuple(map(int,match.groups())) if match else None


def _rule_cells(header, tiers, date_rows):
    current = None
    by_tier = defaultdict(list)
    categories = []
    for row in date_rows[1:]:
        cells=values(row)
        if 'A' in cells:
            current=cells['A']
        key=_numbers(text(cells,'B'))
        if not current or not key or not text(cells,'C'):
            return None
        by_tier[key].append((current,cells['B'],cells['C']))
    for column,cell in tiers.items():
        label = header.get(column)
        if label:
            name = label['text'].strip()
            if '券' not in name:
                return None
            categories.append((name,[]))
        key=_numbers(cell['text'])
        if not key or not categories or len(by_tier[key])!=1:
            return None
        categories[-1][1].append((column,cell,by_tier[key][0]))
    return categories


def _matrix(payload,result,metadata,lookup,doc,sheets):
    rows=next(rows for name,rows in sheets.items() if name!='券生效时间说明')
    header, tiers, *details=rows
    labels_map=labels(header)
    raw_columns=values(tiers)
    if not raw_columns or labels_map['品牌'] in raw_columns:
        return result,metadata
    categories=_rule_cells(values(header),raw_columns,sheets['券生效时间说明'])
    if not categories:
        return result,metadata
    projection=StructuredProjection(payload,result,metadata,lookup)
    form=projection.form
    if not form.get('activityName') or form.get('type') not in (None,'brand') or form.get('sourceActivityId'):
        return result,metadata
    if form.get('platformCode') not in (None,'meituan'):
        return result,metadata
    brand_refs,products={},[]
    tier_columns=set(raw_columns)
    marked_values={'✓','√','是','参加'}
    for row in details:
        cells=values(row)
        brand,barcode,title=(cells.get(labels_map[key]) for key in ('品牌','产品条码','产品名称'))
        if not (brand and barcode and title and UPC.fullmatch(barcode['text'].strip())):
            return result,metadata
        if any(cells[col]['text'].strip() not in marked_values for col in tier_columns & cells.keys()):
            return result,metadata
        label=brand['text'].strip()
        brand_refs.setdefault(label,projection.cell(doc,brand))
        related=(brand,*(cells[col] for col in raw_columns if col in cells))
        product=projection.product(doc,row,barcode,title,related=related)
        if product is None:
            return result,metadata
        products.append(product)
    name=projection.user_field('activityName')
    manual_period=[projection.user_field(key) for key in ('startAt','endAt')]
    confirmed_dates=all(field['value'] for field in manual_period)
    date_values = list(dict.fromkeys(date['text'].strip() for _,tiers in categories for _,_,(_,_,date) in tiers))
    summaries=[]
    refs=[]
    for title, tiers in categories:
        variants=[]
        for column,cell,(_,date_tier,date) in tiers:
            rule=normalize('NFKC',cell['text']).strip()
            duration=date['text'].strip()
            if '整月' not in duration:
                rule += f'（{duration}）'
            variants.append(rule)
            refs.extend(projection.cell(doc,c) for c in (cell,date_tier,date) if c)
        summaries.append(f'{title} {len(variants)} 档：'+'、'.join(variants))
    schedule = "活动时间按已确认的日期，原说明表的月份差异见来源。" if confirmed_dates else (
        '说明表生效时间：'+'、'.join(date_values)+'。月份与提报名称不一致时待确认。')
    summary = f'整份规划共 {len(categories)} 类 {len(raw_columns)} 档。'+"\n"+"\n".join(summaries)+f'\n{schedule}原表勾选为品牌方规划，实际参加商品待确认。'
    header_refs=[projection.cell(doc,cell) for cell in header['cells'] if cell['text'].strip() in PROYA_KEYS]
    sheet_ref=next((projection.cell(doc,cell) for row in sheets['券生效时间说明'][:3] for cell in row['cells'] if '美垂仓' in cell['text']),None)
    if sheet_ref is None:
        return result,metadata
    parent_labels=[brand for brand in brand_refs if brand in form['activityName']]
    if len(parent_labels)==1 and len(brand_refs)>1:
        summary+='\n原表品牌：'+'、'.join(brand_refs)+'。'
        brand_summary=_field(parent_labels,[brand_refs[parent_labels[0]],*name['evidenceIds']])
        refs.extend(brand_refs.values())
    else:
        brand_summary=_field(list(brand_refs),list(brand_refs.values()))
    fields={'activityName':name,'type':_field('brand',name['evidenceIds']),
            'platformCode':_field('meituan',[sheet_ref]),'brandIds':projection.user_field('brandIds'),
            'startAt':manual_period[0],'endAt':manual_period[1],
            'rulesText':_field(summary,[*header_refs,*refs,*(eid for value in manual_period for eid in value['evidenceIds'])])}
    projection.add(fields,products,brands=brand_summary)
    return projection.finish()


def _combined(payload,result,metadata,lookup,doc,sheets):
    first=next(rows for name,rows in sheets.items() if '美团神价' in name)
    mix=next(rows for name,rows in sheets.items() if '美团混补' in name)
    sj_header=labels(first[2]); combo_header=labels(mix[0])
    projection=StructuredProjection(payload,result,metadata,lookup)
    form=projection.form
    if form.get('type') not in (None,'brand') or form.get('sourceActivityId'):
        return result,metadata
    if not form.get('activityName') or form.get('platformCode') not in (None,'meituan'):
        return result,metadata
    direct_refs, totals, counts, brand_refs = [], Counter(), defaultdict(Counter), {}
    products=[]
    for row in first[3:]:
        cells=values(row)
        def get(label):
            return cells.get(sj_header.get(label))
        upc,identifier=get('商家upc'),get('直营活动ID')
        title=next((cell for label,col in sj_header.items() if label.startswith('子活动名称') and (cell:=cells.get(col))),None)
        if not upc or not title or not identifier or not UPC.fullmatch(upc['text']) or not ID.fullmatch(identifier['text']):
            return result,metadata
        cid=identifier['text']
        cell=get('优惠券')
        parsed=_numbers(cell['text']) if cell else None
        bracket=re.match(r'^【(\d+)[-－](\d+)】',title['text'])
        original=tuple(map(int,bracket.groups())) if bracket else None
        if parsed and original and parsed!=original:
            return result,metadata
        if parsed or original:
            counts[cid][parsed or original]+=1
        totals[cid]+=1
        direct_refs.extend(projection.cell(doc,c) for c in (identifier,title,get('活动名称'),cell) if c)
        related=tuple(c for c in (identifier,cell,get('商家报名价'),get('消费者到手价')) if c)
        item=projection.product(doc,row,upc,title,related=related)
        if item is None:
            return result,metadata
        products.append(item)
    for item in metadata['candidateMetadata']:
        for brand in (item['brandNames']['value'] or []):
            if brand in form['activityName']:
                brand_refs.setdefault(brand,projection.user_field('activityName')['evidenceIds'][0])
    # Use the submitted brand name only where the workbook also repeats it as a product prefix.
    if not brand_refs:
        common=set()
        for row in first[3:]:
            for cell in row['cells']:
                m=re.match(r'^【\d+-\d+】([^\s【】0-9]{2,8})(?:Colgate|\s)',cell['text'])
                if m and m[1] in form['activityName']:
                    common.add(m[1])
        brand_refs = {brand:projection.user_field('activityName')['evidenceIds'][0] for brand in sorted(common)}
    if not brand_refs or not totals:
        return result,metadata
    refs=projection.user_field('activityName')['evidenceIds']
    brand=_field(list(brand_refs),list(brand_refs.values()))
    start=next(cell for cell in first[0]['cells'] if '直营' in cell['text'])
    id_summary='、'.join(f'{cid}（{total} 条商品）' for cid,total in totals.items())
    tier_summary='；'.join(f'{cid} 含 '+ '、'.join(f'满{a}减{b}' for a,b in counters)
                           for cid,counters in counts.items() if counters)
    direct_rule=f'美团神价，直营活动 {id_summary}。按商品保留报名价、券档和消费者到手价；代理范围跳过。'
    if tier_summary:
        direct_rule+='\n'+tier_summary
    projection.add({'activityName':_field(form['activityName']+' · 神价',refs),
                    'type':_field('brand',refs),'platformCode':_field('meituan',refs),
                    'rulesText':_field(direct_rule,[projection.cell(doc,start),*direct_refs])},
                   products,brands=brand,method=_field('shen_jia',direct_refs[:1]))
    plans, strengths, details=[],set(),[]
    for row in mix[1:]:
        cells=values(row)
        scheme=cells.get(combo_header['方案'])
        if scheme and scheme['text'].strip()!='方案':
            plans.extend(ID.findall(scheme['text']))
            details.append(projection.cell(doc,scheme))
        c=cells.get(combo_header['机制名称']); offer=cells.get(combo_header['优惠力度'])
        if c and offer and c['text'].strip()!='机制名称':
            strengths.add(offer['text'].strip())
            details.extend(projection.cell(doc,cell) for cell in (c,offer))
    if not details or not strengths:
        return result,metadata
    planned='、'.join(dict.fromkeys(plans))
    summary=f'美团平台混补／联合券。原表共 {len(strengths)} 种优惠力度，实际参加的方案和券档由运营确认。'
    if planned:
        summary+=f'\n方案编号：{planned}。'
    projection.add({'activityName':_field(form['activityName']+' · 混补／联合券',refs),
                    'type':_field('platform',details[:1]),'platformCode':_field('meituan',refs),
                    'rulesText':_field(summary,details)}, brands=brand)
    return projection.finish()


def reconcile_plans(payload,result,metadata,lookup):
    shape=plan_type(payload['documents'])
    if shape is None or not result.get('modelUsed'):
        return result,metadata
    doc,sheets=_sheet_rows(payload['documents'])
    return (_matrix if shape=='matrix' else _combined)(payload,result,metadata,lookup,doc,sheets)
