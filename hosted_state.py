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

from account_primitives import ROLES, fail, iso
from b_auth import BAccounts
from b_state import DDL as BUSINESS_DDL, MARKER, STATE, canon
from storage import prepare_db
from test_accounts import DDL as ACCOUNT_DDL, ITERATIONS


NAMESPACE = "jt024-workbench-v1"
ORIGIN = "hosted_workbench"
MAX_EXPIRY = 253402214400  # An untimed administrator credential, valid through 9999-12-30.
PROFILE_COLUMNS = (
    "business_role TEXT", "name TEXT", "remark TEXT", "phone TEXT",
    "status TEXT NOT NULL DEFAULT 'active'", "profile_version INTEGER NOT NULL DEFAULT 1",
)


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

    def valid(self, db, sid, require_user=False):
        row = self.lookup(db, sid)
        if row is not None and row["account_id"] is None:
            if row["revoked_at"] is not None or row["expires_at"] <= self.clock():
                fail(401, "SESSION_EXPIRED", "登录会话已失效")
            if require_user:
                fail(401, "SESSION_REQUIRED", "请登录工作台")
            return row, None
        return super().valid(db, sid, require_user)

    def review_account_active(self, db, row):
        return row["status"] == "active" and row["expires_at"] > self.clock()

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
