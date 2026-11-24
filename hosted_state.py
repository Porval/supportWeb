"""An isolated persistent store for the hosted JT024 workbench."""
import contextlib
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import secrets
import shutil
import sqlite3
import stat
import tempfile
import time
import uuid

from account_primitives import ROLES, fail, iso, phone as normalize_phone
from b_auth import BAccounts
from b_state import DDL as BUSINESS_DDL, MARKER, STATE, canon, new_id
from storage import prepare_db
from test_accounts import DDL as ACCOUNT_DDL, ITERATIONS, derive


NAMESPACE = "jt024-workbench-v1"
ORIGIN = "hosted_workbench"
MAX_EXPIRY = 253402214400  # An untimed administrator credential, valid through 9999-12-30.
_BASE_READ_GRANTS = (
    ("operations.read", "*"),
    ("operations.download", "*"),
    ("materials.read", "*"),
    ("materials.download", "*"),
)
_SUBMITTER_GRANTS = _BASE_READ_GRANTS + (
    ("submissions.create", "*"),
    ("submissions.read_own", "own"),
    ("submissions.edit_own", "own"),
    ("activities.propose", "*"),
    ("platform_materials.write", "*"),
)
_PRICING_GRANTS = _BASE_READ_GRANTS + (
    ("reviews.read", "assigned"),
    ("reviews.decide", "assigned"),
)
_MANAGEMENT_GRANTS = _SUBMITTER_GRANTS + (
    ("reviews.read", "assigned"),
    ("reviews.decide", "assigned"),
    ("activities.rate", "*"),
)
ROLE_DEFAULT_GRANTS = {
    "operations": _SUBMITTER_GRANTS,
    "merchandising": _SUBMITTER_GRANTS,
    "marketing": _SUBMITTER_GRANTS,
    "management": _MANAGEMENT_GRANTS,
    "pricing": _PRICING_GRANTS,
    "business_development": _BASE_READ_GRANTS,
    "supply_chain": _BASE_READ_GRANTS,
    "finance": _BASE_READ_GRANTS,
    "other": _BASE_READ_GRANTS,
    None: _BASE_READ_GRANTS,
}
ANONYMOUS_ACTOR_ID = "anonymous-viewer"
ANONYMOUS_SESSION_TTL = 28800
ANONYMOUS_PERMISSIONS = (
    "materials.download",
    "materials.read",
    "operations.download",
    "operations.read",
)
ANONYMOUS_SCOPES = frozenset({
    "materials.download",
    "materials.read",
    "operations.download",
    "operations.read",
    "submissions.read",
})
ANONYMOUS_SESSION_DDL = """CREATE TABLE IF NOT EXISTS hosted_anonymous_sessions (
    sid_mac TEXT PRIMARY KEY REFERENCES sessions(sid_mac) ON DELETE CASCADE,
    created_at REAL NOT NULL
)"""
PROFILE_COLUMNS = (
    "business_role TEXT", "name TEXT", "remark TEXT", "phone TEXT",
    "status TEXT NOT NULL DEFAULT 'active'", "profile_version INTEGER NOT NULL DEFAULT 1",
)


def is_anonymous_actor(actor):
    return isinstance(actor, dict) and actor.get("id") == ANONYMOUS_ACTOR_ID and actor.get("_anonymousViewer") is True


def exact_path(value):
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts or any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError("A canonical absolute path without symlinks is required")
    return path


def private_file(path):
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_uid != os.geteuid() or info.st_nlink != 1):
        raise ValueError("Unexpected permissions or ownership on private state")


def sync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def create_private(path, data):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as output:
        output.write(data)
        output.flush()
        os.fsync(output.fileno())


class HostedPaths:
    fixture = False

    def __init__(self, directory):
        self.directory = exact_path(directory)

    def validate(self):
        info = self.directory.lstat()
        if not stat.S_ISDIR(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o700 or info.st_uid != os.geteuid():
            raise ValueError("Unexpected directory at the workbench state path")
        for name in (MARKER, STATE, "session.key"):
            private_file(self.directory / name)
        for suffix in ("-journal", "-wal", "-shm"):
            path = self.directory / (STATE + suffix)
            if os.path.lexists(path):
                private_file(path)
        marker = json.loads((self.directory / MARKER).read_bytes())
        if marker.get("hostedFormat") != NAMESPACE or marker.get("dataOrigin") != ORIGIN:
            raise ValueError("Unrecognized workbench database; refusing to modify it")


def business_migrations(db):
    from b_platform_materials import migrate as platform
    from b_registration_draft import migrate as drafts
    from b_registration_review import migrate as reviews
    from b_coupon_review import migrate as coupons
    for migrate in (platform, drafts, reviews, coupons):
        migrate(db)
    db.execute(ANONYMOUS_SESSION_DDL)


def create_database(stage):
    from schedule_storage import DDL as schedule_ddl, validate as validate_board

    key = secrets.token_bytes(32)
    marker = {"authority": "state-v2", "schemaVersion": 2, "migrationId": str(uuid.uuid4()),
              "hostedFormat": NAMESPACE, "dataOrigin": ORIGIN}
    create_private(stage / "session.key", key)
    metadata = {
        "schemaVersion": 1, "dataOrigin": ORIGIN, "datasetRevision": "hosted-v1:0",
        "sourceReady": True, "relationsComplete": False, "sourceHashes": [],
    }
    # prepare_db creates the same empty entity and relationship tables used by .49.
    base = prepare_db(stage, {"meta": metadata}, mode="hosted")
    state = stage / STATE
    base.rename(state)
    with contextlib.closing(sqlite3.connect(state)) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.executescript(ACCOUNT_DDL)
        for column in PROFILE_COLUMNS:
            db.execute("ALTER TABLE account ADD COLUMN " + column)
        db.executescript(BUSINESS_DDL)
        db.executescript(schedule_ddl)
        db.execute("ALTER TABLE jt022_comments ADD COLUMN author_identity_json TEXT NULL")
        db.execute("INSERT INTO jt022_boards VALUES('shared',2,1,0,?,0,?)",
                   (canon({"review": 0, "doing": 0, "done": 0}), iso(time.time())))
        validate_board(db)
        db.execute("INSERT INTO config VALUES(1,?)", (hashlib.sha256(key).hexdigest(),))
        values = {"schema_version": 2, "authority": "state-v2", "migrationId": marker["migrationId"],
                  "sourceHashes": {}, "datasetCounter": 0, "catalogRevision": "hosted-catalog-v1",
                  "hostedFormat": NAMESPACE}
        db.executemany("INSERT INTO b_meta VALUES(?,?)", [(k, canon(v)) for k, v in values.items()])
        business_migrations(db)
        db.commit()
        if db.execute("PRAGMA foreign_key_check").fetchone() or db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise ValueError("Fresh state integrity check failed")
    create_private(stage / MARKER, canon(marker).encode())
    sync_dir(stage)


def bootstrap_admin(accounts, verifier_path):
    path = exact_path(verifier_path)
    private_file(path)
    if path.stat().st_size > 4096:
        raise ValueError("Invalid administrator verifier")
    data = json.loads(path.read_bytes())
    required = {"username", "displayName", "saltHex", "verifierHex"}
    if not isinstance(data, dict) or not required <= data.keys() <= required | {"expiresAt"}:
        raise ValueError("Invalid administrator verifier schema")
    username, name = data["username"], data["displayName"]
    if (not isinstance(username, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.@+-]{0,126}", username)
            or not isinstance(name, str) or not 1 <= len(name.strip()) <= 128):
        raise ValueError("Invalid administrator identity")
    if not re.fullmatch(r"(?:[a-fA-F0-9]{2}){16,64}", data["saltHex"]) or not re.fullmatch(r"[a-fA-F0-9]{64}", data["verifierHex"]):
        raise ValueError("Invalid administrator password verifier")
    created = accounts.clock()
    expires = data.get("expiresAt", MAX_EXPIRY)
    if type(expires) not in (float, int) or not math.isfinite(expires) or not created < expires <= MAX_EXPIRY:
        raise ValueError("Invalid administrator credential expiry")
    uid = "user-" + str(uuid.uuid4())
    with accounts.tx() as db:
        if db.execute("SELECT 1 FROM account LIMIT 1").fetchone():
            return
        db.execute("""INSERT INTO account(id,username,salt,verifier,algorithm,iterations,derived_length,
                   credential_version,created_at,expires_at,permissions,scope,name)
                   VALUES(?,?,?,?,'pbkdf2_hmac_sha256',?,32,1,?,?,?,'*',?)""",
                   (uid, username, bytes.fromhex(data["saltHex"]), bytes.fromhex(data["verifierHex"]),
                    ITERATIONS, created, expires, canon(["accounts.manage"]), name.strip()))
        db.execute("INSERT INTO system_permissions VALUES(?,'accounts.manage')", (uid,))
        db.executemany("INSERT INTO review_plan VALUES(?,?)", [(stage, uid) for stage in ("initial","pricing","final")])


class HostedAccounts(BAccounts):
    def __init__(self, paths, clock=time.time):
        # The test constructor additionally expects cleartext test delivery files.
        # Its key binding, database and marker checks apply to this subclass.
        super().__init__(paths, clock, allow_delivery_inconsistent=True)
        with self.tx() as db:
            rows = dict(db.execute("SELECT key,value FROM metadata"))
            marker = db.execute("SELECT value FROM b_meta WHERE key='hostedFormat'").fetchone()
            if (not marker or json.loads(marker[0]) != NAMESPACE or
                    json.loads(rows.get("dataOrigin", "null")) != ORIGIN or json.loads(rows.get("mode", "null")) != "hosted"):
                raise ValueError("Unrecognized hosted database")

    @staticmethod
    def validate_account(row):
        if (not re.fullmatch(r"user-[0-9a-f-]{36}", row["id"]) or not row["username"]
                or row["algorithm"] != "pbkdf2_hmac_sha256" or row["iterations"] != ITERATIONS
                or row["derived_length"] != 32 or not 16 <= len(row["salt"]) <= 64 or len(row["verifier"]) != 32
                or row["expires_at"] <= row["created_at"] or row["credential_version"] < 1
                or row["business_role"] not in {*ROLES, None}):
            raise ValueError("Invalid hosted account")

    def account(self, db, uid=None):
        if uid is None:
            row = db.execute("""SELECT a.* FROM account a JOIN system_permissions p
                             ON p.user_id=a.id WHERE p.permission='accounts.manage' ORDER BY a.id LIMIT 1""").fetchone()
            if row is None:
                fail(503, "ADMIN_NOT_CONFIGURED", "管理员登录尚未配置")
        else:
            row = db.execute("SELECT * FROM account WHERE id=?", (uid,)).fetchone()
            if row is None:
                fail(404, "ACCOUNT_NOT_FOUND", "账号不存在")
        self.validate_account(row)
        return self.bind_authorization(db, row)

    @staticmethod
    def anonymous_actor():
        return {
            "id": ANONYMOUS_ACTOR_ID,
            "username": "anonymous",
            "name": "匿名访客",
            "phone": None,
            "business_role": None,
            "status": "active",
            "profile_version": 1,
            "expires_at": None,
            "_administrator": False,
            "_anonymousViewer": True,
        }

    @staticmethod
    def is_anonymous_session(db, session_row):
        if session_row is None or session_row["account_id"] is not None:
            return False
        return bool(db.execute(
            "SELECT 1 FROM hosted_anonymous_sessions WHERE sid_mac=?",
            (session_row["sid_mac"],),
        ).fetchone())

    def full_access_enabled(self, db, actor):
        if is_anonymous_actor(actor):
            return False
        return super().full_access_enabled(db, actor)

    def permissions(self, db, actor):
        if is_anonymous_actor(actor):
            return list(ANONYMOUS_PERMISSIONS)
        return super().permissions(db, actor)

    def scopes(self, db, actor, action):
        if is_anonymous_actor(actor):
            return {"*"} if action in ANONYMOUS_SCOPES else set()
        return super().scopes(db, actor, action)

    def require(self, db, actor, action, resource='*'):
        if is_anonymous_actor(actor):
            if action not in ANONYMOUS_PERMISSIONS:
                fail(403, "FORBIDDEN", "匿名查看模式没有该操作权限")
            return
        return super().require(db, actor, action, resource)

    def user(self, row):
        if is_anonymous_actor(row):
            return {
                "id": ANONYMOUS_ACTOR_ID,
                "username": "anonymous",
                "name": "匿名访客",
                "phone": None,
                "businessRole": None,
                "roleLabel": "匿名只读",
                "profileVersion": 1,
            }
        return super().user(row)

    def valid(self, db, sid, require_user=False):
        row = self.lookup(db, sid)
        if row is not None and row["account_id"] is None:
            if row["revoked_at"] is not None or row["expires_at"] <= self.clock():
                fail(401, "SESSION_EXPIRED", "登录会话已失效")
            if self.is_anonymous_session(db, row):
                return row, self.anonymous_actor()
            if require_user:
                fail(401, "SESSION_REQUIRED", "请登录工作台")
            return row, None
        return super().valid(db, sid, require_user)

    def view(self, db, sid, reason=None):
        session, account = self.valid(db, sid)
        if self.is_anonymous_session(db, session):
            actions = list(ANONYMOUS_PERMISSIONS)
            return {
                "authenticated": True,
                "anonymous": True,
                "authMethod": "password",
                "readOnly": True,
                "user": self.user(account),
                "permissions": actions,
                "csrfToken": self.mac("csrf", sid),
                "absoluteExpiresAt": iso(session["expires_at"]),
                "credentialExpiresAt": None,
                "reason": reason,
                "profileVersion": 1,
                "capabilities": {
                    "contractVersion": "1.1",
                    "allowedActions": actions,
                    "fullAccessAdministrator": False,
                    "businessReadOnly": True,
                },
            }
        return super().view(db, sid, reason) | {"anonymous": False}

    def anonymous_login(self, sid):
        with self.tx() as db:
            db.execute(ANONYMOUS_SESSION_DDL)
            session, _ = self.valid(db, sid)
            if session["account_id"] is not None or self.is_anonymous_session(db, session):
                fail(409, "ALREADY_AUTHENTICATED", "当前已登录")
            now = self.clock()
            rate = self.rate(db, "anonymous-viewer-login")
            start, count = (rate["window_start"], rate["count"]) if rate and rate["window_start"] + 60 > now else (now, 0)
            if count >= 60:
                self.limited(start + 60)
            self.rate_set(db, "anonymous-viewer-login", start, count + 1)
            new_sid = secrets.token_urlsafe(32)
            new_mac = self.mac("sid", new_sid)
            expires = now + ANONYMOUS_SESSION_TTL
            db.execute("UPDATE sessions SET revoked_at=? WHERE sid_mac=?", (now, session["sid_mac"]))
            db.execute("INSERT INTO sessions VALUES(?,NULL,NULL,?,?,NULL)", (new_mac, now, expires))
            db.execute("INSERT INTO hosted_anonymous_sessions(sid_mac,created_at) VALUES(?,?)", (new_mac, now))
            return self.view(db, new_sid), new_sid, expires

    def login(self, sid, username, password):
        with self.tx() as db:
            session, _ = self.valid(db, sid)
            if self.is_anonymous_session(db, session):
                fail(409, "ALREADY_AUTHENTICATED", "请先退出匿名查看模式再登录")
        return super().login(sid, username, password)

    def review_account_active(self, db, row):
        return row["status"] == "active" and row["expires_at"] > self.clock()

    def sync_role_grants(self, db, uid, business_role, *, is_admin=None):
        if business_role not in ROLE_DEFAULT_GRANTS:
            fail(422, "INVALID_REQUEST", "业务角色无效")
        if is_admin is True:
            db.execute("INSERT OR IGNORE INTO system_permissions(user_id,permission) VALUES(?,'accounts.manage')", (uid,))
        elif is_admin is False:
            admins = db.execute(
                """SELECT a.id FROM account a JOIN system_permissions p ON a.id=p.user_id
                   WHERE p.permission='accounts.manage' AND a.status='active' AND a.expires_at>?""",
                (self.clock(),),
            ).fetchall()
            if len(admins) <= 1 and any(row["id"] == uid for row in admins):
                fail(409, "LAST_ADMIN_REQUIRED", "至少需要保留一位有效管理员账号")
            db.execute("DELETE FROM system_permissions WHERE user_id=? AND permission='accounts.manage'", (uid,))
        db.execute("DELETE FROM explicit_grants WHERE user_id=?", (uid,))
        grants = ROLE_DEFAULT_GRANTS[business_role]
        db.executemany("INSERT OR IGNORE INTO explicit_grants(user_id,action,resource_id) VALUES(?,?,?)",
                       [(uid, action, scope) for action, scope in grants])
        has_admin = bool(db.execute(
            "SELECT 1 FROM system_permissions WHERE user_id=? AND permission='accounts.manage'", (uid,),
        ).fetchone())
        actions = sorted({action for action, _ in grants} | ({"accounts.manage"} if has_admin else set()))
        db.execute("UPDATE account SET permissions=? WHERE id=?", (canon(actions), uid))

    def create_user(self, db, actor, body):
        self.require(db, actor, "accounts.manage")
        allowed = {"name", "phone", "businessRole", "remark", "accountManagementAuthorized"}
        if not isinstance(body, dict) or not {"name", "phone"} <= body.keys() <= allowed:
            fail(422, "INVALID_REQUEST", "请填写姓名和手机号")
        raw_name = body.get("name")
        if not isinstance(raw_name, str) or not 1 <= len(raw_name.strip()) <= 50:
            fail(422, "INVALID_REQUEST", "姓名须为 1 至 50 字")
        name = raw_name.strip()
        normalized_phone = normalize_phone(body.get("phone"))
        username = normalized_phone[3:]
        business_role = body.get("businessRole")
        if business_role == "":
            business_role = None
        if business_role is not None and (not isinstance(business_role, str) or business_role not in ROLES):
            fail(422, "INVALID_REQUEST", "业务角色无效")
        raw_remark = body.get("remark")
        if raw_remark is not None:
            if not isinstance(raw_remark, str) or len(raw_remark) > 200:
                fail(422, "INVALID_REQUEST", "备注不能超过 200 字")
            remark = raw_remark.strip() or None
        else:
            remark = None
        is_admin = body.get("accountManagementAuthorized", False)
        if type(is_admin) is not bool:
            fail(422, "INVALID_REQUEST", "账号管理授权参数无效")
        existing = db.execute(
            "SELECT 1 FROM account WHERE username=? OR phone IN (?,?) LIMIT 1",
            (username, normalized_phone, username),
        ).fetchone()
        if existing:
            fail(409, "ACCOUNT_EXISTS", "该手机号账号已存在")
        uid = "user-" + str(uuid.uuid4())
        created = self.clock()
        salt = secrets.token_bytes(24)
        verifier = derive(secrets.token_urlsafe(32), salt)
        db.execute(
            """INSERT INTO account(id,username,salt,verifier,algorithm,iterations,derived_length,
               credential_version,created_at,expires_at,permissions,scope,business_role,name,remark,phone,status,profile_version)
               VALUES(?,?,?,?,'pbkdf2_hmac_sha256',?,32,1,?,?,?,'*',?,?,?,?,'active',1)""",
            (uid, username, salt, verifier, ITERATIONS, created, MAX_EXPIRY,
             canon([]), business_role, name, remark, normalized_phone),
        )
        self.sync_role_grants(db, uid, business_role, is_admin=is_admin)
        row = db.execute("SELECT * FROM account WHERE id=?", (uid,)).fetchone()
        self.validate_account(row)
        profile = self.user(row) | {
            "remark": row["remark"],
            "profileVersion": row["profile_version"],
            "accountManagementAuthorized": is_admin,
            "status": row["status"],
        }
        db.execute(
            "INSERT INTO account_profile_audit VALUES(?,?,?,?,?,?)",
            (new_id("EVT"), actor["id"], uid, canon({}), canon(profile), created),
        )
        return profile

    def admin_configured(self):
        with self.tx() as db:
            return bool(db.execute("""SELECT 1 FROM account a JOIN system_permissions p ON a.id=p.user_id
                WHERE p.permission='accounts.manage' AND a.status='active' AND a.expires_at>? LIMIT 1""",
                (self.clock(),)).fetchone())


def open_hosted_state(data_dir, bootstrap_path=None, *, clock=time.time):
    """Open the independent store. Caller supplies an already-created private data root."""
    root = exact_path(data_dir)
    if not root.is_dir():
        raise ValueError("ACTIVITY_DATA_DIR must name an existing directory")
    target = root / NAMESPACE
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        if not os.path.lexists(target):
            stage = Path(tempfile.mkdtemp(dir=root, prefix=".workbench-initializing-"))
            try:
                create_database(stage)
                stage.rename(target)
                sync_dir(root)
            finally:
                if stage.exists():
                    shutil.rmtree(stage)
        accounts = HostedAccounts(HostedPaths(target), clock)
        with accounts.tx() as db:
            business_migrations(db)
            already_provisioned = db.execute("SELECT 1 FROM account LIMIT 1").fetchone()
        if bootstrap_path is not None and not already_provisioned:
            bootstrap_admin(accounts, bootstrap_path)
        return accounts
    finally:
        os.close(fd)
