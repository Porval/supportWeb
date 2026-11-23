"""Platform material revisions and read projections for the approved-version/platform submission and verification loop."""
import hashlib
import json
import re
import unicodedata
from urllib.parse import quote

from account_primitives import fail, iso
from b_domain import can_version, entity, fields, grant_scopes, new_id, upload_payload
from b_platform_comparison import FILE_LIMIT, FORMATS, JSON_LIMIT, MATCH_RULE, compare, parse_file, confirmed_comparison_inputs
from b_state import canon
from queries import pagination, validate

CONTRACT = 'platform-materials/1.0'
ACTION = 'platform_materials.submit'
VERIFY_ACTION = 'platform_materials.verify'
RECHECK_ACTION = 'platform_materials.recheck'
REVIEW_ACTIONS = {VERIFY_ACTION, RECHECK_ACTION}
ACTIONS = {ACTION, *REVIEW_ACTIONS}
PERMISSION = 'platform_materials.write'
PLATFORMS = ('meituan', 'taobao_instant')
TABLE = 'activity_platform_material_revisions'
MARKER = 'platform_materials_schema'
APPROVED_SCOPE_MEDIA = 'application/vnd.jiatan.approved-scope+json'
DDL = """CREATE TABLE activity_platform_material_revisions(
    id TEXT PRIMARY KEY NOT NULL,
    activity_id TEXT NOT NULL REFERENCES activities(id),
    platform_code TEXT NOT NULL CHECK(platform_code IN ('meituan','taobao_instant')),
    revision INTEGER NOT NULL CHECK(revision>0),
    approved_version_id TEXT NOT NULL REFERENCES activity_versions(id),
    review_revision INTEGER NOT NULL CHECK(review_revision>0),
    platform_activity_id TEXT NOT NULL,
    scope_mode TEXT NOT NULL CHECK(scope_mode IN ('list','all_brand_products')),
    brands TEXT NOT NULL,
    submitter_id TEXT NOT NULL REFERENCES account(id),
    submitter_name TEXT,
    submitter_role TEXT,
    submitted_at REAL NOT NULL,
    command_id TEXT NOT NULL UNIQUE REFERENCES commands(id) DEFERRABLE INITIALLY DEFERRED,
    filename TEXT,
    media_type TEXT,
    byte_size INTEGER,
    file_sha256 TEXT,
    content BLOB,
    comparison_summary TEXT NOT NULL,
    comparison TEXT NOT NULL,
    UNIQUE(activity_id,platform_code,revision),
    CHECK((scope_mode='list' AND filename IS NOT NULL AND media_type IS NOT NULL
           AND byte_size IS NOT NULL AND byte_size=length(content) AND file_sha256 IS NOT NULL AND content IS NOT NULL)
          OR (scope_mode='all_brand_products' AND filename IS NULL AND media_type IS NULL
              AND byte_size IS NULL AND file_sha256 IS NULL AND content IS NULL))
)"""
COLUMNS = ('id,activity_id,platform_code,revision,approved_version_id,review_revision,platform_activity_id,'
           'scope_mode,brands,submitter_id,submitter_name,submitter_role,submitted_at,command_id,'
           'filename,media_type,byte_size,file_sha256,comparison_summary')
STATES = {'initial_pending', 'pricing_pending', 'final_pending', 'rejected',
          'pending_materials', 'pending_verification', 'returned', 'live', 'unknown', 'blocked_review'}
LIFECYCLE_ORDER = ('pending_registration', 'pending_check', 'pending_recheck', 'retrospective')
REASONS = {
    'REVIEW_NOT_APPROVED': '当前版本尚未审核通过',
    'REVIEW_BASIS_UNAVAILABLE': '缺少可核验的当前审核依据',
    'PLATFORM_BASIS_UNAVAILABLE': '活动与审核版的平台或品牌范围无法一致核对',
    'EXECUTION_VERSION_UNCONFIRMED': '当前执行版与通过版不一致',
    'LIVE_CHANGE_POLICY_UNCONFIRMED': '已上线活动的资料变更规则待确认',
    'MATERIAL_SCOPE_DENIED': '当前账号没有绑定审核版本的读取权限',
    'FORBIDDEN': '当前账号没有本平台操作权限',
    'ASSIGNMENT_REQUIRED': '尚未分配本平台运营负责人',
    'PLATFORM_OPERATOR_REQUIRED': '请由本平台原提交人或现有效负责人补齐资料',
    'VERIFICATION_PENDING': '报名资料已提交，请等待管理员检查',
    'RECHECK_PENDING': '请等待管理员复查',
}
ID = r'[A-Za-z0-9_-]{1,128}'
ROUTE = re.compile(rf'/activities/({ID})/platforms/({ID})(?:/materials(?:/({ID})/(comparison|download|check-screenshot))?)?\Z')
SUBMIT = re.compile(rf'/activities/({ID})/platforms/({ID})/materials\Z')

VERIFY = re.compile(rf'/activities/({ID})/platforms/({ID})/verification\Z')
VERIFY_TABLE = 'activity_platform_verifications'
VERIFY_MARKER = 'platform_verifications_schema'
VERIFY_DDL = """CREATE TABLE activity_platform_verifications(
    id TEXT PRIMARY KEY NOT NULL,
    activity_id TEXT NOT NULL REFERENCES activities(id),
    platform_code TEXT NOT NULL CHECK(platform_code IN ('meituan','taobao_instant')),
    approved_version_id TEXT NOT NULL REFERENCES activity_versions(id),
    platform_material_id TEXT NOT NULL UNIQUE REFERENCES activity_platform_material_revisions(id),
    platform_revision INTEGER NOT NULL CHECK(platform_revision>0),
    decision TEXT NOT NULL CHECK(decision IN ('approve','return')),
    platform_status TEXT NOT NULL CHECK(platform_status IN ('live','not_live','unknown')),
    checks TEXT NOT NULL,
    evidence_source TEXT NOT NULL,
    reason TEXT NOT NULL,
    reviewer_id TEXT NOT NULL REFERENCES account(id),
    reviewer_name TEXT,
    reviewer_role TEXT,
    reviewed_at REAL NOT NULL,
    command_id TEXT NOT NULL UNIQUE REFERENCES commands(id) DEFERRABLE INITIALLY DEFERRED,
    CHECK(decision!='approve' OR (platform_status='live' AND length(trim(evidence_source))>0)),
    CHECK(decision!='return' OR length(trim(reason))>0)
)"""

RECHECK_TABLE = 'activity_platform_rechecks'
RECHECK_MARKER = 'platform_rechecks_schema'
RECHECK_DDL = VERIFY_DDL.replace(VERIFY_TABLE, RECHECK_TABLE, 1).replace(
    'platform_material_id TEXT NOT NULL UNIQUE', 'platform_material_id TEXT NOT NULL', 1)
SCREENSHOT_TYPES = ('image/png', 'image/jpeg', 'image/webp')
SCREENSHOT_TABLE = 'activity_platform_check_screenshots'
SCREENSHOT_DDL = f"""CREATE TABLE {SCREENSHOT_TABLE}(
    verification_id TEXT PRIMARY KEY NOT NULL REFERENCES {VERIFY_TABLE}(id),
    filename TEXT NOT NULL,
    media_type TEXT NOT NULL CHECK(media_type IN ('image/png','image/jpeg','image/webp')),
    sha256 TEXT NOT NULL,
    content BLOB NOT NULL CHECK(length(content)>0 AND length(content)<={FILE_LIMIT})
)"""


def recheck_schema_ready(db):
    marker = db.execute('SELECT value FROM b_meta WHERE key=?', (RECHECK_MARKER,)).fetchone()
    table = db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (RECHECK_TABLE,)).fetchone()
    if marker is None and table is None:
        return False
    if not marker or marker[0] != '1' or not table or table[0] != RECHECK_DDL:
        raise ValueError('Unexpected platform recheck schema')
    return True


def verification_schema_ready(db):
    marker = db.execute('SELECT value FROM b_meta WHERE key=?', (VERIFY_MARKER,)).fetchone()
    table = db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (VERIFY_TABLE,)).fetchone()
    if marker is None and table is None:
        return False
    if not marker or marker[0] != '1' or not table or table[0] != VERIFY_DDL:
        raise ValueError('Unexpected platform verification schema')
    return True


def schema_ready(db):
    marker = db.execute('SELECT value FROM b_meta WHERE key=?', (MARKER,)).fetchone()
    table = db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (TABLE,)).fetchone()
    if marker is None and table is None:
        return False
    if not marker or marker[0] != '1' or not table or table[0] != DDL:
        raise ValueError('Unexpected platform materials schema')
    return True


def migrate(db):
    """Additive, transaction-owned migration; never rewrites existing material evidence."""
    changed = False
    if not schema_ready(db):
        db.execute(DDL)
        db.execute('INSERT INTO b_meta VALUES(?,?)', (MARKER, '1'))
        changed = True
    if not verification_schema_ready(db):
        db.execute(VERIFY_DDL)
        db.execute('INSERT INTO b_meta VALUES(?,?)', (VERIFY_MARKER, '1'))
        changed = True
    if not recheck_schema_ready(db):
        db.execute(RECHECK_DDL)
        db.execute(f'CREATE INDEX platform_rechecks_material ON {RECHECK_TABLE}(platform_material_id)')
        db.execute('INSERT INTO b_meta VALUES(?,?)', (RECHECK_MARKER, '1'))
        changed = True
    screenshot_table = db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                                  (SCREENSHOT_TABLE,)).fetchone()
    if screenshot_table is None:
        db.execute(SCREENSHOT_DDL)
        changed = True
    elif screenshot_table[0] != SCREENSHOT_DDL:
        raise ValueError('Unexpected platform check screenshot schema')
    if (not schema_ready(db) or not verification_schema_ready(db) or not recheck_schema_ready(db)
            or db.execute('PRAGMA foreign_key_check').fetchone()):
        raise ValueError('Platform materials schema verification failed')
    return changed


def selected_platforms(activity):
    codes = activity.get('applicablePlatformCodes')
    if not isinstance(codes, list):
        codes = [activity.get('platformCode')]
    return list(dict.fromkeys(code for code in codes if isinstance(code, str) and code in PLATFORMS))


def selected_platform(activity):
    """Compatibility helper for callers that still render a primary platform."""
    codes = selected_platforms(activity)
    return codes[0] if codes else None


def verified_scopes(db):
    """Read-only identity evidence for current approved versions; no brand-name matching."""
    if not verification_schema_ready(db):
        return []
    result = []
    query = f"""SELECT v.*,m.scope_mode,m.brands,m.comparison,a.body AS activity_body
                FROM {VERIFY_TABLE} v JOIN {TABLE} m ON m.id=v.platform_material_id
                JOIN activities a ON a.id=v.activity_id WHERE v.decision='approve'
                AND m.revision=(SELECT MAX(newer.revision) FROM {TABLE} newer
                                WHERE newer.activity_id=m.activity_id AND newer.platform_code=m.platform_code)"""
    for row in db.execute(query):
        activity = json.loads(row['activity_body'])
        if (activity.get('approvedVersionId') != row['approved_version_id']
                or activity.get('reviewVersionId', activity.get('currentReviewVersionId')) != row['approved_version_id']):
            continue
        review = db.execute('SELECT state FROM version_reviews WHERE version_id=? AND activity_id=?',
                            (row['approved_version_id'], row['activity_id'])).fetchone()
        if not review or review[0] != 'approved':
            continue
        comparison = json.loads(row['comparison'])
        matched = [item for item in comparison.get('rows', [])
                   if item.get('platformPresence') == 'present' and (item.get('productId') or item.get('internalCodeSnapshot'))]
        result.append({'activityId': row['activity_id'], 'approvedVersionId': row['approved_version_id'],
                       'platformCode': row['platform_code'], 'scopeMode': row['scope_mode'],
                       'brandIds': [brand['id'] for brand in json.loads(row['brands'])],
                       'platformMaterialId': row['platform_material_id'], 'decisionId': row['id'],
                       'verifiedAt': iso(row['reviewed_at']),
                       'productIds': sorted({item['productId'] for item in matched if item.get('productId')}),
                       'internalCodes': sorted({item['internalCodeSnapshot'] for item in matched if item.get('internalCodeSnapshot')}),
                       'scopeNotEnumerated': row['scope_mode'] == 'all_brand_products',
                       'scopeComplete': comparison.get('status') == 'complete',
                       'comparisonStatus': comparison.get('status')})
    return result


def block(code):
    return {'code': code, 'message': REASONS[code]}


class PlatformMaterials:
    def __init__(self, auth, clock, decode, prefix, *, operator_resolver=None):
        self.auth, self.clock, self.decode, self.prefix = auth, clock, decode, prefix
        self.operator_resolver = operator_resolver

    @staticmethod
    def capability(enabled):
        return {'contractVersion': CONTRACT, 'enabled': enabled, 'singlePlatform': False, 'independentPlatforms': True,
                'platformCodes': list(PLATFORMS), 'verificationEnabled': enabled, 'verificationAction': VERIFY_ACTION,
                'lifecycleEnabled': enabled, 'checkScreenshotRequired': enabled,
                'upload': {'extensions': list(FORMATS), 'maxDecodedBytes': FILE_LIMIT, 'maxJsonBytes': JSON_LIMIT}}

    def authorize(self, db, actor, aid, code, *, write=False, selected=True):
        resource = aid + ':' + code
        if write:
            self.auth.require(db, actor, PERMISSION, resource)
        self.auth.require(db, actor, 'operations.read', aid)
        activity = entity(db, 'activities', aid)
        if code not in PLATFORMS or selected and code not in selected_platforms(activity):
            fail(422 if write else 404, 'PLATFORM_NOT_SELECTED' if write else 'NOT_FOUND',
                 '该活动没有选中此平台', {'field': 'platformCode'} if write else None)
        return activity

    def basis(self, db, activity, code):
        current = activity.get('reviewVersionId', activity.get('currentReviewVersionId'))
        rid = activity.get('approvedVersionId')
        review = db.execute('SELECT * FROM version_reviews WHERE version_id=? AND activity_id=?',
                            (current, activity['id'])).fetchone()
        if review and review['state'] != 'approved':
            return None, review, 'REVIEW_NOT_APPROVED'
        if not current or rid != current or not review or review['approval_mode'] not in ('manual', 'single_management', 'rule_default'):
            return None, review, 'REVIEW_BASIS_UNAVAILABLE'
        row = db.execute('SELECT body FROM activity_versions WHERE id=?', (rid,)).fetchone()
        version = json.loads(row[0]) if row else None
        if not version or version.get('activityId') != activity['id']:
            return None, review, 'REVIEW_BASIS_UNAVAILABLE'
        if any(activity.get(key) not in (None, rid) for key in ('effectiveExecutionVersionId', 'effectiveVersionId')):
            return version, review, 'EXECUTION_VERSION_UNCONFIRMED'
        snapshot = version.get('snapshot') or {}
        confirmed = snapshot.get('resolvedFields') or snapshot.get('confirmedFields') or {}
        brand_ids = confirmed.get('brandIds')
        if 'brandIds' in confirmed and brand_ids is None:brand_ids=[]
        codes = snapshot.get('applicablePlatformCodes', [confirmed.get('platformCode')])
        if (not isinstance(codes,list) or code not in codes
                or not isinstance(brand_ids, list) or any(not isinstance(bid,str) or not bid for bid in brand_ids)
                or len(set(brand_ids)) != len(brand_ids) or set(brand_ids) != set(activity.get('brandIds') or ())):
            return version, review, 'PLATFORM_BASIS_UNAVAILABLE'
        return version, review, None

    def latest(self, db, aid, code):
        return db.execute(f'SELECT {COLUMNS} FROM {TABLE} WHERE activity_id=? AND platform_code=? ORDER BY revision DESC LIMIT 1',
                          (aid, code)).fetchone()

    def decision(self, db, material_id):
        if not verification_schema_ready(db):
            return None
        return db.execute(f'SELECT * FROM {VERIFY_TABLE} WHERE platform_material_id=?', (material_id,)).fetchone()

    def latest_recheck(self, db, material_id):
        if not recheck_schema_ready(db):
            return None
        return db.execute(f'SELECT * FROM {RECHECK_TABLE} WHERE platform_material_id=? ORDER BY rowid DESC LIMIT 1',
                          (material_id,)).fetchone()

    def is_admin(self, actor):
        return bool(getattr(self.auth, 'has_full_access', lambda _: False)(actor))

    def operator(self, db, activity, code, latest=None):
        # A caller may resolve an existing assignment. No new assignment system is created.
        uid = self.operator_resolver(db, activity, code) if self.operator_resolver else None
        if not uid and latest:
            uid = latest['submitter_id']
        if not uid:
            assigned = db.execute("""SELECT DISTINCT a.id FROM explicit_grants g JOIN account a ON a.id=g.user_id
                WHERE g.action=? AND g.resource_id=? AND a.status='active'""", (PERMISSION, activity['id'] + ':' + code)).fetchall()
            uid = assigned[0][0] if len(assigned) == 1 else None
        if not uid:
            return None
        row = db.execute('SELECT id,name,business_role,status FROM account WHERE id=?', (uid,)).fetchone()
        return {'id': uid, 'nameSnapshot': row['name'] if row else None,
                'roleSnapshot': row['business_role'] if row else None,
                'active': bool(row and row['status'] == 'active')}

    def require_verifier(self, db, actor, aid, code):
        activity = self.authorize(db, actor, aid, code)
        if not self.is_admin(actor):
            fail(403, 'FORBIDDEN', '平台上线核实由管理员处理')
        self.auth.require(db, actor, 'reviews.decide', 'assigned')
        return activity

    def can_submit(self, db, actor, activity, code, latest):
        aid = activity['id']
        if (PERMISSION not in self.auth.permissions(db, actor)
                or not grant_scopes(db, actor, self.auth, PERMISSION) & {'*', aid + ':' + code}):
            return 'FORBIDDEN'
        if self.is_admin(actor):
            return None
        operator = self.operator(db, activity, code, latest)
        if not operator or not operator['active']:
            return 'ASSIGNMENT_REQUIRED'
        if operator['id'] != actor['id']:
            return 'PLATFORM_OPERATOR_REQUIRED'
        return None

    def summary(self, db, actor, activity, code):
        version, review, reason = self.basis(db, activity, code)
        aid = activity['id']
        latest = self.latest(db, aid, code)
        legacy_live = ((activity.get('execution') or {}).get('state') == 'live'
                       and activity.get('platformCode') == code)
        bound = bool(version and latest and latest['approved_version_id'] == version['id'])
        readable = bool(version and can_version(db, actor, version['id'], self.auth))
        decision = self.decision(db, latest['id']) if latest and bound else None
        lifecycle = None
        actions = []
        if legacy_live:
            state, origin, reason = 'live', 'legacy_execution', 'LIVE_CHANGE_POLICY_UNCONFIRMED'
        elif reason == 'REVIEW_NOT_APPROVED':
            state, origin = 'blocked_review', 'review_gate'
        elif reason:
            state, origin = 'unknown', 'unknown'
        else:
            state = ('live' if decision['decision'] == 'approve' else 'returned') if decision else ('pending_verification' if bound else 'pending_materials')
            origin = 'platform_verification' if decision else 'platform_materials' if bound else 'review_gate'
            if state == 'live':
                recheck = self.latest_recheck(db, latest['id'])
                lifecycle = 'retrospective' if recheck and recheck['decision'] == 'approve' else 'pending_recheck'
            else:
                lifecycle = 'pending_registration' if state == 'pending_materials' else 'pending_check'
            if not readable:
                reason = 'MATERIAL_SCOPE_DENIED'
            elif state == 'pending_verification' or lifecycle == 'pending_recheck':
                if self.is_admin(actor) and 'reviews.decide' in self.auth.permissions(db, actor):
                    actions = [RECHECK_ACTION if lifecycle == 'pending_recheck' else VERIFY_ACTION]
                else:
                    reason = 'RECHECK_PENDING' if lifecycle == 'pending_recheck' else 'VERIFICATION_PENDING'
            elif state != 'live':
                reason = self.can_submit(db, actor, activity, code, latest)
                if not reason:
                    actions = [ACTION]
        blocked = [block(reason)] if reason else []
        if bound and not readable and reason != 'MATERIAL_SCOPE_DENIED':
            blocked.append(block('MATERIAL_SCOPE_DENIED'))
        operator = self.operator(db, activity, code, latest)
        summary = {'platformCode': code, 'state': state, 'stateOrigin': origin,
                   'lifecycleStage': lifecycle,
                   'platformRevision': latest['revision'] if latest else 0,
                   'allowedActions': actions, 'blockedReasons': blocked,
                   'responsibleOperator': operator, 'assignmentRequired': operator is None,
                   'reviewerRole': 'administrator',
                   'returnReason': decision['reason'] if decision and decision['decision'] == 'return' and readable else None}
        workflow = review['state'] if state == 'blocked_review' and review['state'] in STATES - {'blocked_review'} else state
        return summary, workflow, version, review, latest if bound and readable else None

    def project_activity(self, db, actor, item):
        activity = entity(db, 'activities', item['id'])
        codes = selected_platforms(activity)
        if not codes:
            state = 'live' if (activity.get('execution') or {}).get('state') == 'live' else activity.get('reviewState')
            return item | {'workflowStatus': state if state in STATES else 'unknown', 'platformProgress': []}
        progress, workflows = [], []
        for code in codes:
            summary, workflow, *_ = self.summary(db, actor, activity, code)
            progress.append(summary)
            workflows.append(workflow)
        # A live platform must not make another platform appear live.
        order = ('initial_pending', 'pricing_pending', 'final_pending', 'rejected', 'unknown',
                 'returned', 'pending_materials', 'pending_verification', 'live')
        workflow = next((value for value in order if value in workflows), 'unknown')
        stages = {p['lifecycleStage'] for p in progress}
        lifecycle = next((stage for stage in LIFECYCLE_ORDER if stage in stages), None)
        if workflow not in ('pending_materials', 'pending_verification', 'returned', 'live'):
            lifecycle = None
        return item | {'workflowStatus': workflow, 'lifecycleStage': lifecycle, 'platformProgress': progress}

    @staticmethod
    def decision_view(row):
        return {'decisionId': row['id'], 'decision': row['decision'], 'platformStatus': row['platform_status'],
                'platformMaterialId': row['platform_material_id'], 'approvedVersionId': row['approved_version_id'],
                'platformRevision': row['platform_revision'], 'platformCode': row['platform_code'],
                'checks': json.loads(row['checks']), 'evidenceSource': row['evidence_source'], 'reason': row['reason'],
                'reviewer': {'id': row['reviewer_id'], 'nameSnapshot': row['reviewer_name'], 'roleSnapshot': row['reviewer_role']},
                'reviewedAt': iso(row['reviewed_at'])}

    def material_view(self, db, actor, row):
        url = f"{self.prefix}/activities/{row['activity_id']}/platforms/{row['platform_code']}/materials/{row['id']}/download"
        download = grant_scopes(db, actor, self.auth, 'materials.download') & {'*', row['id']}
        comparison = json.loads(row['comparison_summary'])
        decision = self.decision(db, row['id'])
        recheck = self.latest_recheck(db, row['id'])
        verification = self.decision_view(decision) if decision else None
        if verification:
            screenshot = db.execute(f'SELECT filename,media_type,sha256,length(content) AS size FROM {SCREENSHOT_TABLE} '
                                    'WHERE verification_id=?', (decision['id'],)).fetchone()
            if screenshot:
                verification['screenshot'] = {'filename': screenshot['filename'], 'mediaType': screenshot['media_type'],
                    'sha256': screenshot['sha256'], 'byteSize': screenshot['size'],
                    'downloadUrl': url.removesuffix('/download') + '/check-screenshot' if download else None}
        return {'platformMaterialId': row['id'], 'revision': row['revision'], 'approvedVersionId': row['approved_version_id'],
                'platformCode': row['platform_code'], 'platformActivityId': row['platform_activity_id'],
                'scopeMode': row['scope_mode'], 'brands': json.loads(row['brands']),
                'scopeSource': 'approved_version' if row['media_type'] == APPROVED_SCOPE_MEDIA else
                               'uploaded_list' if row['filename'] else 'all_brand_products',
                'submitter': {'id': row['submitter_id'], 'nameSnapshot': row['submitter_name'], 'roleSnapshot': row['submitter_role']},
                'submittedAt': iso(row['submitted_at']), 'file': None if row['filename'] is None or row['media_type'] == APPROVED_SCOPE_MEDIA else
                {'filename': row['filename'], 'mediaType': row['media_type'], 'byteSize': row['byte_size'],
                 'sha256': row['file_sha256'], 'downloadUrl': url if download else None},
                'comparison': comparison, 'verification': verification,
                'recheck': self.decision_view(recheck) if recheck else None}

    def read(self, db, actor, path, q, handler):
        aid, code, mid, operation = ROUTE.fullmatch(path).groups()
        activity = self.authorize(db, actor, aid, code, selected=False)
        if code not in selected_platforms(activity) and not self.latest(db, aid, code):
            fail(404, 'NOT_FOUND', '该活动没有此平台资料')
        if mid:
            validate(q, set())
            row = db.execute(f'SELECT {COLUMNS} FROM {TABLE} WHERE id=? AND activity_id=? AND platform_code=?',
                             (mid, aid, code)).fetchone()
            if not row or not can_version(db, actor, row['approved_version_id'], self.auth):
                fail(404, 'NOT_FOUND', '平台资料不存在或不可访问')
            if operation == 'check-screenshot':
                self.auth.require(db, actor, 'materials.download', mid)
                screenshot = db.execute(f'SELECT s.* FROM {SCREENSHOT_TABLE} s JOIN {VERIFY_TABLE} v '
                                        'ON v.id=s.verification_id WHERE v.platform_material_id=?', (mid,)).fetchone()
                if not screenshot or q:
                    fail(404, 'NOT_FOUND', '检查截图不存在')
                if hashlib.sha256(screenshot['content']).hexdigest() != screenshot['sha256']:
                    fail(503, 'SOURCE_UNAVAILABLE', '检查截图校验失败')
                handler.binary_response = (screenshot['content'], {'Content-Type': screenshot['media_type'],
                    'Content-Disposition': "attachment; filename*=UTF-8''" + quote(screenshot['filename'], safe='')})
                return None
            if operation == 'download':
                if q or not row['filename'] or row['media_type'] == APPROVED_SCOPE_MEDIA:
                    fail(404, 'NOT_FOUND', '清单原件不存在')
                self.auth.require(db, actor, 'materials.download', mid)
                content = db.execute(f'SELECT content FROM {TABLE} WHERE id=?', (mid,)).fetchone()[0]
                if hashlib.sha256(content).hexdigest()!=row['file_sha256']:
                    fail(503,'SOURCE_UNAVAILABLE','平台清单原件校验失败')
                handler.binary_response = (content, {'Content-Type': row['media_type'],
                    'Content-Disposition': "attachment; filename*=UTF-8''" + quote(row['filename'], safe='')})
                return None
            value = json.loads(db.execute(f'SELECT comparison FROM {TABLE} WHERE id=?', (mid,)).fetchone()[0])
            value['rows'] = pagination(value['rows'], q)
            return value
        if SUBMIT.fullmatch(path):
            validate(q, {'throughRevision'})
            allowed = [row for row in db.execute(f'SELECT {COLUMNS} FROM {TABLE} WHERE activity_id=? AND platform_code=? ORDER BY revision DESC,id',
                                                (aid, code)) if can_version(db, actor, row['approved_version_id'], self.auth)]
            maximum = allowed[0]['revision'] if allowed else 0
            limit = q.get('throughRevision')
            if limit is not None:
                if not limit.isascii() or not limit.isdigit() or len(limit) > 18:
                    fail(422, 'INVALID_FILTER', '历史修订上界无效')
                maximum = min(maximum, int(limit))
            result = pagination([row for row in allowed if row['revision'] <= maximum], q)
            result['items'] = [self.material_view(db, actor, row) for row in result['items']]
            return result | {'throughRevision': maximum}
        if q:
            fail(422, 'INVALID_FILTER', '平台详情不支持查询参数')
        status, _, version, review, material = self.summary(db, actor, activity, code)
        readable=version and can_version(db,actor,version['id'],self.auth)
        brands,full_brand=self.brand_scope(db,version['snapshot']) if readable and not self.basis(db,activity,code)[2] else ([],False)
        scope_modes=(['list']+(['all_brand_products'] if full_brand else [])) if ACTION in status['allowedActions'] else []
        return status | {'activityId': aid, 'activityRevision': activity.get('revision'),
                         'brands':brands,'allowedScopeModes':scope_modes,
                         'approvedVersionId': version['id'] if version else None,
                         'reviewRevision': review['revision'] if version else None,
                         'currentMaterial': self.material_view(db, actor, material) if material else None}

    def check_receipt(self, db, actor, command):
        aid, code = command['resource_id'].split(':', 1)
        self.auth.require(db, actor, 'operations.read', aid)
        if command['action'] in REVIEW_ACTIONS:
            if not self.is_admin(actor):
                fail(403, 'FORBIDDEN', '平台核实回执仅管理员可读取')
            self.auth.require(db, actor, 'reviews.decide', 'assigned')
            table = RECHECK_TABLE if command['action'] == RECHECK_ACTION else VERIFY_TABLE
        else:
            self.auth.require(db, actor, PERMISSION, command['resource_id'])
            table = TABLE
        row = db.execute(f'SELECT approved_version_id FROM {table} WHERE command_id=? AND activity_id=? AND platform_code=?',
                         (command['id'], aid, code)).fetchone()
        if not row or not can_version(db, actor, row[0], self.auth):
            fail(403, 'FORBIDDEN', '平台资料回执访问权失效')

    @staticmethod
    def prepare_verification(body):
        fields(body, ['expectedApprovedVersionId', 'expectedPlatformRevision', 'platformMaterialId', 'decision',
                      'platformStatus', 'platformActivityIdConfirmed', 'productScopeConfirmed', 'evidenceSource', 'reason'],
               ['checkStage', 'screenshot'])
        if 'checkStage' in body and body['checkStage'] != 'recheck':
            fail(422, 'INVALID_REQUEST', '检查阶段无效', {'field': 'checkStage'})
        for key in ('expectedApprovedVersionId', 'platformMaterialId'):
            if not isinstance(body[key], str) or not re.fullmatch(ID, body[key]):
                fail(422, 'INVALID_REQUEST', '版本或资料标识无效', {'field': key})
        if type(body['expectedPlatformRevision']) is not int or body['expectedPlatformRevision'] < 1:
            fail(422, 'INVALID_REQUEST', '平台资料修订无效', {'field': 'expectedPlatformRevision'})
        if body['decision'] not in ('approve', 'return') or body['platformStatus'] not in ('live', 'not_live', 'unknown'):
            fail(422, 'INVALID_REQUEST', '核实决定或平台实际状态无效')
        for key in ('platformActivityIdConfirmed', 'productScopeConfirmed'):
            if type(body[key]) is not bool:
                fail(422, 'INVALID_REQUEST', '核对项须为布尔值', {'field': key})
        for key in ('evidenceSource', 'reason'):
            value = body[key]
            if not isinstance(value, str) or len(value) > 2000 or any(unicodedata.category(c) == 'Cs' or unicodedata.category(c) == 'Cc' and c not in '\n\r\t' for c in value):
                fail(422, 'INVALID_REQUEST', '说明须为 2000 字以内有效文本', {'field': key})
        if body['decision'] == 'return' and not body['reason'].strip():
            fail(422, 'RETURN_REASON_REQUIRED', '请填写退回原因', {'field': 'reason'})
        if body['decision'] == 'approve' and (body['platformStatus'] != 'live' or not body['platformActivityIdConfirmed']
                or body.get('checkStage') == 'recheck' and not body['evidenceSource'].strip()):
            fail(422, 'VERIFICATION_EVIDENCE_REQUIRED', '请核对平台活动 ID 和上线状态，并提供本次检查所需的依据')

    @staticmethod
    def prepare_screenshot(body, recheck):
        screenshot = body.get('screenshot')
        if recheck:
            if screenshot is not None:
                fail(422, 'INVALID_REQUEST', '截图附件用于首次检查', {'field': 'screenshot'})
            return None
        if screenshot is None:
            if body['decision'] == 'approve':
                fail(422, 'CHECK_SCREENSHOT_REQUIRED', '请上传可看清平台活动 ID 和上线状态的截图', {'field': 'screenshot'})
            return None
        if not isinstance(screenshot, dict) or screenshot.get('mediaType') not in SCREENSHOT_TYPES:
            fail(422, 'INVALID_CHECK_SCREENSHOT', '请选择 PNG、JPG 或 WebP 截图', {'field': 'screenshot'})
        filename, media, content, _ = upload_payload(screenshot)
        return filename, media, hashlib.sha256(content).hexdigest(), content

    def verify(self, db, actor, aid, code, body, command_id):
        self.prepare_verification(body)
        recheck = body.get('checkStage') == 'recheck'
        ready = recheck_schema_ready if recheck else verification_schema_ready
        if not ready(db):
            fail(503, 'VERIFICATION_NOT_READY', '平台核实存储尚未准备完成')
        activity = self.require_verifier(db, actor, aid, code)
        status, _, version, _, material = self.summary(db, actor, activity, code)
        if not version or body['expectedApprovedVersionId'] != version['id']:
            fail(409, 'ACTIVITY_VERSION_CONFLICT', '当前审核通过版本已变化')
        if body['expectedPlatformRevision'] != status['platformRevision'] or not material or body['platformMaterialId'] != material['id']:
            fail(409, 'PLATFORM_VERSION_CONFLICT', '平台资料已变化，请刷新后重新核对')
        action = RECHECK_ACTION if recheck else VERIFY_ACTION
        if action not in status['allowedActions']:
            reason = 'PLATFORM_NOT_PENDING_RECHECK' if recheck else 'PLATFORM_NOT_PENDING_VERIFICATION'
            if not recheck and status['state'] == 'live':
                reason = 'LIVE_CHANGE_POLICY_UNCONFIRMED'
            elif status['blockedReasons']:
                reason = status['blockedReasons'][0]['code']
            fail(409, reason, '当前平台资料不处于可核实状态')
        screenshot = self.prepare_screenshot(body, recheck)
        reviewed = self.clock()
        decision_id = new_id('PMRCK' if recheck else 'PMV')
        checks = {key: body[key] for key in ('platformActivityIdConfirmed', 'productScopeConfirmed')}
        evidence = body['evidenceSource'].strip() or ('平台活动截图' if screenshot else '')
        values = (decision_id, aid, code, version['id'], material['id'], material['revision'], body['decision'],
                  body['platformStatus'], canon(checks), evidence, body['reason'], actor['id'],
                  actor['name'], actor['business_role'], reviewed, command_id)
        table = RECHECK_TABLE if recheck else VERIFY_TABLE
        db.execute(f'INSERT INTO {table} VALUES ({",".join("?" for _ in values)})', values)
        if screenshot:
            db.execute(f'INSERT INTO {SCREENSHOT_TABLE} VALUES (?,?,?,?,?)', (decision_id, *screenshot))
        approved = body['decision'] == 'approve'
        lifecycle = ('retrospective' if approved else 'pending_recheck') if recheck else (
            'pending_recheck' if approved else 'pending_check')
        return 201, {'activityId': aid, 'approvedVersionId': version['id'], 'platformCode': code,
                     'platformMaterialId': material['id'], 'platformRevision': material['revision'], 'decisionId': decision_id,
                     'state': 'live' if approved or recheck else 'returned',
                     'lifecycleStage': lifecycle, 'reviewedAt': iso(reviewed)}

    def prepare(self, body):
        fields(body, ['expectedActivityRevision', 'expectedApprovedVersionId', 'expectedPlatformRevision',
                      'platformActivityId', 'scopeMode'], ['listFile'])
        value = body['platformActivityId']
        if value is None or isinstance(value, str) and not value.strip():
            fail(422, 'PLATFORM_ID_REQUIRED', '请输入平台活动 ID', {'field': 'platformActivityId'})
        if not isinstance(value, str) or len(value) > 200 or any(unicodedata.category(c) in ('Cc', 'Cs') for c in value):
            fail(422, 'PLATFORM_ID_INVALID', '平台活动 ID 需为有效原字符串', {'field': 'platformActivityId'})
        for key, low in [('expectedActivityRevision', 1), ('expectedPlatformRevision', 0)]:
            if type(body[key]) is not int or body[key] < low:
                fail(422, 'INVALID_REQUEST', '修订值无效', {'field': key})
        if not isinstance(body['expectedApprovedVersionId'], str) or not re.fullmatch(ID, body['expectedApprovedVersionId']):
            fail(422, 'INVALID_REQUEST', '审核版本无效', {'field': 'expectedApprovedVersionId'})
        mode = body['scopeMode']
        if mode not in ('list', 'all_brand_products'):
            fail(422, 'SCOPE_CONFLICT', '请选择一种商品范围', {'field': 'scopeMode'})
        if mode == 'all_brand_products':
            if body.get('listFile') is not None:
                fail(422, 'SCOPE_CONFLICT', '全品牌商品与清单互斥', {'field': 'listFile'})
            return None
        if body.get('listFile') is None:
            return None
        return parse_file(body['listFile'], self.decode)

    @staticmethod
    def brand_scope(db,snapshot):
        brand_ids=(snapshot.get('resolvedFields') or snapshot.get('confirmedFields') or {}).get('brandIds') or []
        brands=[]
        for bid in brand_ids:
            row=db.execute('SELECT body FROM brands WHERE id=?',(bid,)).fetchone()
            brands.append({'id':bid,'nameSnapshot':json.loads(row[0]).get('name') if row else None})
        unconfirmed=any(item.get('field')=='brandIds' or item.get('reasonCode')=='BRAND_UNCONFIRMED'
                        for item in snapshot.get('pendingItems') or [])
        return brands,len(brands)==1 and bool(brands[0]['nameSnapshot']) and not unconfirmed

    def submit(self, db, actor, aid, code, body, command_id, file):
        activity = self.authorize(db, actor, aid, code, write=True)
        version, review, reason = self.basis(db, activity, code)
        if (activity.get('execution') or {}).get('state') == 'live' and activity.get('platformCode') == code:
            reason = 'LIVE_CHANGE_POLICY_UNCONFIRMED'
        if version and not can_version(db, actor, version['id'], self.auth):
            fail(403, 'FORBIDDEN', '缺少绑定审核版读取权限')
        if reason:
            fail(409, reason, REASONS[reason])
        if body['expectedActivityRevision'] != activity.get('revision') or body['expectedApprovedVersionId'] != version['id']:
            fail(409, 'ACTIVITY_VERSION_CONFLICT', '活动或当前通过版已变化', {
                'activityRevision': activity.get('revision'), 'approvedVersionId': version['id']})
        latest = self.latest(db, aid, code)
        revision = latest['revision'] if latest else 0
        if body['expectedPlatformRevision'] != revision:
            fail(409, 'PLATFORM_VERSION_CONFLICT', '平台资料已变化', {'platformRevision': revision})
        status, *_ = self.summary(db, actor, activity, code)
        if ACTION not in status['allowedActions']:
            reason = ('LIVE_CHANGE_POLICY_UNCONFIRMED' if status['state'] == 'live' else
                      status['blockedReasons'][0]['code'] if status['blockedReasons'] else 'VERIFICATION_PENDING')
            fail(403 if reason in ('FORBIDDEN', 'PLATFORM_OPERATOR_REQUIRED') else 409, reason, REASONS[reason])
        snapshot = version['snapshot']
        brands,full_brand=self.brand_scope(db,snapshot)
        if body['scopeMode']=='all_brand_products' and not full_brand:
            fail(422, 'ALL_BRAND_REQUIRES_SINGLE_BRAND', '全品牌范围需绑定一个现有品牌', {'field': 'scopeMode'})
        filename, media, content, digest, parsed = file or (None,) * 5
        mid = new_id('PMR')
        catalog = json.loads(db.execute("SELECT value FROM b_meta WHERE key='catalogRevision'").fetchone()[0])
        basis = {'approvedVersionId': version['id'], 'platformMaterialId': mid, 'platformCode': code,
                 'catalogRevision': catalog, 'parserVersion': parsed['parserVersion'] if parsed else None,
                 'matchRuleVersion': MATCH_RULE, 'scopeMode': body['scopeMode']}
        products = {row['id']: json.loads(row['body']) for row in db.execute('SELECT id,body FROM products')} if parsed else {}
        snapshot, products, confirmed_basis = confirmed_comparison_inputs(db, aid, version['id'], code, snapshot, products)
        basis.update(confirmed_basis)
        if file is None and body['scopeMode'] == 'list':
            # Store a version-bound internal scope receipt. It is not a platform attachment
            # and does not assert that the platform supplied a verified line-by-line list.
            from b_registration_review import saved
            frozen = saved(db, aid, version['id'], code)
            final_rows = frozen.get('finalRows', []) if frozen and frozen.get('status') == 'finalized' else []
            scope_evidence = {'source': 'approved_version', 'activityId': aid, 'versionId': version['id'],
                              'platformCode': code, 'finalSelectionDigest': basis.get('finalSelectionDigest'),
                              'internalCodes': sorted({row['internalCode'] for row in final_rows
                                                       if isinstance(row.get('internalCode'), str) and row['internalCode']})}
            content = canon(scope_evidence).encode('utf-8')
            filename, media, digest = 'approved-scope.json', APPROVED_SCOPE_MEDIA, hashlib.sha256(content).hexdigest()
            basis['scopeSource'] = 'approved_version'
        comparison = compare(snapshot, products, basis, parsed)
        if basis.get('scopeSource') == 'approved_version':
            comparison['reasonCodes'] = ['APPROVED_VERSION_SCOPE']
            comparison['counts']['approvedProducts'] = len(scope_evidence['internalCodes']) if frozen else None
        submitted = self.clock()
        comparison_summary = {key: comparison[key] for key in ('status','counts','reasonCodes')}
        values=(mid, aid, code, revision + 1, version['id'], review['revision'], body['platformActivityId'],
                body['scopeMode'], canon(brands), actor['id'], actor['name'], actor['business_role'], submitted,
                command_id, filename, media, len(content) if content else None, digest, content,
                canon(comparison_summary), canon(comparison))
        db.execute(f'INSERT INTO {TABLE} VALUES ({",".join("?" for _ in values)})',values)
        return 201, {'activityId': aid, 'approvedVersionId': version['id'], 'platformCode': code,
                     'platformMaterialId': mid, 'platformRevision': revision + 1,
                     'state': 'pending_verification', 'lifecycleStage': 'pending_check', 'submittedAt': iso(submitted)}
