"""Review participation by coupon, using the confirmed version and source references."""

from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
import json
import re
from unicodedata import normalize

from account_primitives import fail
from b_activity_method import PRODUCT_REGISTRATION_METHODS, read_activity_method, requires_store_bearing
from b_coupon_schedule import BEIJING, public_schedule
from b_coupon_details import bound_candidate, funding, per_tier_products, source_key

MIXED = re.compile(r'(?:^|[\s【（(：:、，,；;。]|[两一二三四234]方)(?:混补|共补)')
COUPON_AMOUNT = re.compile(r'(?:满\s*)?(\d+(?:\.\d+)?)\s*元?\s*(?:-|减)\s*(\d+(?:\.\d+)?)')
COUPON_CUE = re.compile(r'满\s*\d+(?:\.\d+)?\s*元?\s*减|满减|券|^(?:满\s*)?\d+(?:\.\d+)?\s*-\s*\d+(?:\.\d+)?(?:元|$|\s)')
GROUPING_MESSAGE = '同一活动的同一档券被识别为多个分组，请重新归组后再建档。'
PLATFORMS = {'美团': 'meituan', '淘宝': 'taobao_instant', '淘宝闪购': 'taobao_instant', '京东': 'jd'}
PLATFORM_LABELS = {'meituan': '美团', 'taobao_instant': '淘宝闪购', 'jd': '京东'}
SELECTED_MARKS = frozenset(('Y', '✓', '✔', '✅', '√', '是'))


def marked(text):
    return text.strip().upper().rstrip('\ufe0f') in SELECTED_MARKS


def threshold_coupon(method, rules):
    return method not in PRODUCT_REGISTRATION_METHODS and (requires_store_bearing(method, rules) or explicit_mix(rules))


def split_brand_coupon_groups(result, metadata, db=None):
    """A brand can run overlapping promotions. Block only a duplicate of one identified coupon."""
    grouped = defaultdict(list)
    evidence = {item['evidenceId']:item for item in result.get('evidence', [])}
    details = {item['candidateId']: item for item in metadata}
    for candidate in result['candidates']:
        fields = candidate['fields']
        brands = fields.get('brandIds', {})
        metadata_item = details.get(candidate['candidateId'], {})
        method = metadata_item.get('activityMethod', {})
        if (method.get('status') == 'conflict' or brands.get('status') == 'conflict'
                or not threshold_coupon(method.get('value'), fields.get('rulesText', {}).get('value'))):
            continue
        brand_ids = brands.get('value') or []
        names = metadata_item.get('brandNames') or {}
        if db is not None and not brand_ids and names.get('status')=='explicit' and len(names.get('value') or [])==1:
            # Resolve an explicit recognized brand label only on one exact master match.
            brand_ids = [row[0] for row in db.execute(
                "SELECT id FROM brands WHERE json_extract(body,'$.name')=? LIMIT 2", (names['value'][0],))]
        platform, external = fields.get('platformCode') or {}, fields.get('sourceActivityId') or {}
        if (len(brand_ids)!=1 or external.get('status')!='explicit' or not external.get('value')
                or platform.get('status')!='explicit' or not platform.get('value')):
            continue
        for eid in (fields.get('rulesText') or {}).get('evidenceIds', []):
            ref = evidence.get(eid) or {}
            where = ref.get('locator') or {}
            if (where.get('kind')=='xlsx' and where.get('cell') and ref.get('sourceSha256')
                    and (explicit_mix(ref.get('quote')) or COUPON_CUE.search(normalize('NFKC',ref.get('quote',''))))
                    and len(COUPON_AMOUNT.findall(normalize('NFKC', ref.get('quote',''))))==1):
                key = (brand_ids[0],platform['value'],external['value'],ref['sourceSha256'],where.get('sheet'),where['cell'])
                if candidate['candidateId'] not in grouped[key]:
                    grouped[key].append(candidate['candidateId'])
    conflicts = defaultdict(set)
    for peers in grouped.values():
        if len(peers)>1:
            for cid in peers:
                conflicts[cid].update(peers)
    return {cid:sorted(peers) for cid,peers in conflicts.items()}


def explicit_mix(*values):
    return any(isinstance(value, str) and MIXED.search(value) for value in values)


def coupon_sources(analysis, rule_evidence_ids, allowed_platforms, *, mixed_only=True, product_rows=()):
    """Project the exact coupon headers cited by the Agent, retaining their identities."""
    cited = set(rule_evidence_ids)
    documents = {doc['materialId']: doc for doc in analysis.get('materialResults', [])
                 if doc.get('kind') == 'xlsx' and doc.get('status') == 'parsed'}
    options = {}
    original = analysis.get('evidence', [])
    scoped = [ref for ref in original if ref['evidenceId'] in cited]
    scoped.extend(matrix_headers_from_marks(documents, original, product_rows))
    for ref in scoped:
        name = ' '.join(ref['quote'].split())
        normalized = normalize('NFKC', name)
        mixed = explicit_mix(normalized)
        amounts = COUPON_AMOUNT.findall(normalized)
        if (mixed_only and not mixed) or len(name)>240 or len(amounts)!=1:
            continue
        threshold, discount = map(Decimal, amounts[0])
        if not 0 < discount <= threshold:
            continue
        where = ref['locator']
        doc = documents.get(ref['materialId'])
        platform = None
        matrix_header = False
        source_products_unmarked = False
        if where.get('kind') == 'xlsx':
            if not doc or doc['sha256'] != ref['sourceSha256']:
                continue
            matrix_header, platform, source_products_unmarked = header_platform(doc, where, ref['quote'])
            if matrix_header and not platform:
                continue
        if not (mixed or matrix_header or COUPON_CUE.search(normalized)):
            continue
        if platform and allowed_platforms and platform not in allowed_platforms:
            continue
        if not platform and len(allowed_platforms) == 1:
            platform, = allowed_platforms
        if not platform and (mixed_only or not allowed_platforms):
            continue
        # The source location keeps equal discounts in different platform columns distinct.
        key = (ref['materialId'], where.get('sheet'), where.get('cell') or name, platform)
        option = {'name': name, 'evidenceId': ref['evidenceId'], 'storeFundingRequired': mixed,
                  'source': {key: ref[key] for key in ('materialId','sourceSha256','locator','quote')}}
        if platform:
            option.update(platformCode=platform,platformLabel=PLATFORM_LABELS[platform])
        if source_products_unmarked:
            option['sourceProductsUnmarked'] = True
        option.update(funding(option))
        option['storeFundingRequired'] = option['storeFundingStatus']=='required'
        options.setdefault(key, option)
    return [{'tierId': f'offer-{index}', **option} for index, option in enumerate(options.values(), 1)]


def matrix_headers_from_marks(documents, evidence, product_rows):
    """Keep exact coupon-column identities when the Agent cites marked product cells."""
    cited = {key for row in product_rows for key in row['evidenceIds']}
    scoped = defaultdict(dict)
    for item in evidence:
        where = item['locator']
        doc = documents.get(item['materialId'])
        if (item['evidenceId'] in cited and doc and doc['sha256']==item['sourceSha256']
                and where.get('kind')=='xlsx' and where.get('cell') and marked(item['quote'])):
            scoped[(item['materialId'],where.get('sheet'))][where['cell']] = item
    headers = []
    for (mid,sheet), refs in scoped.items():
        rows = [row for row in documents[mid]['tableRows'] if row['locator'].get('sheet')==sheet]
        if not rows or not any(cell['text'].strip()=='提报平台' for cell in rows[0]['cells']):
            continue
        by_column = {}
        for row in rows[1:]:
            for cell in row['cells']:
                ref = refs.get(cell['locator'].get('cell'))
                if ref and ref['quote']==cell['text'] and ref['locator']==cell['locator']:
                    by_column.setdefault(cell['column'],ref)
        for cell in rows[0]['cells']:
            ref = by_column.get(cell['column'])
            if ref:
                headers.append(ref | {'locator':cell['locator'],'quote':cell['text']})
    return headers


def header_platform(document, where, quote):
    from business_agent.activity_model_first.horizontal_coupon_matrix import column_order

    sheet = where.get('sheet')
    rows = [row for row in document['tableRows'] if row['locator'].get('sheet') == sheet]
    headers = next((row for row in rows if row['locator'].get('row') == where.get('row')), None)
    if headers is None:
        return False, None, False
    target = next((cell for cell in headers['cells']
                   if cell['locator'].get('cell') == where.get('cell') and cell['text'] == quote), None)
    if target is None:
        return False, None, False
    platform_cols = [cell['column'] for cell in headers['cells'] if cell['text'].strip() == '提报平台']
    possible = [column for column in platform_cols if column_order(column) < column_order(target['column'])]
    if not possible:
        return False, None, False
    column = max(possible, key=column_order)
    product_rows = [{cell['column']: cell['text'].strip() for cell in row['cells']}
                    for row in rows if row['locator']['row'] > where['row']]
    marked_rows = [row for row in product_rows if marked(row.get(target['column'], ''))]
    if marked_rows:
        platforms = {PLATFORMS.get(row.get(column)) for row in marked_rows}
        return True, next(iter(platforms)) if len(platforms) == 1 else None, False

    # A declared coupon column may have no enrolment marks. In that case the
    # adjacent platform, UPC and product-name headers identify its own block.
    label_columns = {column_order(cell['column']): cell for cell in headers['cells']}
    block_start = column_order(column)
    upc_header = label_columns.get(block_start + 1)
    name_header = label_columns.get(block_start + 2)
    if (not upc_header or not name_header or column_order(target['column']) <= block_start + 2
            or normalize('NFKC', upc_header['text']).strip().casefold() not in ('商品条码(upc)', '商品条码', 'upc')
            or name_header['text'].strip() != '商品名称'):
        return True, None, False
    upc_column, name_column = upc_header['column'], name_header['column']
    block_platforms = {PLATFORMS.get(row.get(column))
                       for row in product_rows if row.get(upc_column) and row.get(name_column)}
    if len(block_platforms) != 1 or None in block_platforms:
        return True, None, False
    return True, next(iter(block_platforms)), True


def brand_coupon_method_correction(analysis, candidate, metadata, allowed_platforms):
    """Correct a price method cited only from a coupon matrix's participation header."""
    method = metadata['activityMethod']
    rules = candidate['fields'].get('rulesText') or {}
    documents = analysis.get('materialResults', [])
    if (method['status'] != 'explicit' or method['value'] != 'bao_hao_jia'
            or rules.get('status') != 'explicit'
            or len(analysis.get('candidates', [])) != 1 or len(documents) != 1):
        return None
    doc, = documents
    if doc.get('kind') != 'xlsx' or doc.get('status') != 'parsed':
        return None
    references = {ref['evidenceId']: ref for ref in analysis.get('evidence', [])}
    if len(method['evidenceIds']) != 1 or method['evidenceIds'][0] not in references:
        return None
    original = references[method['evidenceIds'][0]]
    where = original['locator']
    if (original['quote'].strip() != '爆好价' or original['materialId'] != doc['materialId']
            or original['sourceSha256'] != doc['sha256'] or where.get('kind') != 'xlsx'):
        return None
    matrix_header, platform, unmarked = header_platform(doc, where, original['quote'])
    if not matrix_header or not platform:
        return None
    first = next((row for row in doc['tableRows'] if row['locator'].get('sheet') == where.get('sheet')), None)
    if not first or first['locator'].get('row') != where.get('row'):
        return None
    if any(re.search(r'报名价|商品单价|原价|活动价|到手价|调价|价格|商品补贴金额', cell['text'])
           for cell in first['cells']):
        return None
    column = next(cell['column'] for cell in first['cells'] if cell['locator'].get('cell') == where['cell'])
    if any(cell['column'] == column and cell['text'].strip() and not marked(cell['text'])
           for row in doc['tableRows'] if row['locator'].get('sheet') == where.get('sheet')
           and row['locator']['row'] > where['row'] for cell in row['cells']):
        return None
    options = coupon_sources(analysis, rules['evidenceIds'], allowed_platforms, mixed_only=False,
                             product_rows=candidate['productRows'])
    evidence_ids = list(dict.fromkeys(option['evidenceId'] for option in options
                                      if option['storeFundingRequired'] and '三方混补' in option['name']))
    subtype = 'brand_threshold_subsidy_triple' if evidence_ids else None
    return {'fromMethod':method['value'], 'toMethod':subtype,
            'priceHeaderEvidenceId':original['evidenceId'],
            'couponEvidenceIds':evidence_ids or list(dict.fromkeys(o['evidenceId'] for o in options))}


def migrate(db):
    db.execute("""CREATE TABLE IF NOT EXISTS coupon_selection_drafts(
        version_id TEXT PRIMARY KEY REFERENCES activity_versions(id) ON DELETE CASCADE,
        revision INTEGER NOT NULL, selected_ids TEXT NOT NULL,
        saved_by TEXT NOT NULL, saved_at TEXT NOT NULL, review_stage TEXT NOT NULL DEFAULT 'initial')""")
    if 'review_stage' not in {r[1] for r in db.execute('PRAGMA table_info(coupon_selection_drafts)')}:
        db.execute("ALTER TABLE coupon_selection_drafts ADD COLUMN review_stage TEXT NOT NULL DEFAULT 'initial'")


def read_selection(db, version_id):
    cursor = db.execute('SELECT * FROM coupon_selection_drafts WHERE version_id=?', (version_id,))
    row = cursor.fetchone()
    if row is None:
        return None
    values = dict(zip((c[0] for c in cursor.description), row))
    return {'versionId':version_id, 'revision':values['revision'], 'selectedCouponIds':json.loads(values['selected_ids']),
            'savedBy':values['saved_by'], 'savedAt':values['saved_at'], 'reviewStage':values.get('review_stage','initial')}


def checked_selection(known, chosen):
    if (not isinstance(chosen,list) or len(chosen)>len(known)
            or any(not isinstance(item,str) or item not in known for item in chosen)
            or len(set(chosen))!=len(chosen)):
        fail(422,'INVALID_COUPON_SELECTION','券勾选须来自当前审核版本，请刷新后重试。')
    return [item for item in known if item in chosen]


def check_revision(expected, actual):
    if type(expected) is not int or expected<0:
        fail(422,'INVALID_REQUEST','请输入当前的选券草稿版本。')
    if expected != actual:
        fail(409,'COUPON_SELECTION_STALE','选券草稿已有更新，请刷新后核对。')


def require_visible_options(projection, visible_ids, version):
    if projection is None or visible_ids is None:
        return
    for option in projection['options']:
        mid = option.get('sourceMaterialId')
        if mid and mid not in visible_ids:
            fail(403, 'FORBIDDEN', '当前账号无法核对本版本的完整券档。')


def save_selection(db, version, actor, body, *, visible_material_ids=None, review_state=None):
    """The caller verifies this actor can decide the pending node and holds the write transaction."""
    if not isinstance(body,dict) or set(body) - {'expectedReviewStage'} != {'expectedRevision','selectedCouponIds'}:
        fail(422,'INVALID_REQUEST','保存选券需要当前草稿版本和券档清单。')
    stage = body.get('expectedReviewStage','initial')
    if stage not in ('initial','pricing','final') or (review_state or 'initial_pending') != stage+'_pending':
        fail(409,'STATE_CONFLICT','选券的审核阶段已变化，请刷新页面。')
    projection = review_projection(db,version,include_products=False,review_state=review_state)
    require_visible_options(projection, visible_material_ids, version)
    if projection is None or projection['selectionLocked']:
        fail(409,'COUPON_SELECTION_LOCKED','当前版本不能修改审核选券。')
    revision = projection['draftRevision']
    check_revision(body['expectedRevision'],revision)
    chosen = checked_selection([o['tierId'] for o in projection['options']],body['selectedCouponIds'])
    require_eligible_selection(projection,chosen)
    stamp = datetime.now(timezone.utc).isoformat(timespec='microseconds')
    updated = db.execute("""INSERT INTO coupon_selection_drafts(version_id,revision,selected_ids,saved_by,saved_at,review_stage)
        VALUES(?,?,?,?,?,?) ON CONFLICT(version_id) DO UPDATE SET revision=excluded.revision,
        selected_ids=excluded.selected_ids, saved_by=excluded.saved_by, saved_at=excluded.saved_at,
        review_stage=excluded.review_stage
        WHERE coupon_selection_drafts.revision=?""",
        (version['id'],revision+1,json.dumps(chosen),actor['id'],stamp,stage,revision))
    if updated.rowcount!=1:
        fail(409,'COUPON_SELECTION_STALE','选券草稿已有更新，请刷新后核对。')
    return read_selection(db,version['id'])


def review_projection(db, version, decisions=None, *, visible_material_ids=None, include_products=True, review_state=None):
    snapshot = version.get('snapshot', {})
    if read_activity_method(snapshot) in PRODUCT_REGISTRATION_METHODS:
        return None
    values = snapshot.get('resolvedFields') or snapshot.get('confirmedFields') or {}
    bound = snapshot.get('couponReviewInput') or snapshot.get('mixedCouponReview')
    mixed = bool(snapshot.get('mixedCouponReview')) or (bound or {}).get('storeFundingRequired', False)
    mixed = mixed or explicit_mix(values.get('activityName'), values.get('rulesText'))
    schedule = snapshot.get('couponSchedule')
    if not bound and not mixed and not schedule:
        return None
    bound_options = (bound or {}).get('options', [])
    schedule_tiers = {t['tierId']:t for t in (schedule or {}).get('tiers',[])}
    options = public_schedule(schedule)['tiers'] if schedule else bound_options
    if schedule and schedule.get('includesManualTiers') and all(tid.startswith('manual-coupon-') for tid in schedule_tiers):
        # A manually added dated coupon accompanies the recognized offers in the same group.
        options = [o for o in bound_options if o['tierId'] not in schedule_tiers] + options
    if len(options)==1 and not options[0].get('startOn'):
        from b_coupon_schedule import beijing_day
        start, end = (beijing_day(values.get(key)) for key in ('startAt','endAt'))
        if start and end:
            options = [options[0] | {'startOn':start.isoformat(), 'endOn':end.isoformat()}]
    options = [dict(o) for o in options]
    materials = set(version.get('materialIds',[]))
    visible = materials if visible_material_ids is None else materials & set(visible_material_ids)
    for option in options:
        tier = schedule_tiers.get(option['tierId'])
        if tier:
            for key in ('dateEvidenceId','evidenceId','source','sourceReference','platformCode','platformLabel'):
                if key in tier:
                    option[key] = tier[key]
        reference = option.get('source') or option.get('sourceReference') or {}
        option['sourceMaterialId'] = reference.get('materialId') or ((schedule or {}).get('materialId') if tier else None)
    options = [o for o in options if not o['sourceMaterialId'] or o['sourceMaterialId'] in visible]
    # Source rows are read only within the current submission, version, group and permitted materials.
    needs_source = any(o['sourceMaterialId'] in visible for o in options)
    analysis, candidate = (bound_candidate(db,version) if include_products and needs_source else (None,None))
    scopes = per_tier_products(analysis,candidate,options)
    candidate_rows = {r['rowId']:r for r in (candidate or {}).get('productRows',[])}
    today = datetime.now(BEIJING).date().isoformat()
    for option in options:
        tier = schedule_tiers.get(option['tierId'],{})
        original = option | tier
        source_schedule = schedule if tier else None
        option.update(funding(original,store_bearing=values.get('storeBearing'),schedule=source_schedule))
        option.pop('fundingNote',None)
        option['sourceTierKey'] = source_key(original,source_schedule)
        state = 'source_unmarked' if option.get('sourceProductsUnmarked') else 'unknown'
        scope = scopes.get(option['tierId'], {'productScopeStatus':state,'sourceProducts':[]})
        if candidate_rows and tier.get('productRowIds'):
            rows = [candidate_rows[rid] for rid in tier['productRowIds'] if rid in candidate_rows]
            scope = {'productScopeStatus':'source_marked' if len(rows)==len(tier['productRowIds']) else 'source_partial',
                     'sourceProducts':[{'rowId':r['rowId'],'upc':r.get('rawUpc'),'name':r.get('rawName'),'rawName':r.get('rawName')} for r in rows]}
        option.update(scope)
        option['productRowIds'] = [p['rowId'] for p in scope['sourceProducts']]
        option['sourceUpcs'] = list(dict.fromkeys(p['upc'] for p in scope['sourceProducts'] if p.get('upc')))
        start, end = option.get('startOn'),option.get('endOn')
        option.update(startDate=start,endDate=end,periodState=(
            'expired' if end and end < today else 'upcoming' if start and today < start
            else 'active' if start and end else 'unknown'))
    mixed = mixed or any(o['storeFundingStatus']=='required' for o in options)
    if decisions is None:
        decisions = [json.loads(row[0]) for row in db.execute(
            'SELECT payload FROM review_decisions WHERE version_id=? ORDER BY rowid', (version['id'],))]
    approvals = [d for d in decisions if d.get('couponReview') and d.get('decision','approve')=='approve']
    visible_ids = [o['tierId'] for o in options]
    def approved_ids(decision):
        chosen = set(decision['couponReview']['selectedCouponIds'])
        return [tid for tid in visible_ids if tid in chosen]

    approved = approved_ids(approvals[-1]) if approvals else None
    mode = 'initial' if approved is None and review_state in (None,'initial_pending') else 'locked'
    eligible = visible_ids if mode=='initial' else []
    stage = (review_state or 'initial_pending').removesuffix('_pending')
    previous_stage = {'pricing_pending':'initial','final_pending':'pricing'}.get(review_state)
    if previous_stage:
        # Historical decisions refer to their task via nodeId. Only the immediately preceding
        # approved task can establish the next stage's editable scope.
        previous_node = db.execute("SELECT id FROM review_tasks WHERE version_id=? AND stage=? AND state='approved'",
                                   (version['id'],previous_stage)).fetchone()
        decision = next((d for d in reversed(approvals) if previous_node and d.get('nodeId')==previous_node[0]),None)
        if decision:
            mode,eligible = 'reduce_only',approved_ids(decision)

    stored = read_selection(db,version['id'])
    draft = stored if mode!='locked' and stored and stored['reviewStage']==stage else None
    if draft:
        draft = draft | {'selectedCouponIds':[tid for tid in draft['selectedCouponIds'] if tid in eligible]}
    selected = eligible if mode=='reduce_only' else (approved or [])
    if draft:
        selected = draft['selectedCouponIds']
    defaults = [o['tierId'] for o in options if o['storeFundingStatus']=='none' and not o.get('sourceProductsUnmarked')]
    return {'options':options, 'selectedCouponIds':selected, 'approvedCouponIds':approved,
            'eligibleCouponIds':eligible, 'selectionMode':mode, 'selectionStage':stage if mode!='locked' else None, 'storeFundingRequired':mixed,
            'selection':draft, 'draftRevision':stored['revision'] if stored else 0, 'hasDraft':draft is not None,
            'defaultSelectedCouponIds':defaults if mode=='initial' and draft is None else [],
            'storeBearingPolicy':'per_tier' if snapshot.get('storeBearingPolicy')=='per_tier' else 'activity',
            'storeBearing':values.get('storeBearing'),
            'storeBearingConfirmed':values.get('storeBearing') in (('yes',) if mixed else ('yes','no')),
            'selectionLocked':mode=='locked'}


def require_eligible_selection(projection, chosen):
    if projection['selectionMode']=='reduce_only' and not set(chosen)<=set(projection['eligibleCouponIds']):
        if projection['selectionStage']=='final':
            fail(409,'COUPON_SELECTION_OUTSIDE_PRICING','终审只能从价盘复审已通过的券档中取消。')
        fail(409,'COUPON_SELECTION_OUTSIDE_INITIAL','价盘复审只能从初审已通过的券档中取消。')


def store_bearing_requirement(review):
    if not review or review['storeBearingPolicy']=='per_tier' or review['storeBearingConfirmed']:
        return None
    if review['storeFundingRequired']:
        return {'field':'storeBearing','code':'MIXED_COUPON_STORE_BEARING_REQUIRED','message':'请由提报人确认混补券的门店出资。'}
    return {'field':'storeBearing','code':'STORE_BEARING_REQUIRED','message':'请由提报人确认满减券的门店出资。'}


def decide_coupons(db, version, body, *, visible_material_ids=None, review_state=None):
    supplied = 'selectedCouponIds' in body
    result = review_projection(db, version, include_products=False, review_state=review_state)
    if result is None or body['decision'] == 'reject':
        if supplied:
            fail(422, 'INVALID_COUPON_SELECTION', '当前审核操作不接受券勾选。')
        return None
    require_visible_options(result, visible_material_ids, version)
    missing=store_bearing_requirement(result)
    if missing:
        fail(409,missing['code'],missing['message'])
    known = [option['tierId'] for option in result['options']]
    if not known:
        fail(409, 'COUPON_OPTIONS_REQUIRED', '本版本缺少可核对的券明细，请补齐后再通过审核。')
    reducing = review_state in ('pricing_pending','final_pending')
    if reducing and result['selectionMode']!='reduce_only':
        fail(409,'COUPON_SELECTION_LOCKED','本版本缺少上一审核节点通过的券档清单，请核对审核记录。')
    if reducing and (supplied or result['hasDraft']) and 'expectedCouponSelectionRevision' not in body:
        fail(422,'INVALID_REQUEST','请刷新并核对当前审核节点的选券草稿版本。')
    if 'expectedCouponSelectionRevision' in body:
        check_revision(body['expectedCouponSelectionRevision'],result['draftRevision'])
    chosen = checked_selection(known,body.get('selectedCouponIds', result['selectedCouponIds']))
    require_eligible_selection(result,chosen)
    if reducing and not chosen:
        fail(409,'COUPON_SELECTION_REQUIRED','通过审核须保留至少一张券。如果全部取消，请填写原因并退回活动。')
    if not chosen:
        fail(409, 'COUPON_SELECTION_REQUIRED', '请在审核详情勾选至少一张拟参加的券。')
    if result['selectionLocked'] and set(chosen) != set(result['selectedCouponIds']):
        fail(409, 'COUPON_SELECTION_LOCKED', '本版本的选券已审核。如需变更，请打回后重新提报。')
    if reducing:
        return {'selectedCouponIds':chosen, 'removedCouponIds':[tid for tid in result['eligibleCouponIds'] if tid not in chosen]}
    return {'selectedCouponIds':chosen}
