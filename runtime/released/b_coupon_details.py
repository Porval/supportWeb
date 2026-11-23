"""Structured per-tier amounts and candidate-scoped source rows for the workbench."""
from collections import defaultdict
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from hashlib import sha256
import json
import os
import re
from unicodedata import normalize

from b_state import fingerprint

AMOUNTS = re.compile(r'(?:满\s*)?(\d+(?:\.\d+)?)\s*元?\s*(?:-|减)\s*(\d+(?:\.\d+)?)')
PARTY = {'品牌方':'brand','品牌':'brand','品':'brand','商家':'store','门店':'store','商':'store',
         '平台':'platform','公司':'company','价探':'company'}
ALLOCATION = re.compile(r'(品牌方|品牌|商家|门店|平台|公司|价探|品|商)\s*(?:出资|承担|补贴|补)?\s*(?:为|[:：])?\s*(\d+(?:\.\d+)?)\s*(%|元)')
# The owner verified bare integers as a ratio for this exact original workbook.
ZIYOUDIAN_RATIO_WORKBOOK = os.environ.get('WORKBENCH_COUPON_RATIO_WORKBOOK_SHA256')
MARKS = frozenset(('Y','✓','✔','✅','√','是'))


def number(value):
    return format(value.normalize(), 'f')


# Match complete statements so a question, exception or partial negation cannot authorize selection.
STORE_PARTY = r'(?:门店|商家)(?:方)?'
NO_NEED = r'(?:无需|不需要|不用|不需|不必)'
FUND_VERB = r'(?:出资|出钱|补贴|承担(?:补贴|费用|优惠)?)'
NO_STORE_CLAUSE = re.compile(
    rf'(?:{STORE_PARTY}(?:{NO_NEED}|不){FUND_VERB}|{NO_NEED}(?:由)?{STORE_PARTY}{FUND_VERB}'
    rf'|{STORE_PARTY}(?:出资|补贴|承担)(?:金额)?(?:为|:)?0+(?:\.0+)?(?:元|%)?)')
STORE_PAYS_CLAUSE = re.compile(
    rf'(?:由{STORE_PARTY}{FUND_VERB}|{STORE_PARTY}(?:必须|需要|应|须|需){FUND_VERB}|{STORE_PARTY}(?:出资|出钱))')
OTHER_FUNDER_CLAUSE = re.compile(
    r'(?:由|仅由)?(?:平台|公司|价探|门店|商家)(?:方)?(?:补贴|出资|承担|全(?:额|部)(?:出资|承担|补贴)|承担全部(?:补贴|优惠)?)')
BRAND_ONLY_CLAUSE = re.compile(
    r'(?:由)?品牌(?:方)?(?:单(?:方)?补(?:贴)?|独资|全(?:额|部)(?:出资|承担|补贴)|承担全部(?:补贴|优惠)?)'
    r'|(?:仅(?:由)?|单)品牌(?:方)?(?:出资|承担|补贴)')
SINGLE_FUNDER_CLAUSE = re.compile(
    r'(?:由|仅由)?(平台|公司|价探|门店|商家)(?:方)?(?:全额(?:承担|出资|补贴)?|承担全部(?:补贴|优惠)?|独资|单补)')
UNCERTAIN_FUNDING = re.compile(
    r'待(?:核实|核对|确认|定)|不确定|未(?:核实|确认)|是否|[?？]|可能|假设|除非|若|如果|'
    r'并非|不是|不代表|不一定|尚不能|以.*为准|需(?:要)?确认|不适用|取消|不属|不认可|争议|矛盾|冲突')


def funding_clauses(quote):
    text = AMOUNTS.sub('|', quote)
    # A source cell may put the face value, clause, and qualifying text on separate lines.
    return [''.join(part.split()) for part in re.split(r'[,;；、。|/()\n]', text) if part.strip()]


def source_share_positive(entry):
    for key in ('amount','percent'):
        try:
            amount = Decimal(str(entry.get(key)))
        except InvalidOperation:
            continue
        if amount.is_finite() and amount > 0:
            return True
    return False


def funding(option, *, store_bearing=None, schedule=None):
    quote = normalize('NFKC', option.get('name') or '')
    matches = AMOUNTS.findall(quote)
    threshold, discount = (map(Decimal, matches[0]) if len(matches) == 1 else (None, None))
    if discount is None or not 0 < discount <= threshold:
        threshold = discount = None
    shares = {}
    conflicting = False
    uncertain = bool(UNCERTAIN_FUNDING.search(quote))
    # The original 自由点 header expresses tenths of face value, as confirmed by the business owner.
    ratio_declared = (bool(ZIYOUDIAN_RATIO_WORKBOOK) and (option.get('source') or {}).get('sourceSha256') == ZIYOUDIAN_RATIO_WORKBOOK) or '按比例' in quote
    if ratio_declared and '三方混补' in quote and re.search(r'\(\s*品\s*5\s*商\s*3\s*平台\s*2\s*\)', quote):
        shares = {p: ('%', Decimal(v)) for p,v in [('brand',50),('store',30),('platform',20)]}
    else:
        for label, raw, unit in ALLOCATION.findall(quote):
            party, value = PARTY[label], Decimal(raw)
            if party in shares and shares[party] != (unit, value):
                conflicting = True
                shares.clear()
                break
            shares[party] = unit, value
    clauses = funding_clauses(quote)
    explicit_single = any(BRAND_ONLY_CLAUSE.fullmatch(part) for part in clauses)
    sole_funders = {'brand'} if explicit_single else set()
    for clause in clauses:
        match = SINGLE_FUNDER_CLAUSE.fullmatch(clause)
        if match:
            sole_funders.add(PARTY[match[1]])
    if sole_funders:
        active = {party for party, (_, value) in shares.items() if value > 0}
        if len(sole_funders)>1 or active-sole_funders:
            conflicting = True
        elif not shares and not (uncertain or conflicting):
            shares[next(iter(sole_funders))] = '%', Decimal(100)
    total_percent = sum(v for unit,v in shares.values() if unit == '%')
    total_amount = sum(v for unit,v in shares.values() if unit == '元')
    accounted = total_amount + discount * total_percent / 100 if discount is not None else None
    if total_percent > 100 or accounted is not None and accounted > discount:
        conflicting = True
        shares.clear()
    split = []
    for party,(unit,value) in shares.items():
        amount = value if unit == '元' else (discount * value / 100 if discount is not None else None)
        # A fractional cent has no confirmed settlement rule. Keep the source ratio.
        if amount is not None and amount != amount.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP):
            amount = None
        split.append({'party':party, 'percent':number(value) if unit == '%' else None,
                      'amount':format(amount,'.2f') if amount is not None else None})
    positive = {party for party,(_,v) in shares.items() if v > 0}
    total_known = bool(shares) and (total_amount==0 and total_percent==100
                                   or accounted is not None and accounted==discount)
    participants = ()
    if re.search(r'三方(?:混补|共补|补贴)',quote):
        participants = ('brand','store','platform')
    elif re.search(r'(?:两方|双方|二方)(?:混补|共补|补贴)|品牌补[+、与和 ]+(?:商补|门店补)',quote):
        participants = ('brand','store')
    if participants and (positive-set(participants) or total_known and positive!=set(participants)):
        conflicting=True
    if sole_funders and shares and not total_known:
        conflicting=True
    mixed_store = 'store' in participants
    if total_known:
        participants = tuple(positive)
    for party in participants:
        if party not in shares:
            split.append({'party':party,'amount':None,'percent':None})
    mode = {frozenset(('brand',)):'brand_only', frozenset(('brand','store')):'brand_store',
            frozenset(('brand','store','platform')):'brand_store_platform'}.get(frozenset(participants),'unknown')
    if total_known and len(positive)==1:
        mode = next(iter(positive))+'_only'
    elif total_known and mode=='unknown' and positive:
        mode = 'shared'
    # This stored status was projected for the exact source cell in the same version.
    # A same-name coupon from another platform or an unbound label provides no such evidence.
    bound_source = source_key(option)
    stored_status = option.get('storeFundingStatus') if bound_source else None
    saved_shares = (option.get('fundingShares') or []) if bound_source else []
    saved_funders = {entry.get('party') for entry in saved_shares if source_share_positive(entry)}
    store_required = ('store' in positive or 'store' in saved_funders or mixed_store or option.get('storeFundingRequired')
                      or stored_status == 'required' or any(STORE_PAYS_CLAUSE.fullmatch(part) for part in clauses))
    store_exempt = (('store' in shares and 'store' not in positive)
                    or (total_known and 'store' not in positive)
                    or any(NO_STORE_CLAUSE.fullmatch(part) for part in clauses) or stored_status == 'none')
    if conflicting or uncertain or (store_required and store_exempt):
        store = 'unknown'
    elif store_required:
        store = 'required'
    elif store_exempt or store_bearing == 'no':
        store = 'none'
    else:
        store = 'unknown'
    if not split and not (conflicting or uncertain) and saved_shares:
        split = saved_shares
        mode = option.get('fundingMode') or mode
    confirmation = option.get('brandFundingConfirmation') or {}
    other_funders = (positive | saved_funders) - {'brand'}
    contradictory = conflicting or store_required or other_funders or any(OTHER_FUNDER_CLAUSE.fullmatch(p) for p in clauses)
    if confirmation.get('answer') == 'brand_only' and not contradictory:
        # An explicit partial brand allocation prevents an all-brand declaration.
        brand_share = shares.get('brand')
        if brand_share is None or brand_share == ('%', Decimal(100)) or brand_share == ('元', discount):
            store, mode = 'none', 'brand_only'
            amount = (format(discount,'.2f') if discount is not None and discount == discount.quantize(Decimal('.01'))
                      else None)
            split = [{'party':'brand','percent':'100','amount':amount}]
    note = option.get('fundingNote') or {}
    note_key = source_key(option,schedule)
    same_platform = (note.get('platformCode')==option['platformCode'] if option.get('platformCode')
                     else note.get('sourceChannel')==option.get('channel'))
    note_text = note.get('text') if (same_platform and note_key and note.get('tierId')==option.get('tierId')
                                    and note.get('sourceTierKey')==note_key) else None
    return {'thresholdAmount': number(threshold) if threshold is not None else None,
            'discountAmount': number(discount) if discount is not None else None,
            'fundingShares':split, 'fundingMode':mode, 'storeFundingStatus':store,
            'submitterFundingNote': note_text}


def nonbrand_funding(option):
    """Conflicting or incomplete sums cannot support the brand-only shortcut."""
    projected = funding(option)
    if (projected['storeFundingStatus'] == 'required' or any(
            share['party'] != 'brand' and source_share_positive(share) for share in projected['fundingShares'])):
        return True
    quote = normalize('NFKC', option.get('name') or '')
    return any(PARTY[label] != 'brand' and Decimal(value) > 0 for label,value,_ in ALLOCATION.findall(quote))


def source_key(option, schedule=None):
    source = option.get('source') or option.get('sourceReference') or {}
    schedule = schedule or {}
    loc = source.get('locator') or {}
    identity = [source.get('sourceSha256') or schedule.get('sourceSha256'),source.get('materialId') or schedule.get('materialId'),
                loc.get('sheet'), loc.get('cell') or loc.get('location') or source.get('location') or option.get('dateEvidenceId'),option.get('platformCode')]
    if identity[0] and identity[1] and not identity[3] and loc and source.get('quote'):
        return fingerprint([identity[1],identity[0],loc,source['quote'],option.get('platformCode')])
    if not identity[1] or not identity[3]:
        return None
    return sha256(json.dumps(identity, ensure_ascii=False,separators=(',',':')).encode()).hexdigest()


def bound_candidate(db, version):
    binding = version.get('snapshot', {}).get('agentRecognitionBinding') or {}
    if not binding.get('runId') or not version.get('submissionId'):
        return None, None
    row = db.execute("SELECT payload FROM analysis_runs WHERE id=? AND submission_id=? AND input_revision=? "
                     "AND input_digest=? AND state='succeeded'", (binding['runId'],version['submissionId'],
                      binding.get('inputRevision'),binding.get('inputDigest'))).fetchone()
    if not row:
        return None, None
    payload = json.loads(row[0])
    analysis = payload.get('result') or {}
    if (not binding.get('analysisDigest') or binding['analysisDigest'] != payload.get('analysisDigest')
            or 'sha256:'+fingerprint(analysis) != binding['analysisDigest']):
        return None, None
    candidate = next((c for c in analysis.get('candidates',[]) if c['candidateId']==binding.get('candidateId')), None)
    return analysis, candidate


def mirrored_taobao_product(row, coupon_column, material_id, digest, row_refs, products, evidence):
    """Recover the omitted right-hand UPC reference in the original 自由点 coupon workbook."""
    sheet, number = row['locator']['sheet'], row['locator']['row']
    if digest != ZIYOUDIAN_RATIO_WORKBOOK or sheet != '参券商品' or coupon_column not in ('M','N'):
        return None
    cols = ('A','B','C','J','K','L',coupon_column)
    cells = {c['column']:c for c in row['cells'] if c['column'] in cols}
    if len(cells)!=len(cols) or any(cells[col]['locator'] != {
            'kind':'xlsx','sheet':sheet,'row':number,'cell':f'{col}{number}'} for col in cols):
        return None
    value = {col:cells[col]['text'].strip() for col in cols}
    if (value['A']!='美团' or value['J'] not in ('淘宝','淘宝闪购') or not value['B'] or not value['C']
            or value['B']!=value['K'] or value['C']!=value['L'] or value[coupon_column].upper().rstrip('\ufe0f') not in MARKS):
        return None
    ids = row_refs[(material_id,digest,sheet,f'B{number}',value['B'])]
    if len(ids)!=1:
        return None
    rid, = ids
    product = products[rid]
    if product.get('rawName')!=value['L']:
        return None
    required = {f'{col}{number}':value[col] for col in ('B','C','J',coupon_column)}
    found = {}
    for eid in product['evidenceIds']:
        ref = evidence.get(eid) or {}
        loc = ref.get('locator') or {}
        cell = loc.get('cell')
        if (ref.get('materialId')==material_id and ref.get('sourceSha256')==digest and loc.get('sheet')==sheet
                and loc.get('row')==number and cell in required and ref['quote'].strip()==required[cell]):
            found[cell]=eid
    if found.keys()!=required.keys():
        return None
    return {'rowId':rid,'upc':value['K'],'name':value['L'],'rawName':value['L'],
            'sourceReference':{'materialId':material_id,'sourceSha256':digest,'sheet':sheet,'row':number,
                               'couponCell':f'{coupon_column}{number}','upcCell':f'K{number}',
                               'nameCell':f'L{number}','evidenceIds':[found[cell] for cell in required]}}


def per_tier_products(analysis, candidate, options):
    """Associate an original mark only with a candidate's proven UPC in the same horizontal block/row."""
    result = {}
    if not candidate or not analysis:
        return result
    scoped = defaultdict(list)
    for option in options:
        source = option.get('source') or {}
        loc = source.get('locator') or {}
        if loc.get('kind') == 'xlsx':
            scoped[(source['materialId'],source['sourceSha256'],loc['sheet'],loc['row'])].append(option)
    if not scoped:
        return result
    evidence = {e['evidenceId']:e for e in analysis['evidence']}
    row_refs = defaultdict(set)
    product_by_id = {}
    for product in candidate['productRows']:
        upc = product.get('rawUpc')
        if not upc:
            continue
        product_by_id[product['rowId']] = product
        for eid in product['evidenceIds']:
            e = evidence.get(eid) or {}
            loc = e.get('locator') or {}
            if loc.get('kind') == 'xlsx' and e['quote'].strip() == upc:
                row_refs[(e['materialId'],e['sourceSha256'],loc['sheet'],loc['cell'],upc)].add(product['rowId'])
    from business_agent.activity_model_first.horizontal_coupon_matrix import column_order
    for doc in analysis.get('materialResults', []):
        if doc.get('status') != 'parsed' or doc.get('kind') != 'xlsx':
            continue
        for (mid,digest,sheet,row_no),tiers in scoped.items():
            if (mid,digest) != (doc['materialId'],doc['sha256']):
                continue
            rows = [r for r in doc['tableRows'] if r['locator'].get('sheet') == sheet]
            header = next((r for r in rows if r['locator']['row']==row_no),None)
            if not header:
                continue
            cells = header['cells']
            platforms = [c for c in cells if c['text'].strip()=='提报平台']
            for tier in tiers:
                ref = tier['source']
                target = next((c for c in cells if c['locator']==ref['locator'] and c['text']==ref['quote']), None)
                if not target:
                    continue
                prior = [c for c in platforms if column_order(c['column']) < column_order(target['column'])]
                if not prior:
                    continue
                block = max(column_order(c['column']) for c in prior)
                if not any(column_order(c['column'])==block+1 and normalize('NFKC',c['text']).strip().lower()
                           in ('商品条码(upc)','商品条码','upc') for c in cells):
                    continue
                products = {}
                marked_rows = 0
                for r in rows:
                    if r['locator']['row'] <= row_no:
                        continue
                    contents = r['cells']
                    if not any(c['column']==target['column'] and c['text'].strip().upper().rstrip('\ufe0f') in MARKS for c in contents):
                        continue
                    marked_rows += 1
                    barcode = next((c for c in contents if column_order(c['column'])==block+1),None)
                    if not barcode:
                        continue
                    upc = barcode['text'].strip()
                    ids = row_refs[(mid,digest,sheet,barcode['locator']['cell'],upc)]
                    if len(ids) == 1:
                        rid = next(iter(ids))
                        products[rid] = {'rowId':rid,'upc':upc,'name':product_by_id[rid].get('rawName'),'rawName':product_by_id[rid].get('rawName')}
                    elif not ids and tier.get('platformCode')=='taobao_instant' and target['column'] in ('M','N'):
                        match = mirrored_taobao_product(r,target['column'],mid,digest,row_refs,product_by_id,evidence)
                        if match:
                            products[match['rowId']] = match
                state = 'source_unmarked' if not marked_rows else ('source_marked' if len(products)==marked_rows else 'source_partial')
                result[tier['tierId']] = {'productScopeStatus':state,'sourceProducts':list(products.values())}
    return result
