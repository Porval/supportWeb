"""brand-coupon-review/2.2: group coupon options by brand within one submission."""
import json
import re
from collections import defaultdict
from decimal import Decimal
from functools import lru_cache
from pathlib import Path

POLICY = ('同次提报中属于同一场品牌满减活动的券档归为一场。独立活动按身份分开；各平台、券档、有效期、出资方、'
          '平台券编号和对应商品各自保留，审核人逐档勾选参加。原表商品标记不等于审核选择或平台报名。'
          '满减活动的优惠说明首行概括可审核券数和适用平台，随后每张券单列一行，同面额跨平台分别计数。'
          '商品行是券的适用范围，按商品行统计时说明去重口径，参选标记不计作券档。'
          '同一行并列多个平台时，每档券引用本平台栏组中的勾号、条码和商品名；同条码在多个原表行出现时分别保留。'
          '只对有单品调价依据的活动判断是否拆场；同场商品价格不同也不自动逐商品拆分。'
          '同品牌满减券明细中的“爆好价”若只有参选勾号，不构成整场单品调价依据。'
          '依据券档表头、出资说明和表单确认活动方式。多品牌通用平台券不能按其中一个商品品牌归组。')
COUPON = re.compile(r'满减|满\s*\d+(?:\.\d+)?\s*(?:元)?\s*减\s*\d|品牌券|混补|联合券|品类券|满额券')
DIRECT = re.compile(r'神价|爆好价|单品(?:补贴|调价)|单价调整|直降|逐品调价')
MULTI = re.compile(r'通用品牌联合券|多品牌|跨品牌')
MISSING = {'value': None, 'status': 'missing', 'evidenceIds': []}
LABELS = {'platformCode':'平台','sourceActivityId':'活动编号','startAt':'开始','endAt':'结束'}
PLATFORMS = {'meituan':'美团', 'taobao_instant':'淘宝闪购', 'jd':'京东'}
CHECKS = {'✅','✓','√','✔','Y','是','参加'}


def unique(items):
    return list(dict.fromkeys(items))


def explicit(value, refs):
    return {'value':value, 'status':'explicit', 'evidenceIds':unique(refs)}


def values(fields):
    return [f for f in fields if f['status']=='explicit' and f['value'] is not None]


def combined(fields):
    """Return a shared field only when its populated values agree."""
    conflicts = [f for f in fields if f['status']=='conflict']
    known = values(fields)
    refs = unique(e for f in fields for e in f['evidenceIds'])
    if conflicts:
        return {'value':None, 'status':'conflict', 'evidenceIds':refs}
    if not known:
        return fields[0]
    if all(f['value']==known[0]['value'] for f in known):
        return explicit(known[0]['value'], refs)
    return None


def coupon_selection_sheets(docs):
    """Identify coupon matrices from their headers and the values below each option."""
    matrices = {}
    identity_headers = {'提报平台', '商品名称', '商品条码(upc)', '商品条码', 'upc'}
    tier = re.compile(r'(?:满\s*)?\d+(?:\.\d+)?\s*元?\s*[-－减]\s*\d+')
    for doc in docs:
        if doc['kind'] != 'xlsx':
            continue
        sheets = defaultdict(list)
        for row in doc.get('tableRows', []):
            sheets[row['locator'].get('sheet')].append(row)
        for sheet, rows in sheets.items():
            header, *items = rows
            labels = {c['column']: ''.join(c['text'].split()).lower() for c in header['cells']}
            if not {'提报平台', '商品名称'} <= set(labels.values()) or not any(
                    name in ('商品条码(upc)', '商品条码', 'upc') for name in labels.values()):
                continue
            options = {col: label for col, label in labels.items() if label not in identity_headers}
            if not items or any(not tier.search(label) and label != '爆好价' for label in options.values()):
                continue
            marks = [c['text'].strip().replace('\ufe0f', '').upper()
                     for row in items for c in row['cells'] if c['column'] in options]
            if not any(m in CHECKS for m in marks) or any(m and m not in CHECKS for m in marks):
                continue
            triple = {c['locator']['cell']: c['text'] for c in header['cells']
                      if re.search(r'(?:三|3)方(?:混补|共补)', labels[c['column']])
                      and tier.search(labels[c['column']])}
            if triple:
                flags = {c['locator']['cell']: c['text'] for c in header['cells']
                         if labels[c['column']] == '爆好价'}
                matrices[(doc['materialId'], sheet)] = (doc['sha256'], triple, flags)
    return matrices


def apply_coupon_method(group, detail, matrices, evidence, confirmed=False):
    if not confirmed and group['fields']['type']['value'] != 'brand':
        return False
    method = detail['activityMethod']
    if method['status'] == 'conflict':
        return False
    scoped = sheet_names(group, evidence)
    if len(scoped) != 1 or not scoped <= matrices.keys():
        return False
    mid, sheet = next(iter(scoped))
    digest, tiers, flags = matrices[mid, sheet]
    refs = {key: ref for key, ref in evidence.items() if ref['materialId'] == mid
            and ref['sourceSha256'] == digest and ref['locator'].get('sheet') == sheet}
    if not confirmed and method['value'] == 'bao_hao_jia':
        if not method['evidenceIds'] or not all(
                (ref := refs.get(key)) and flags.get(ref['locator'].get('cell')) == ref['quote']
                for key in method['evidenceIds']):
            return False
    elif not confirmed and method['value'] not in (None, 'brand_threshold_subsidy_triple'):
        return False
    scoped_rules = set(group['fields']['rulesText']['evidenceIds'])
    tier_refs = [key for key, ref in refs.items() if (confirmed or key in scoped_rules)
                 and tiers.get(ref['locator'].get('cell')) == ref['quote']]
    if not tier_refs:
        return False
    detail['activityMethod'] = explicit('brand_threshold_subsidy_triple', tier_refs)
    return True


def brand_for(group, meta, known_source, coupon_matrix=False):
    f = group['fields']
    brands = meta['brandNames']
    if known_source:
        return '自由点'
    ids=f['brandIds']
    if brands['status']=='conflict' or ids['status']=='conflict' or len(brands['value'] or [])>1 or len(ids['value'] or [])>1:
        return None
    names = brands['value'] if brands['status']=='explicit' else []
    brand = names[0].strip() if names else ''
    identity = ('id',ids['value'][0]) if ids['status']=='explicit' and ids['value'] else brand
    if not identity:
        return None
    method = meta['activityMethod']['value'] or ''
    title = f['activityName']['value'] or ''
    terms = f['rulesText']['value'] or ''
    if f['type']['status']=='conflict' or method in ('shen_jia','bao_hao_jia') or (not coupon_matrix and DIRECT.search(title+' '+terms)) or MULTI.search(title+' '+terms):
        return None
    if method.startswith('brand_threshold_subsidy_'):
        return identity
    if not COUPON.search(title+' '+terms):
        return None
    # The brand of a product in a platform-wide offer does not establish ownership.
    if f['type']['value']=='brand' or (brand and brand in title):
        return identity
    products = group['productRows']
    if brand and products and all(brand in (p['rawName'] or '') for p in products):
        return identity
    return None


def merged_products(groups):
    """Keep original source rows, folding shared memberships only for the same identity."""
    by_source = {}
    used = set()
    for g in groups:
        for item in g['productRows']:
            scope=unique(e for k in ('sourceActivityId','startAt','endAt') for e in g['fields'][k]['evidenceIds'])
            item=dict(item, evidenceIds=unique([*item['evidenceIds'],*scope]))
            key = tuple(sorted(item['sourceRowIds']))
            prior = by_source.get(key)
            if prior is not None:
                identity = ('rawUpc','rawInternalCode','matchedProductId')
                if any(prior[k] and item[k] and prior[k]!=item[k] for k in identity):
                    return None
                if prior['rawName'] and item['rawName'] and prior['rawName']!=item['rawName']:
                    return None
                if prior['matchKind'] != item['matchKind'] or prior['candidates']!=item['candidates']:
                    return None
                for k in ('rawName','rawUpc','rawInternalCode','rawSpec','matchedProductId'):
                    prior[k] = prior[k] or item[k]
                prior['evidenceIds'] = unique([*prior['evidenceIds'],*item['evidenceIds']])
            elif used.intersection(key):
                return None
            else:
                by_source[key] = dict(item)
                used.update(key)
    return sorted(by_source.values(),key=lambda row:min(int(s[1:]) for s in row['sourceRowIds']))


def company_field(fields):
    refs = unique(e for f in fields for e in f['evidenceIds'])
    states = {f['value']['state'] for f in fields}
    if any(f['status']=='conflict' for f in fields):
        return {'value':{'state':'unknown','evidenceIds':refs},'status':'conflict','evidenceIds':refs}
    state = 'yes' if 'yes' in states else 'no' if states=={'no'} else 'unknown'
    if state=='unknown':
        return dict(MISSING, value={'state':'unknown','evidenceIds':[]})
    return explicit({'state':state,'evidenceIds':refs}, refs)


def merged_group(groups, metadata, brand):
    fields = {}
    summaries, rule_refs = [], []
    for g in groups:
        f = g['fields']
        rule = f['rulesText']
        parts = []
        for name, label in LABELS.items():
            fact = f[name]
            if fact['status']=='explicit' and fact['value']:
                parts.append(label+' '+PLATFORMS.get(fact['value'],fact['value']))
                rule_refs.extend(fact['evidenceIds'])
        bearing=f['companyBearing']
        if bearing['status']=='explicit' and bearing['value']['state'] in ('yes','no'):
            parts.append('公司出资 '+{'yes':'是','no':'否'}[bearing['value']['state']])
            rule_refs.extend(bearing['evidenceIds'])
        if f['activityName']['value']:
            parts.insert(0,f['activityName']['value'])
            rule_refs.extend(f['activityName']['evidenceIds'])
        if rule['value']:
            parts.append(rule['value'])
            rule_refs.extend(rule['evidenceIds'])
        if parts:
            summaries.append(' · '.join(parts))
    priced_sections = [g for g in groups if g['fields']['rulesText']['status']=='explicit']
    partial_scopes = {k for k in LABELS if len(priced_sections)>1 and
                      {g['fields'][k]['status'] for g in priced_sections}=={'explicit','missing'}}
    for name in groups[0]['fields']:
        facts = [g['fields'][name] for g in groups]
        shared = combined(facts)
        if name=='companyBearing':
            fields[name]=company_field(facts)
        elif name=='rulesText' and (partial_scopes or len(set(f['value'] for f in values(facts)))>1 or
                any(combined([g['fields'][k] for g in groups]) is None for k in (*LABELS,'activityName','companyBearing'))):
            if any(f['status']=='conflict' for f in facts):
                return None
            fields[name] = explicit('\n'.join(unique(summaries)),rule_refs)
        elif name in partial_scopes:
            fields[name]=dict(MISSING)
        elif shared is not None:
            fields[name] = shared
        elif name in ('startAt','endAt') and len(values(facts))==len(facts):
            from datetime import datetime
            try:
                bound=(min if name=='startAt' else max)(facts,key=lambda f:datetime.fromisoformat(f['value']))
                fields[name]=explicit(bound['value'],[e for f in facts for e in f['evidenceIds']])
            except (TypeError,ValueError):
                fields[name]=dict(MISSING)
        elif name in LABELS:
            fields[name] = dict(MISSING)
        elif name=='activityName':
            fields[name] = (explicit(brand+'满减券', [e for m in metadata for e in m['brandNames']['evidenceIds']])
                            if brand else dict(MISSING))
        elif name in ('remark','brandBenefit') and all(isinstance(f['value'],str) for f in values(facts)):
            fields[name] = explicit('\n'.join(unique(f['value'] for f in values(facts))),[e for f in facts for e in f['evidenceIds']])
        elif name=='type' and set(f['value'] for f in values(facts))<={'brand','platform'}:
            brand_type = [f for f in facts if f['value']=='brand']
            fields[name] = combined(brand_type)
        else:
            # Financial conflicts and different confirmed company brand IDs are not guessed.
            return None
    products = merged_products(groups)
    if products is None:
        return None
    brand_refs = unique(e for m in metadata for e in m['brandNames']['evidenceIds'])
    details = dict(metadata[0], brandNames=explicit([brand],brand_refs) if brand and brand_refs else metadata[0]['brandNames'],
                   activityMethod=combined([m['activityMethod'] for m in metadata]) or dict(MISSING))
    if any(len(f['evidenceIds'])>1500 for f in [*fields.values(),details['brandNames'],details['activityMethod'],*products]):
        return None
    return dict(groups[0], fields=fields, productRows=products),details


def preserve_review_schedule(groups, parent, payload):
    """Preserve the workbench's structured coupon schedule when regrouping dated tiers."""
    rules=[g['fields']['rulesText'] for g in groups]
    if not all(f['status']=='explicit' and (f['value'] or '').startswith('满减券共 ') for f in rules):
        return
    from business_agent.activity_model_first.coupon_tier_sheet import tier_schedule
    schedule=tier_schedule(payload)
    if not schedule:
        return
    _,tiers=schedule
    lines=[t.summary+'，原表行号'+'、'.join(str(position) for position,_ in t.rows) for t in tiers]
    observed=[line for f in rules for line in f['value'].splitlines()[1:]]
    if sorted(observed)==sorted(lines):
        value='\n'.join([f'满减券共 {len(tiers)} 档',*lines])
        parent['fields']['rulesText']=explicit(value,[ref for f in rules for ref in f['evidenceIds']])


def coupon_products_by_source_row(items, source_evidence, products, barcode_columns):
    """Only an exact barcode cell can anchor a receipt product to a physical row."""
    source_cells = {cell['locator']['cell']: cell for row in items for col, cell in row.items()
                    if col in barcode_columns and cell['text'].strip()}
    owners = defaultdict(list)
    for product in products:
        anchors = set()
        for eid in product['evidenceIds']:
            ref = source_evidence.get(eid)
            if not ref:
                continue
            cell = source_cells.get(ref['locator'].get('cell'))
            if (cell and cell['locator'] == ref['locator'] and cell['text'] == ref['quote']
                    and cell['text'].strip() == product.get('rawUpc')):
                anchors.add((cell['locator']['row'], product['rawUpc']))
        for key in anchors:
            # A product citing more than one physical row cannot resolve any of them.
            owners[key].append(product if len(anchors) == 1 else None)
    return {key: matches[0] for key, matches in owners.items() if len(matches) == 1 and matches[0]}


def original_coupon_options(doc, evidence, group, *, allow_funding_default):
    """Bind the confirmed workbook's parallel platform columns to its original checkmarks."""
    rows = [r for r in doc['tableRows'] if r['locator'].get('sheet')=='参券商品']
    header, *items = rows
    source_evidence = {e['evidenceId']: e for e in evidence if e['materialId'] == doc['materialId']
                       and e['sourceSha256'] == doc['sha256'] and e['locator'].get('kind') == 'xlsx'
                       and e['locator'].get('sheet') == '参券商品'}
    existing = {(e['materialId'], e['locator'].get('cell'), e['quote']): eid for eid, e in source_evidence.items()}
    ids = {e['evidenceId'] for e in evidence}
    def cite(cell):
        quote = cell['text']
        key = (doc['materialId'],cell['locator']['cell'],quote)
        if key not in existing:
            i=len(ids)+1
            while f'ev-{i}' in ids:
                i+=1
            eid=f'ev-{i}'
            ids.add(eid)
            evidence.append(dict(evidenceId=eid, materialId=doc['materialId'],sourceSha256=doc['sha256'],locator=cell['locator'],quote=quote))
            existing[key]=eid
        return existing[key]
    item_cells = [{c['column']: c for c in r['cells']} for r in items]
    by_row = coupon_products_by_source_row(item_cells, source_evidence, group['productRows'], ('B', 'K'))
    header_cells = {c['column']:c for c in header['cells']}
    lines, refs, tier_warnings = [], [], {}
    platform_counts = []
    for prefix, upc_col, name_col, platform_name, options in [
            ('A', 'B', 'C', '美团', 'DEFGH'), ('J', 'K', 'L', '淘宝', 'MNOP')]:
        entries = [row for row in item_cells if prefix in row]
        platform = next((row[prefix] for row in entries if row[prefix]['text'] == platform_name), None)
        if platform is None:
            continue
        refs.append(cite(platform))
        platform_label = '淘宝闪购' if platform['text'] == '淘宝' else platform['text']
        previous_count = len(lines)
        for col in options:
            h=header_cells[col]
            title=' '.join(h['text'].split())
            refs.append(cite(h))
            marked=0
            for row in entries:
                mark=row.get(col)
                if mark and mark['text'].strip().replace('\ufe0f','').upper() in CHECKS:
                    marked+=1
                    barcode, name = row.get(upc_col), row.get(name_col)
                    if barcode and name and row[prefix]['text'] == platform['text']:
                        product = by_row.get((mark['locator']['row'], barcode['text'].strip()))
                        source_name = name['text'].strip()
                        if product and source_name and source_name == (product.get('rawName') or '').strip():
                            cells = (row[prefix], barcode, name, h, mark)
                            product['evidenceIds'] = unique([*product['evidenceIds'], *(cite(c) for c in cells)])
            # A participation flag stays in the product evidence, outside the coupon list.
            coupon_amounts = re.search(r'(\d+)\s*-\s*(\d+)', title)
            if not coupon_amounts:
                continue
            description = title
            if '品5商3平台2' in title:
                threshold, discount_text = coupon_amounts.groups()
                discount = Decimal(discount_text)
                brand, merchant, platform_share = (discount * weight / 10 for weight in (5, 3, 2))
                allocation = (f'按券面优惠分摊：品牌50%（{brand:.2f}元），'
                              f'商家30%（{merchant:.2f}元），平台20%（{platform_share:.2f}元）')
                description = f'{title}，{allocation}'
                tier = f'{threshold}-{discount_text}'
                tier_warnings.setdefault(tier, {'code': 'MANUAL_ENROLLMENT_SELECTION_REQUIRED',
                    'message': f'自由点满{threshold}减{discount_text}，{allocation}，需要审核人勾选报名。',
                    'locator': h['locator']})
            scope = f'原表勾选 {marked} 行' if marked else '原表未勾选商品，适用范围待确认'
            line = f'{platform_label} · {description}（{scope}）'
            # Only the verified workbook's four headers with no funding declaration.
            if allow_funding_default and col in ('F', 'G', 'H', 'P') and 0 < Decimal(coupon_amounts[2]) < 10:
                line += '，品牌出资（按小额券规则默认）'
            lines.append(line)
        platform_counts.append(f'{platform_label} {len(lines) - previous_count} 张')
    upcs = {cell['text'].strip() for row in items for cell in row['cells']
            if cell['column'] in ('B', 'K') and cell['text'].strip()}
    summary = (f'品牌满减活动，共 {len(lines)} 张券（{"、".join(platform_counts)}），审核时逐档勾选；'
               f'参券商品表有 {len(items)} 行，按 UPC 去重 {len(upcs)} 个')
    group['fields']['rulesText']=explicit('\n'.join([summary, *lines]),refs)
    return list(tier_warnings.values())


def sheet_names(group, evidence):
    return {(e['materialId'],e['locator']['sheet']) for p in group['productRows'] for key in p['evidenceIds']
            if (e:=evidence.get(key)) and 'sheet' in e['locator']}


def unassigned_coupon_brand(group, meta, anchors, evidence):
    # Batch boundaries can leave product-only rows after the coupon header.
    brand_field = meta['brandNames']
    known = brand_field['value'] or []
    if len(known)>1 or meta['activityMethod']['value'] or any(
            group['fields'][k]['status']!='missing' for k in ('type','rulesText','activityName','sourceActivityId')):
        return None
    products = group['productRows']
    sheets = sheet_names(group,evidence)
    confirmed_ids=values([group['fields']['brandIds']])
    confirmed=confirmed_ids[0]['value'] if confirmed_ids else []
    matches = [b for b,locs in anchors.items() if sheets and sheets<=locs and
               ((isinstance(b,tuple) and confirmed==[b[1]]) or
                (isinstance(b,str) and (known==[b] or (not known and all(b in (p['rawName'] or '') for p in products)))))]
    return matches[0] if len(matches)==1 else None


@lru_cache(maxsize=1)
def confirmed_fingerprint():
    return json.loads(Path(__file__).with_name('freemore-cross-platform-coupon-v1.json').read_text())['sourceSha256']


def reconcile(reply, payload):
    if not reply.get('ok'):
        return reply
    result, meta = reply['result'],reply['metadata']
    docs=payload['documents']
    if not result['candidates']:
        return reply
    known = len(docs)==1 and docs[0]['kind']=='xlsx' and docs[0]['sha256']==confirmed_fingerprint()
    form=(payload.get('manualFields') or {}).get('fields',{})
    if form.get('activityMethod') in ('shen_jia','bao_hao_jia'):
        return reply
    data = {m['candidateId']:m for m in meta['candidateMetadata']}
    matrices = (coupon_selection_sheets(docs) if not form.get('activityMethod')
                and form.get('type') in (None, 'brand') else {})
    evidence = {e['evidenceId']: e for e in result['evidence']}
    buckets=defaultdict(list)
    for g in result['candidates']:
        detail = data[g['candidateId']]
        matrix_method = apply_coupon_method(g, detail, matrices, evidence, known) if matrices else False
        brand=brand_for(g,detail,known,matrix_method)
        if brand:
            buckets[brand].append(g)
    if not known and buckets:
        # A name-only candidate can join an identified brand only when this batch
        # provides exactly one explicit company brand ID for that exact name.
        ids_by_name=defaultdict(set)
        for identity,members in buckets.items():
            if isinstance(identity,tuple):
                for g in members:
                    label=data[g['candidateId']]['brandNames']
                    if label['status']=='explicit' and len(label['value'] or [])==1:
                        ids_by_name[label['value'][0].strip()].add(identity)
        for label,ids in ids_by_name.items():
            if label in buckets and len(ids)==1:
                buckets[next(iter(ids))].extend(buckets.pop(label))
        evidence = {e['evidenceId']:e for e in result['evidence']}
        anchors={b:set().union(*(sheet_names(g,evidence) for g in gs)) for b,gs in buckets.items()}
        assigned={g['candidateId'] for gs in buckets.values() for g in gs}
        # A mixed sheet may have price-adjustment rows with no field assignment.
        prices = {(doc['materialId'],r['locator'].get('sheet')) for doc in docs for r in doc['tableRows']
                  if any(c['text'].strip() in ('报名价','商品调价','单品调价','调整后单价','神价报名价') for c in r['cells'])}
        for b in anchors:
            anchors[b]-=prices
        for g in result['candidates']:
            if g['candidateId'] not in assigned:
                b=unassigned_coupon_brand(g,data[g['candidateId']],anchors,evidence)
                if b:
                    buckets[b].append(g)
    replacements, removed = {},set()
    for brand,groups in buckets.items():
        if len(groups)==1 and not known:
            continue
        members=[data[g['candidateId']] for g in groups]
        label=brand if isinstance(brand,str) else next((m['brandNames']['value'][0] for m in members if m['brandNames']['value']),None)
        merged=merged_group(groups,members,label)
        if merged is None:
            continue
        group, details=merged
        if not known:
            preserve_review_schedule(groups,group,payload)
        if known:
            manual_rules = group['fields']['rulesText'] if form.get('rulesText') else None
            notes = ' '.join(str(form.get(k) or '') for k in ('rulesText','remark','brandBenefit','rawText'))
            company = form.get('companyBearing') or {}
            has_funding_input = (form.get('storeBearing') == 'yes' or
                                 (isinstance(company, dict) and company.get('state') == 'yes') or
                                 re.search(r'出资|出钱|出款|买单|承担|分[摊担]|补贴|混补|共补|单补|[品商店]补|[品商]\s*\d', notes))
            tier_warnings=original_coupon_options(docs[0],result['evidence'],group,
                                                  allow_funding_default=not has_funding_input)
            if manual_rules and manual_rules['status']=='explicit':
                observed=group['fields']['rulesText']
                group['fields']['rulesText']=explicit(manual_rules['value']+'\n'+observed['value'],manual_rules['evidenceIds']+observed['evidenceIds'])
            ws=result['warnings']
            ws[:]=[w for w in ws if w['code'] not in ('MANUAL_ENROLLMENT_SELECTION_REQUIRED','COUPON_PERIOD_CONFIRMATION_REQUIRED','COUPON_SOURCE_ID_PENDING')]
            ws.extend(tier_warnings)
            fields=group['fields']
            if not form.get('platformCode') and fields['platformCode']['status']!='conflict':
                fields['platformCode']=dict(MISSING)
            if not manual_rules:
                ws[:]=[w for w in ws if not (w['code']=='MODEL_FIELD_EVIDENCE_UNSUPPORTED' and '字段 rulesText ' in w['message'])]
            if not form.get('type') and fields['type']['status']!='conflict':
                refs=unique(details['brandNames']['evidenceIds']+[e['evidenceId'] for e in result['evidence'] if e['quote'].startswith('品牌券')])
                fields['type']=explicit('brand',refs)
            if fields['type']['value']=='brand':
                ws[:]=[w for w in ws if not(w['code']=='MODEL_FIELD_EVIDENCE_UNSUPPORTED' and '字段 type ' in w['message'])]
            if matrices:
                evidence = {e['evidenceId']: e for e in result['evidence']}
                if apply_coupon_method(group, details, matrices, evidence, confirmed=True):
                    method_cells = {evidence[key]['locator']['cell'] for key in details['activityMethod']['evidenceIds']}
                    ws[:] = [w for w in ws if not (w['code']=='MODEL_FIELD_EVIDENCE_UNSUPPORTED'
                             and '字段 activityMethod ' in w['message'] and w.get('locator', {}).get('sheet')=='参券商品'
                             and w['locator'].get('cell') in method_cells)]
            if fields['sourceActivityId']['status']=='missing':
                ws.append({'code':'COUPON_SOURCE_ID_PENDING','message':'自由点平台活动 ID 后补。'})
            if any(fields[k]['status']=='missing' for k in ('startAt','endAt')):
                names=payload.get('materialNames',{})
                source_month=re.search(r'(\d{1,2})月',names.get(docs[0]['materialId'],''))
                form_month=re.search(r'(\d{1,2})月',form.get('activityName') or '')
                reason='自由点原表没有整场活动的完整起止日期'
                if source_month and form_month and int(source_month[1])!=int(form_month[1]):
                    reason+=f'，提报名为{form_month[0]}、文件名为{source_month[0]}'
                ws.append({'code':'COUPON_PERIOD_CONFIRMATION_REQUIRED','message':reason+'，请提报人确认。“国庆生效”仅适用于满3减3。'})
        first=group['candidateId']
        replacements[first]=(group,details)
        removed.update(g['candidateId'] for g in groups[1:])
    if replacements:
        result['candidates']=[replacements[g['candidateId']][0] if g['candidateId'] in replacements else g
                              for g in result['candidates'] if g['candidateId'] not in removed]
        meta['candidateMetadata']=[replacements[m['candidateId']][1] if m['candidateId'] in replacements else m
                                   for m in meta['candidateMetadata'] if m['candidateId'] not in removed]
        missing={k for g in result['candidates'] for k,f in g['fields'].items() if f['status']=='missing'}
        old={x['field']:x for x in result['missingFields']}
        result['missingFields']=[old.get(k,{'field':k,'reasonCode':'NOT_PROVIDED'}) for k in sorted(missing)]
    return reply
