"""Shared, version/platform scoped selections; no changes to approved product scope."""
import json
import re
from account_primitives import APIError, fail, iso
from b_state import canon, fingerprint
from b_activity_method import product_registration_enabled, require_product_registration, require_signup_price
import b_registration_review as registration_review
from b_domain import can_material, can_version, entity, fields

ACTION = 'activity_registration_drafts.save'
ROW_ACTION = 'activity_registration_drafts.decide'
CONTRACT = 'activity-registration-draft/2.0'
ROUTE = re.compile(r'/activities/([A-Za-z0-9_-]{1,128})/versions/([A-Za-z0-9_-]{1,128})/registration-drafts/(meituan|taobao_instant|jd)')
ROW_ROUTE = re.compile(ROUTE.pattern + r'/decisions')
TABLE = 'activity_registration_drafts'


def normalized_rows(saved):
    # Legacy unchecked means undecided, never an explicit refusal. Keep the old
    # evidence without treating the new decision as an identity confirmation.
    return [item | {'decision': item.get('decision', 'planned' if item['selected'] else 'pending'),
                    'rowRevision': item.get('rowRevision', saved['revision'])}
            for item in (saved or {}).get('rows', [])]


def migrate(db):
    db.execute('''CREATE TABLE IF NOT EXISTS activity_registration_drafts (
        activity_id TEXT NOT NULL, version_id TEXT NOT NULL, platform_code TEXT NOT NULL,
        revision INTEGER NOT NULL CHECK(revision > 0), body TEXT NOT NULL,
        PRIMARY KEY(activity_id, version_id, platform_code))''')


def authorize(auth, db, actor, aid, vid, platform, write=False):
    auth.require(db, actor, 'operations.read', aid)
    if write:
        auth.require(db, actor, 'platform_materials.write', aid + ':' + platform)
    activity = entity(db, 'activities', aid)
    version = entity(db, 'activity_versions', vid)
    if version['activityId'] != aid or not can_version(db, actor, vid, auth):
        fail(404, 'NOT_FOUND', '版本不存在或不可访问')
    snapshot = version.get('snapshot') or {}
    codes = snapshot.get('applicablePlatformCodes') or []
    confirmed = snapshot.get('resolvedFields') or snapshot.get('confirmedFields') or {}
    if not codes and confirmed.get('platformCode'):
        codes = [confirmed['platformCode']]
    if platform not in codes:
        fail(422 if write else 404, 'PLATFORM_NOT_SELECTED', '该版本未声明此适用平台')
    if write:
        require_product_registration(version)
        if (activity.get('reviewVersionId') or activity.get('currentReviewVersionId')) != vid:
            fail(409, 'REVIEW_VERSION_NOT_CURRENT', '只能修改当前审核版本的拟报名选择')
        frozen = registration_review.saved(db, aid, vid, platform)
        if frozen:
            fail(409, 'REGISTRATION_ALREADY_FROZEN', '初审已冻结商品清单，新增或替换须退回重新审核')
        review = db.execute('SELECT state FROM version_reviews WHERE version_id=?', (vid,)).fetchone()
        if review and review[0] not in ('not_started', 'initial_pending'):
            fail(409, 'REGISTRATION_ALREADY_FROZEN', '此审核状态不能修改拟报名选择')
    return activity, version


def receipt(auth, db, actor, row):
    payload = json.loads(row['response'])
    authorize(auth, db, actor, payload['activityId'], payload['versionId'], payload['platformCode'])
    auth.require(db, actor, 'platform_materials.write', payload['activityId'] + ':' + payload['platformCode'])
    if any(not can_material(db, actor, source['materialId'], auth) for source in payload.get('sourceBinding', {}).get('materials', [])):
        fail(403, 'FORBIDDEN', '原件访问权限已变化，无法读取此草稿回执')


class RegistrationDrafts:
    def __init__(self, business):
        self.business = business
        self.auth = business.auth
        self.clock = business.clock
        business.domain.registration_drafts = self

    def context(self, db, actor, aid, vid, platform, write=False):
        activity, version = authorize(self.auth, db, actor, aid, vid, platform, write)
        load_version = getattr(self.business, 'registration_version', self.business.version)
        recognized = load_version(db, actor, aid, vid).get('productRecognition') or {}
        if recognized.get('status') != 'ready':
            fail(409, 'RECOGNITION_UNAVAILABLE', '商品识别暂不可用，请稍后重新读取')
        digest = fingerprint({'binding': recognized['binding'], 'analysis': recognized['derivedAnalysisDigest']})
        return activity, version, recognized, digest

    def read(self, db, actor, aid, vid, platform):
        activity, version = authorize(self.auth, db, actor, aid, vid, platform)
        frozen = registration_review.saved(db, aid, vid, platform)
        if frozen:
            if any(not can_material(db, actor, source['materialId'], self.auth) for source in frozen.get('sourceBinding', {}).get('materials', [])):
                fail(403, 'FORBIDDEN', '当前账号无法读取清单原始依据')
            digest = frozen['bindingDigest']
        else:
            _, _, recognition, digest = self.context(db, actor, aid, vid, platform)
        row = db.execute(f'SELECT body FROM {TABLE} WHERE activity_id=? AND version_id=? AND platform_code=?', (aid, vid, platform)).fetchone()
        saved = json.loads(row[0]) if row else None
        editable = True
        try:
            self.auth.require(db, actor, 'platform_materials.write', aid + ':' + platform)
        except APIError as error:
            if error.status != 403:
                raise
            editable = False
        can_manage_final = editable
        frozen = registration_review.saved(db, aid, vid, platform)
        enabled = product_registration_enabled(version)
        editable = editable and enabled and not frozen and (activity.get('reviewVersionId') or activity.get('currentReviewVersionId')) == vid
        review = db.execute('SELECT state FROM version_reviews WHERE version_id=?', (vid,)).fetchone()
        editable = editable and bool(review and review[0] in ('not_started', 'initial_pending'))
        return {'contractVersion': CONTRACT, 'activityId': aid, 'versionId': vid, 'platformCode': platform,
                'revision': saved['revision'] if saved else 0, 'bindingDigest': digest,
                'canEdit': editable, 'stale': bool(saved and saved['bindingDigest'] != digest),
                'rows': normalized_rows(saved), 'updatedBy': saved['updatedBy'] if saved else None,
                'updatedAt': saved['updatedAt'] if saved else None,
                'activityMethod': version.get('snapshot', {}).get('activityMethod'),
                'methodSupported': enabled, 'reviewSnapshot': frozen, 'canManageFinal': can_manage_final,
                'formalList': registration_review.formal_list(db, activity, version, platform)}

    def save(self, db, actor, aid, vid, platform, body):
        fields(body, ['expectedRevision', 'bindingDigest', 'rows'])
        if type(body['expectedRevision']) is not int or body['expectedRevision'] < 0 or not isinstance(body['bindingDigest'], str):
            fail(422, 'INVALID_REQUEST', '草稿版本或来源摘要无效')
        if not isinstance(body['rows'], list) or len(body['rows']) > 1000:
            fail(422, 'INVALID_REQUEST', '草稿商品行无效或过多')
        _, _, recognized, digest = self.context(db, actor, aid, vid, platform, True)
        row = db.execute(f'SELECT body FROM {TABLE} WHERE activity_id=? AND version_id=? AND platform_code=?', (aid, vid, platform)).fetchone()
        old = json.loads(row[0]) if row else None
        revision = old['revision'] if old else 0
        if body['expectedRevision'] != revision:
            fail(409, 'DRAFT_CONFLICT', '草稿已被更新，请载入最新草稿后核对。本页选择已保留。', {'currentRevision': revision})
        if old and old.get('decisionContract') == CONTRACT:
            fail(409, 'DRAFT_CLIENT_UPGRADE_REQUIRED', '商品状态已启用逐行保存，请刷新页面后操作')
        if body['bindingDigest'] != digest:
            fail(409, 'SOURCE_CHANGED', '附件或目录依据已更新，请刷新后重新核对商品')
        available = {(r['candidateId'], r['rowId']): r for r in recognized['rows']}
        previous = {(r['candidateId'], r['rowId']): r for r in (old or {}).get('rows', [])} if old and old['bindingDigest'] == digest else {}
        rows, seen = [], set()
        for item in body['rows']:
            fields(item, ['candidateId', 'rowId', 'internalCode', 'selected'])
            if any(not isinstance(item[k], str) or not item[k] or len(item[k]) > 200 for k in ['candidateId', 'rowId', 'internalCode']) or type(item['selected']) is not bool:
                fail(422, 'INVALID_REQUEST', '已核对商品字段无效')
            key = item['candidateId'], item['rowId']
            if key in seen or key not in available:
                fail(422, 'INVALID_CANDIDATE', '商品行重复或不属于本次识别')
            seen.add(key)
            source = available[key]
            if item['selected']:
                require_signup_price(source)
            candidates = [c for c in source['candidates'] if c['internalCode'] == item['internalCode']]
            if len(candidates) != 1:
                fail(422, 'INVALID_CANDIDATE', '所选内部码不属于该行目录候选')
            prior = previous.get(key)
            same = prior and prior['internalCode'] == item['internalCode']
            rows.append(item | {'identityStatus': 'manually_checked_in_draft',
                'checkedBy': prior['checkedBy'] if same else {'id': actor['id'], 'name': actor['name'] or actor['username']},
                'checkedAt': prior['checkedAt'] if same else iso(self.clock()),
                'productSnapshot': candidates[0], 'sourceRow': {k: source.get(k) for k in ['rawName', 'rawUpc', 'rawInternalCode', 'rawSpec', 'sourceReferences', 'activityPriceCandidate']}})
        saved = {'activityId': aid, 'versionId': vid, 'platformCode': platform, 'revision': revision + 1,
                 'bindingDigest': digest, 'sourceBinding': recognized['binding'], 'rows': rows,
                 'updatedBy': {'id': actor['id'], 'name': actor['name'] or actor['username']}, 'updatedAt': iso(self.clock())}
        db.execute(f'INSERT INTO {TABLE} VALUES(?,?,?,?,?) ON CONFLICT(activity_id,version_id,platform_code) DO UPDATE SET revision=excluded.revision,body=excluded.body',
                   (aid, vid, platform, revision + 1, canon(saved)))
        return 200, saved | {'contractVersion': CONTRACT}

    def decide(self, db, actor, aid, vid, platform, body):
        fields(body, ['expectedRowRevision', 'bindingDigest', 'candidateId', 'rowId', 'decision'])
        if (type(body['expectedRowRevision']) is not int or body['expectedRowRevision'] < 0 or
                any(not isinstance(body[k], str) or not body[k] or len(body[k]) > 200
                    for k in ('bindingDigest', 'candidateId', 'rowId')) or
                body['decision'] not in ('pending', 'planned', 'declined')):
            fail(422, 'INVALID_REQUEST', '商品行、版本或报名状态无效')
        _, _, recognized, digest = self.context(db, actor, aid, vid, platform, True)
        record = db.execute(f'SELECT body FROM {TABLE} WHERE activity_id=? AND version_id=? AND platform_code=?',
                            (aid, vid, platform)).fetchone()
        old = json.loads(record[0]) if record else None
        if body['bindingDigest'] != digest or old and old['bindingDigest'] != digest:
            fail(409, 'SOURCE_CHANGED', '商品来源已变化，请刷新；原报名状态仍保留，不能自动套用到新来源')
        key = body['candidateId'], body['rowId']
        sources = [r for r in recognized['rows'] if (r['candidateId'], r['rowId']) == key]
        if len(sources) != 1 or (body['decision'] == 'planned' and not sources[0].get('candidates')):
            fail(422, 'INVALID_CANDIDATE', '该行不属于本次商品候选')
        source = sources[0]
        if body['decision'] == 'planned':
            require_signup_price(source)
        if body['decision'] == 'planned' and len(source['candidates']) != 1:
            fail(409, 'REGISTRATION_IDENTITY_UNCONFIRMED', '该商品尚未唯一匹配内部码，请先核对身份')
        rows = normalized_rows(old)
        prior = next((r for r in rows if (r['candidateId'], r['rowId']) == key), None)
        row_revision = prior['rowRevision'] if prior else 0
        if body['expectedRowRevision'] != row_revision:
            fail(409, 'DRAFT_ROW_CONFLICT', '此商品已被其他人更新，请查看最新状态后再操作',
                 {'currentRowRevision': row_revision})
        actor_info = {'id': actor['id'], 'name': actor['name'] or actor['username']}
        now = iso(self.clock())
        row = {**(prior or {}), 'candidateId': key[0], 'rowId': key[1],
               'decision': body['decision'], 'selected': body['decision'] == 'planned',
               'rowRevision': row_revision + 1, 'decidedBy': actor_info, 'decidedAt': now}
        if prior is None:
            row.update(internalCode=source['candidates'][0]['internalCode'] if len(source['candidates']) == 1 else None,
                       identityStatus='candidate_not_adopted',
                       productSnapshot=source['candidates'][0] if len(source['candidates']) == 1 else None,
                       sourceRow={k: source.get(k) for k in ['rawName', 'rawUpc', 'rawInternalCode', 'rawSpec', 'sourceReferences', 'activityPriceCandidate']})
        rows = [r for r in rows if (r['candidateId'], r['rowId']) != key] + [row]
        revision = (old['revision'] if old else 0) + 1
        saved = {'activityId': aid, 'versionId': vid, 'platformCode': platform, 'revision': revision,
                 'decisionContract': CONTRACT, 'bindingDigest': digest, 'sourceBinding': recognized['binding'],
                 'rows': rows, 'updatedBy': actor_info, 'updatedAt': now}
        # The enclosing command transaction holds BEGIN IMMEDIATE. Read/merge/write
        # and the idempotent receipt commit atomically; unrelated rows are retained.
        db.execute(f'INSERT INTO {TABLE} VALUES(?,?,?,?,?) ON CONFLICT(activity_id,version_id,platform_code) DO UPDATE SET revision=excluded.revision,body=excluded.body',
                   (aid, vid, platform, revision, canon(saved)))
        return 200, {k: v for k, v in saved.items() if k != 'rows'} | {'row': row, 'contractVersion': CONTRACT}
