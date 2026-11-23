"""Immutable review product snapshots and the same final operations list.

All mutations run inside Domain.command's BEGIN IMMEDIATE / receipt transaction.
Migration is additive and is invoked by the owning application, never on import.
"""
import copy
import csv
import io
import json
import re
from account_primitives import fail, iso
from b_state import canon, fingerprint
from b_activity_method import product_registration_enabled, require_product_registration, require_signup_price
from business_agent.activity_price_evidence_108.evidence import PLAIN_DECIMAL

TABLE = 'activity_registration_reviews'
CONTRACT = 'activity-registration-review/1.0'
SCOPE_ACTION = 'activity_registration_reviews.confirm_scope'


def migrate(db):
    db.execute('''CREATE TABLE IF NOT EXISTS activity_registration_reviews (
        activity_id TEXT NOT NULL, version_id TEXT NOT NULL, platform_code TEXT NOT NULL,
        revision INTEGER NOT NULL CHECK(revision > 0), body TEXT NOT NULL,
        PRIMARY KEY(activity_id,version_id,platform_code))''')


def saved(db, aid, vid, platform):
    row = db.execute(f'SELECT body FROM {TABLE} WHERE activity_id=? AND version_id=? AND platform_code=?', (aid, vid, platform)).fetchone()
    return json.loads(row[0]) if row else None


def store(db, value):
    db.execute(f'''INSERT INTO {TABLE} VALUES(?,?,?,?,?) ON CONFLICT(activity_id,version_id,platform_code)
        DO UPDATE SET revision=excluded.revision,body=excluded.body''',
        (value['activityId'], value['versionId'], value['platformCode'], value['revision'], canon(value)))


def platforms(version):
    snapshot = version.get('snapshot') or {}
    fields = snapshot.get('resolvedFields') or snapshot.get('confirmedFields') or {}
    return snapshot.get('applicablePlatformCodes') or ([fields['platformCode']] if fields.get('platformCode') else [])


def row_key(row):
    return row['candidateId'], row['rowId']

def require_activity_price(row, platform):
    price = row.get('activityPrice')
    price = price if isinstance(price, dict) else {}
    amount = price.get('amountDecimal')
    if (price.get('status') in (None, 'ready', 'needs_review')
            and isinstance(amount, str) and PLAIN_DECIMAL.fullmatch(amount)):
        return
    name = (row.get('productSnapshot') or {}).get('name') or row.get('internalCode') or row['rowId']
    fail(409, 'REGISTRATION_ACTIVITY_PRICE_REQUIRED',
         f'拟报名商品「{name}」缺少可核对的活动价，请在商品明细核对。',
         {'platformCode': platform, 'candidateId': row['candidateId'], 'rowId': row['rowId']})


def list_confirmed_product_sets(db):
    result = []
    for record in db.execute(f'SELECT body FROM {TABLE}'):
        value = json.loads(record[0])
        if value.get('status') != 'finalized':
            continue
        activity = db.execute('SELECT body FROM activities WHERE id=?', (value['activityId'],)).fetchone()
        review = db.execute('SELECT state FROM version_reviews WHERE version_id=?', (value['versionId'],)).fetchone()
        if activity and json.loads(activity[0]).get('approvedVersionId') == value['versionId'] and review and review[0] == 'approved':
            result.append(value)
    return result


def review_decision(domain, db, actor, aid, vid, stage, body, decision_id):
    """Validate and persist atomically before Domain advances the review node."""
    from b_domain import entity, fields
    version = entity(db, 'activity_versions', vid)
    request = body.get('registrationReview')
    if not product_registration_enabled(version):
        if request:
            fail(409, 'ACTIVITY_METHOD_NOT_SUPPORTED', '此活动方式不支持逐商品报名审核')
        return None
    codes = platforms(version)
    if not codes or any(code not in ('meituan', 'taobao_instant', 'jd') for code in codes):
        fail(409, 'REGISTRATION_PLATFORMS_REQUIRED', '请先确认本版本适用平台')
    request = request or {}
    if not isinstance(request, dict) or set(request) - {'platforms', 'removals', 'confirmedStoreScopes'}:
        fail(409, 'REGISTRATION_RESTART_REQUIRED', '终审不能新增或替换商品，请退回后重新选品审核')
    removals = request.get('removals', [])
    if not isinstance(removals, list) or len(removals) > 1000:
        fail(422, 'INVALID_REQUEST', '终审移除记录无效')
    if removals and (stage != 'final' or body['decision'] != 'approve'):
        fail(422, 'INVALID_REQUEST', '仅终审通过时可移除商品')
    scopes = request.get('confirmedStoreScopes', [])
    if not isinstance(scopes, list) or len(scopes) > len(codes) or scopes and (stage != 'final' or body['decision'] != 'approve'):
        fail(422, 'INVALID_REQUEST', '门店范围仅在终审通过时按平台确认')
    scope_inputs = {}
    for entry in scopes:
        fields(entry, ['platformCode', 'scopeText', 'sourceRef'])
        if entry['platformCode'] not in codes or entry['platformCode'] in scope_inputs:
            fail(422, 'INVALID_REQUEST', '门店范围平台重复或未声明')
        validate_scope(entry['scopeText'], entry['sourceRef'])
        scope_inputs[entry['platformCode']] = entry
    snapshots = {code: saved(db, aid, vid, code) for code in codes}
    at, person = iso(domain.clock()), {'id': actor['id'], 'name': actor.get('name') or actor.get('username')}
    event = {'stage': stage, 'decisionId': decision_id, 'decision': body['decision'], 'actor': person,
             'at': at, 'comment': body.get('comment'), 'reason': body.get('reason'), 'optimizationNote': body.get('optimizationNote')}
    if body['decision'] == 'reject':
        for code, value in snapshots.items():
            if value:
                value.update(status='rejected', revision=value['revision'] + 1)
                value['events'].append(event)
                store(db, value)
        return {'contractVersion': CONTRACT, 'status': 'rejected', 'platformCodes': codes}
    if stage == 'initial':
        supplied = request.get('platforms')
        if not isinstance(supplied, list) or len(supplied) != len(codes):
            fail(409, 'REGISTRATION_SNAPSHOT_REQUIRED', '初审须核对各适用平台的最新拟报名清单')
        checked = {}
        for entry in supplied:
            fields(entry, ['platformCode', 'expectedDraftRevision', 'bindingDigest'])
            if (entry['platformCode'] not in codes or entry['platformCode'] in checked or
                    type(entry['expectedDraftRevision']) is not int or not isinstance(entry['bindingDigest'], str)):
                fail(422, 'INVALID_REQUEST', '初审清单绑定无效')
            checked[entry['platformCode']] = entry
        drafts = getattr(domain, 'registration_drafts', None)
        if drafts is None:
            fail(503, 'REGISTRATION_REVIEW_NOT_READY', '报名审核服务尚未就绪')
        total = 0
        for code in codes:
            if snapshots[code]:
                fail(409, 'REGISTRATION_ALREADY_FROZEN', '本版本已有初审商品快照，不能覆盖历史')
            _, _, recognition, digest = drafts.context(db, actor, aid, vid, code)
            record = db.execute('SELECT body FROM activity_registration_drafts WHERE activity_id=? AND version_id=? AND platform_code=?', (aid, vid, code)).fetchone()
            draft = json.loads(record[0]) if record else {'revision': 0, 'rows': [], 'bindingDigest': digest}
            expected = checked[code]
            if draft['revision'] != expected['expectedDraftRevision'] or digest != expected['bindingDigest'] or draft['bindingDigest'] != digest:
                fail(409, 'DRAFT_CONFLICT', '拟报名清单或商品来源已变化，请刷新后重新审核')
            sources = {row_key(row): row for row in recognition['rows']}
            selected = []
            for row in draft['rows']:
                if row.get('decision', 'planned' if row.get('selected') else 'pending') != 'planned':
                    continue
                source = sources.get(row_key(row))
                candidates = [c for c in (source or {}).get('candidates', []) if c.get('internalCode') == row.get('internalCode')]
                if not row.get('internalCode') or len(candidates) != 1:
                    fail(409, 'REGISTRATION_IDENTITY_UNCONFIRMED', '拟报名商品尚未唯一核对到公司内部码')
                require_signup_price(source)
                item = copy.deepcopy(row)
                item.update(productSnapshot=copy.deepcopy(candidates[0]), identityStatus='initial_review_confirmed',
                            identityConfirmedBy=person, identityConfirmedAt=at,
                            activityPrice=copy.deepcopy(source.get('activityPriceCandidate')),
                            agentSupplyPrices=copy.deepcopy(source.get('agentSupplyPrices')),
                            trialMargin=copy.deepcopy((source.get('operatingMetrics') or {}).get('trialMargin')))
                require_activity_price(item, code)
                if source.get('signupPriceCandidate') is not None:
                    item['signupPrice'] = copy.deepcopy(source['signupPriceCandidate'])
                selected.append(item)
            total += len(selected)
            snapshots[code] = {'contractVersion': CONTRACT, 'activityId': aid, 'versionId': vid, 'platformCode': code,
                'activityMethod': version['snapshot']['activityMethod'], 'revision': 1, 'status': 'initial_approved',
                'draftRevision': draft['revision'], 'bindingDigest': digest, 'sourceBinding': recognition['binding'],
                'initialRows': selected, 'finalRows': [], 'removals': [], 'initialDecisionId': decision_id,
                'initialApprovedBy': person, 'initialApprovedAt': at, 'events': [event]}
        if not total:
            fail(409, 'REGISTRATION_SELECTION_REQUIRED', '初审前请至少选好一件拟报名商品')
    else:
        required = 'initial_approved' if stage == 'pricing' else 'pricing_approved'
        if any(not value or value['status'] != required for value in snapshots.values()):
            fail(409, 'REGISTRATION_REVIEW_SEQUENCE', '缺少上一审核节点确认的商品清单')
        if 'platforms' in request:
            # Frozen lists may only be referenced, never replaced with a fresh draft.
            fail(409, 'REGISTRATION_RESTART_REQUIRED', '初审后不能新增或替换商品，请退回后重新选品审核')
        removed = {}
        for entry in removals:
            fields(entry, ['platformCode', 'candidateId', 'rowId', 'reason'])
            if (not all(isinstance(entry[k], str) for k in entry) or
                    entry['platformCode'] not in codes or not entry['reason'].strip() or len(entry['reason']) > 2000):
                fail(422, 'REGISTRATION_REMOVAL_REASON_REQUIRED', '每件移除商品必须填写原因')
            key = (entry['platformCode'], entry['candidateId'], entry['rowId'])
            if key in removed or row_key(entry) not in {row_key(r) for r in snapshots[entry['platformCode']]['initialRows']}:
                fail(409, 'REGISTRATION_RESTART_REQUIRED', '移除商品不属于初审清单，新增或替换须退回重审')
            removed[key] = entry | {'actor': person, 'at': at, 'decisionId': decision_id}
        retained_by_platform = {
            code: [row for row in value['initialRows'] if (code, *row_key(row)) not in removed]
            for code, value in snapshots.items()
        }
        if stage == 'final' and not any(retained_by_platform.values()):
            fail(409, 'REGISTRATION_FINAL_SELECTION_REQUIRED', '终审通过前须至少保留一件拟报名商品。全部不报名时可退回。')
        for code, value in snapshots.items():
            retained = retained_by_platform[code]
            for row in retained:
                require_activity_price(row, code)
            value['revision'] += 1
            value['events'].append(event)
            if stage == 'pricing':
                value.update(status='pricing_approved', pricingDecisionId=decision_id, pricingApprovedBy=person, pricingApprovedAt=at)
            else:
                final_rows = copy.deepcopy(retained)
                value.update(status='finalized', finalRows=final_rows,
                    removals=[r for key, r in removed.items() if key[0] == code],
                    finalDecisionId=decision_id, finalizedBy=person, finalizedAt=at)
                if code in scope_inputs:
                    value['confirmedStoreScope'] = scope_inputs[code] | {'status': 'confirmed', 'basis': 'manual_scope_declaration', 'confirmedBy': person, 'confirmedAt': at}
                value['finalDigest'] = fingerprint({'activityId': aid, 'versionId': vid, 'platformCode': code, 'rows': final_rows})
    for value in snapshots.values():
        store(db, value)
    return {'contractVersion': CONTRACT, 'status': snapshots[codes[0]]['status'], 'platformCodes': codes,
            'finalDigests': {code: value.get('finalDigest') for code, value in snapshots.items()}}



def price_projection(raw):
    """Normalize a display/export unit without changing frozen price evidence.

The Agent's known single_item basis means one piece. A bundle is never guessed.
Legacy explicit pricingUnit remains authoritative when no unit conflict exists.
"""
    price = copy.deepcopy(raw) if isinstance(raw, dict) else {}
    unit = price.get('pricingUnit')
    absent = {'', '-', '—', 'unknown', 'unavailable', 'not_applicable', 'n/a', '待核实', '未知', '未提供', '不适用'}
    explicit = isinstance(unit, str) and unit.strip().casefold() not in absent
    issues = price.get('issues') or []
    unit_conflict = price.get('unitStatus') == 'conflict' or 'QUANTITY_BASIS_CONFLICT' in issues
    inferred = False
    if (not explicit and not unit_conflict and price.get('unitStatus') == 'known'
            and price.get('quantityBasis') == 'single_item'):
        inferred = True
        price['pricingUnit'] = '件'
        price['pricingUnitRuleVersion'] = 'known-single-item-to-piece/1.0'
    known_unit = (explicit or inferred) and not unit_conflict
    currency = price.get('currency')
    known_currency = (isinstance(currency, str) and re.fullmatch(r'[A-Z]{3}', currency) is not None
                      and currency not in ('UNK', 'XXX', 'XTS')
                      and not any(issue in issues for issue in ('CURRENCY_UNSPECIFIED', 'CURRENCY_CONFLICT')))
    return price, known_currency, known_unit

def formal_list(db, activity, version, platform):
    value = saved(db, activity['id'], version['id'], platform)
    reasons = []
    review = db.execute('SELECT state FROM version_reviews WHERE version_id=?', (version['id'],)).fetchone()
    if not value or value.get('status') != 'finalized' or activity.get('approvedVersionId') != version['id'] or not review or review[0] != 'approved':
        reasons.append('活动版本尚未完成终审确认清单')
    scope = (value or {}).get('confirmedStoreScope') or version.get('snapshot', {}).get('confirmedStoreScope')
    if not isinstance(scope, dict) or scope.get('status') != 'confirmed' or not (scope.get('scopeText') or scope.get('storeIds')):
        reasons.append('尚未确认适用门店范围')
    final_rows = copy.deepcopy((value or {}).get('finalRows', []))
    missing_fields = []
    if value and value.get('status') == 'finalized' and not final_rows:
        reasons.append('此平台最终确认清单为空')
    for row in final_rows:
        if not row.get('internalCode') or not (row.get('productSnapshot') or {}).get('spec'):
            reasons.append('最终清单缺少内部码或规格')
        price, currency_known, unit_known = price_projection(row.get('activityPrice'))
        row['activityPrice'] = price
        if price.get('amountDecimal') in (None, '') or isinstance(price.get('amountDecimal'), bool):
            reasons.append('最终清单缺少活动价')
        identity = {key: row.get(key) for key in ('candidateId', 'rowId', 'internalCode')}
        label = row.get('internalCode') or row.get('rowId') or '未核商品'
        if not currency_known:
            reasons.append(f'最终清单缺少已确认币种（{label}）')
            missing_fields.append(identity | {'field': 'currency', 'reasonCode': 'ACTIVITY_CURRENCY_UNCONFIRMED'})
        if not unit_known:
            reasons.append(f'最终清单缺少已确认计价单位（{label}）')
            missing_fields.append(identity | {'field': 'pricingUnit', 'reasonCode': 'ACTIVITY_PRICE_UNIT_UNCONFIRMED'})
    return {'available': not reasons, 'reasons': list(dict.fromkeys(reasons)),
            'revision': (value or {}).get('revision', 0), 'finalDigest': (value or {}).get('finalDigest'),
            'rows': final_rows, 'confirmedStoreScope': scope, 'missingFields': missing_fields}


def validate_scope(scope_text, source):
    if any(not isinstance(value, str) or not value.strip() or len(value) > 2000 for value in (scope_text, source)):
        fail(422, 'INVALID_REQUEST', '须提供已确认门店范围和可回查的确认依据')


def confirm_scope(drafts, db, actor, aid, vid, platform, body):
    from b_domain import fields
    from b_registration_draft import authorize
    activity, version = authorize(drafts.auth, db, actor, aid, vid, platform)
    drafts.auth.require(db, actor, 'platform_materials.write', aid + ':' + platform)
    fields(body, ['expectedRevision', 'scopeText', 'sourceRef'])
    if type(body['expectedRevision']) is not int:
        fail(422, 'INVALID_REQUEST', '清单版本无效')
    validate_scope(body['scopeText'], body['sourceRef'])
    value = saved(db, aid, vid, platform)
    if not value or value.get('status') != 'finalized' or activity.get('approvedVersionId') != vid:
        fail(409, 'REGISTRATION_FINAL_REQUIRED', '须先完成当前版本终审')
    if value['revision'] != body['expectedRevision']:
        fail(409, 'STATE_CONFLICT', '最终清单或门店范围已变化，请刷新核对')
    person, at = {'id': actor['id'], 'name': actor.get('name') or actor.get('username')}, iso(drafts.clock())
    scope = {'status': 'confirmed', 'scopeText': body['scopeText'].strip(), 'sourceRef': body['sourceRef'].strip(), 'basis': 'manual_scope_declaration', 'confirmedBy': person, 'confirmedAt': at}
    value['events'].append({'event': 'store_scope_confirmed', 'actor': person, 'at': at, 'previousScope': value.get('confirmedStoreScope'), 'scope': scope})
    value.update(confirmedStoreScope=scope, revision=value['revision'] + 1)
    store(db, value)
    return 200, {'activityId': aid, 'versionId': vid, 'platformCode': platform,
                 'revision': value['revision'], 'formalList': formal_list(db, activity, version, platform)}


def download(drafts, db, actor, aid, vid, platform):
    from b_registration_draft import authorize
    activity, version = authorize(drafts.auth, db, actor, aid, vid, platform)
    drafts.auth.require(db, actor, 'platform_materials.write', aid + ':' + platform)
    drafts.auth.require(db, actor, 'operations.download', aid)
    from b_domain import can_material
    value = saved(db, aid, vid, platform)
    if value and any(not can_material(db, actor, source['materialId'], drafts.auth) for source in value.get('sourceBinding', {}).get('materials', [])):
        fail(403, 'FORBIDDEN', '当前账号无法下载清单原始依据')
    result = formal_list(db, activity, version, platform)
    if not result['available']:
        fail(409, 'REGISTRATION_LIST_INCOMPLETE', '待报名清单尚不能下载', {'reasons': result['reasons'], 'missingFields': result['missingFields']})
    output = io.StringIO(newline='')
    writer = csv.writer(output)
    writer.writerow(['活动ID', '版本ID', '适用平台', '内部码', '商品名称', '规格', '活动价', '币种', '计价单位', '已确认门店范围', '终审清单摘要'])
    def safe(value):
        text = '' if value is None else str(value)
        return "'" + text if text.startswith(('=', '+', '-', '@', '\t', '\r')) else text
    for row in result['rows']:
        product, price = row['productSnapshot'], row['activityPrice']
        writer.writerow([safe(v) for v in (aid, vid, platform, row['internalCode'], product.get('name'), product.get('spec'),
            price.get('amountDecimal'), price['currency'], price['pricingUnit'],
            result['confirmedStoreScope'].get('scopeText') or '|'.join(result['confirmedStoreScope'].get('storeIds', [])), result['finalDigest'])])
    return output.getvalue().encode('utf-8-sig'), f'{aid}-{vid}-{platform}-内部待报名清单.csv'
