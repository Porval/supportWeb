"""JT020 isolated local test credentials. No normal Store or phone identities."""
import contextlib
from datetime import datetime,timezone
import fcntl
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import sqlite3
import tempfile
import time
import uuid
from account_primitives import APIError,fail,iso
from seed import dataset
from storage import prepare_db,Snapshots

ROOT=Path(__file__).resolve().parents[2]
EXPECTED=ROOT/'backend/ops-service/var/test'
USERNAME='local_reader'
ITERATIONS=600000
ACTIONS=['operations.read','operations.download']
FILES=['accounts.sqlite3','operations.sqlite3','session.key','credentials.json']
DDL='''
CREATE TABLE config(version INTEGER NOT NULL,key_sha256 TEXT NOT NULL);
CREATE TABLE account(id TEXT PRIMARY KEY,username TEXT UNIQUE NOT NULL,salt BLOB NOT NULL,verifier BLOB NOT NULL,algorithm TEXT NOT NULL,iterations INTEGER NOT NULL,derived_length INTEGER NOT NULL,credential_version INTEGER NOT NULL,created_at REAL NOT NULL,expires_at REAL NOT NULL,permissions TEXT NOT NULL,scope TEXT NOT NULL);
CREATE TABLE sessions(sid_mac TEXT PRIMARY KEY,account_id TEXT REFERENCES account(id),credential_version INTEGER,created_at REAL NOT NULL,expires_at REAL NOT NULL,revoked_at REAL);
CREATE TABLE rate_limits(scope TEXT PRIMARY KEY,window_start REAL NOT NULL,count INTEGER NOT NULL,blocked_until REAL NOT NULL DEFAULT 0);
'''

def inconsistent():fail(503,'TEST_STATE_INCONSISTENT','测试状态不一致；停止服务后由原Owner显式恢复或轮换，禁止清库重建')
class TestPaths:
    def __init__(self,directory):
        if str(directory)!=str(EXPECTED):fail(400,'INVALID_TEST_DIRECTORY','只准本工程精确var/test绝对目录')
        self.directory=EXPECTED;self.fixture=False;self.validate()
    @classmethod
    def _isolated_fixture(cls,directory):
        # Only programmatic offline checks can pass a fixture object; no CLI/env switch.
        p=Path(directory).resolve()
        if not any(parent.name.startswith('jt020-check-') for parent in [p,*p.parents]):raise ValueError('not an isolated fixture')
        if Path(tempfile.gettempdir()).resolve() not in p.parents:raise ValueError('fixture must be under temporary storage')
        obj=object.__new__(cls);obj.directory=p;obj.fixture=True;obj.validate();return obj
    def validate(self):
        p=self.directory
        for part in [p,*p.parents]:
            if part.is_symlink():fail(400,'INVALID_TEST_DIRECTORY','测试目录不允许链接或别名')
        for name in [*FILES,'.run.lock','state-v2.sqlite3','authority.json','.migration-b.json',*[x.name for x in p.glob('credentials-*.json')]]:
            child=p/name
            if child.is_symlink():inconsistent()
        if p.exists() and p.stat().st_mode & 0o777 != 0o700:inconsistent()
        for name in [*FILES,'state-v2.sqlite3','authority.json','.migration-b.json',*[x.name for x in p.glob('credentials-*.json')]]:
            child=p/name
            if child.exists() and (not child.is_file() or child.stat().st_mode & 0o777 != 0o600):inconsistent()
    @contextlib.contextmanager
    def exclusive(self):
        self.validate()
        if not self.directory.is_dir():inconsistent()
        fd=os.open(self.directory/'.run.lock',os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
        try:
            try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:fail(409,'TEST_PROCESS_ACTIVE','先停止本机test自有进程，不能在线初始化或轮换')
            yield
        finally:
            os.close(fd)

def derive(password,salt,iterations=ITERATIONS):return hashlib.pbkdf2_hmac('sha256',password.encode('utf-8'),salt,iterations,32)
def atomic_credentials(paths,value):
    path=paths.directory/'credentials.json'
    tmp=paths.directory/('.credentials-'+secrets.token_hex(12)+'.tmp')
    try:
        fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,'w') as f:
            json.dump(value,f,ensure_ascii=False);f.write('\n');f.flush();os.fsync(f.fileno())
        os.replace(tmp,path)
        directory_fd=os.open(paths.directory,os.O_RDONLY)
        try:os.fsync(directory_fd)
        finally:os.close(directory_fd)
    finally:
        if tmp.exists():tmp.unlink() # Only our own unpublished temporary file.

def credential_document(password,version,created,expires):
    return {'mode':'test','url':'http://127.0.0.1:52669/','username':USERNAME,'password':password,'credentialVersion':version,'createdAt':iso(created),'expiresAt':iso(expires)}

def synthetic_dataset():
    data=json.loads(json.dumps(dataset()))
    data['meta'].update(mode='test',dataOrigin='synthetic_test',datasetRevision='jt020-test-r1')
    def mark(value):
        if isinstance(value,dict):
            if 'provenance' in value:value['provenance'].update(kind='demo',datasetMode='test')
            if 'sourceKind' in value:value['sourceMode']='test'
            for child in list(value.values()):mark(child)
        elif isinstance(value,list):
            for child in value:mark(child)
    mark(data);return data

def initialize(paths,clock=time.time):
    os.umask(0o077);paths.validate()
    try:paths.directory.mkdir(parents=True,mode=0o700,exist_ok=False)
    except FileExistsError:fail(409,'TEST_STATE_EXISTS','测试目录已存在，init不会覆盖或重置任何状态')
    with paths.exclusive():
        key=secrets.token_bytes(32);password=secrets.token_urlsafe(24);salt=secrets.token_bytes(24)
        verifier=derive(password,salt);created=clock();expires=created+86400
        (paths.directory/'session.key').write_bytes(key)
        with contextlib.closing(sqlite3.connect(paths.directory/'accounts.sqlite3')) as db:
            db.execute('PRAGMA foreign_keys=ON');db.executescript(DDL)
            db.execute('INSERT INTO config VALUES(?,?)',(1,hashlib.sha256(key).hexdigest()))
            db.execute('INSERT INTO account VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',('test-'+str(uuid.uuid4()),USERNAME,salt,verifier,'pbkdf2_hmac_sha256',ITERATIONS,32,1,created,expires,json.dumps(ACTIONS),'*'))
            db.commit()
        business=synthetic_dataset();business['meta'].pop('mode') # prepare_db writes the authoritative mode column.
        prepare_db(paths.directory,business,mode='test')
        atomic_credentials(paths,credential_document(password,1,created,expires))
        return {'mode':'test','credentialsPath':str(paths.directory/'credentials.json'),'username':USERNAME,'credentialVersion':1,'expiresAt':iso(expires)}

class TestAccounts:
    def __init__(self,paths,clock=time.time,*,allow_delivery_inconsistent=False):
        self.paths=paths;self.clock=clock;paths.validate()
        if any(not (paths.directory/name).is_file() for name in FILES if not allow_delivery_inconsistent or name!='credentials.json'):inconsistent()
        try:
            self.key=(paths.directory/'session.key').read_bytes()
            if len(self.key)!=32:inconsistent()
            with self.tx() as db:
                if db.execute('PRAGMA quick_check').fetchone()[0]!='ok':inconsistent()
                config=db.execute('SELECT * FROM config').fetchall();account=db.execute('SELECT * FROM account').fetchall()
                if len(config)!=1 or config[0]['version']!=1 or config[0]['key_sha256']!=hashlib.sha256(self.key).hexdigest():inconsistent()
                if len(account)!=1:inconsistent()
                row=account[0];self.validate_account(row)
                if not allow_delivery_inconsistent:
                    doc=json.loads((paths.directory/'credentials.json').read_text())
                    if set(doc)!={'mode','url','username','password','credentialVersion','createdAt','expiresAt'}:inconsistent()
                    expected=credential_document(doc['password'],row['credential_version'],row['created_at'],row['expires_at'])
                    if doc!=expected or not isinstance(doc['password'],str) or not hmac.compare_digest(derive(doc['password'],row['salt']),row['verifier']):inconsistent()
            # Validation of mode before serving; no other data directory is accessed.
            snapshots=Snapshots(paths.directory,'test')
            with snapshots.read() as db:
                metadata=snapshots.load(db)['meta']
                if metadata.get('dataOrigin')!='synthetic_test' or metadata.get('datasetRevision')!='jt020-test-r1':inconsistent()
        except APIError:raise
        except (OSError,ValueError,KeyError,TypeError,sqlite3.Error):inconsistent()
    @staticmethod
    def validate_account(row):
        if not row['id'].startswith('test-') or row['username']!=USERNAME or row['algorithm']!='pbkdf2_hmac_sha256' or row['iterations']!=ITERATIONS or row['derived_length']!=32 or len(row['salt'])<16 or len(row['verifier'])!=32 or row['credential_version']<1 or row['expires_at']!=row['created_at']+86400:inconsistent()
        permissions=json.loads(row['permissions'])
        if not isinstance(permissions,list) or any(p not in ACTIONS for p in permissions) or row['scope']!='*':inconsistent()
    @contextlib.contextmanager
    def tx(self):
        path=self.paths.directory/'accounts.sqlite3'
        if not path.is_file() or path.is_symlink():inconsistent()
        db=sqlite3.connect(path.as_uri()+'?mode=rw',uri=True,timeout=3,isolation_level=None);db.row_factory=sqlite3.Row
        try:
            db.execute('PRAGMA busy_timeout=3000');db.execute('PRAGMA foreign_keys=ON');db.execute('BEGIN IMMEDIATE');yield db;db.commit()
        except BaseException:
            db.rollback();raise
        finally:db.close()
    def mac(self,purpose,value):return hmac.new(self.key,(purpose+':'+value).encode(),hashlib.sha256).hexdigest()
    def account(self,db):
        row=db.execute('SELECT * FROM account').fetchone()
        if row is None:inconsistent()
        self.validate_account(row);return row
    def lookup(self,db,sid):return db.execute('SELECT * FROM sessions WHERE sid_mac=?',(self.mac('sid',sid),)).fetchone() if sid else None
    def valid(self,db,sid,require_user=False):
        session=self.lookup(db,sid);account=self.account(db);now=self.clock()
        if not session:fail(401,'SESSION_REQUIRED','请登录隔离测试账号')
        if session['account_id'] and account['expires_at']<=now:fail(401,'TEST_CREDENTIAL_EXPIRED','测试账号已到期，请联系提供者')
        if session['revoked_at'] is not None or session['expires_at']<=now or session['account_id'] and (session['credential_version']!=account['credential_version'] or session['account_id']!=account['id']):fail(401,'SESSION_EXPIRED','测试登录已失效，请重新登录')
        if require_user and not session['account_id']:fail(401,'SESSION_REQUIRED','请登录隔离测试账号')
        return session,account
    def new_session(self,db,account=None):
        sid=secrets.token_urlsafe(32);now=self.clock();expires=min(now+28800,account['expires_at']) if account is not None else now+900
        db.execute('INSERT INTO sessions VALUES(?,?,?,?,?,NULL)',(self.mac('sid',sid),account['id'] if account is not None else None,account['credential_version'] if account is not None else None,now,expires))
        return sid
    def user(self,account):return {'id':account['id'],'username':USERNAME,'name':'本地只读体验','phone':None,'businessRole':None,'roleLabel':None}
    def view(self,db,sid,reason=None):
        session,account=self.valid(db,sid);authenticated=session['account_id'] is not None
        return {'authenticated':authenticated,'authMethod':'password','readOnly':True,'user':self.user(account) if authenticated else None,'permissions':json.loads(account['permissions']) if authenticated else [],'csrfToken':self.mac('csrf',sid),'absoluteExpiresAt':iso(session['expires_at']) if authenticated else None,'credentialExpiresAt':iso(account['expires_at']) if authenticated else None,'reason':reason}
    def rate(self,db,scope):
        return db.execute('SELECT * FROM rate_limits WHERE scope=?',(scope,)).fetchone()
    def rate_set(self,db,scope,start,count,blocked=0):db.execute('INSERT OR REPLACE INTO rate_limits VALUES(?,?,?,?)',(scope,start,count,blocked))
    def limited(self,until):
        seconds=max(1,int(until-self.clock()+.999));fail(429,'RATE_LIMITED','请求过于频繁，请稍后重试',{'retryAfterSeconds':seconds})
    def session_view(self,sid):
        with self.tx() as db:
            reason=None
            try:self.valid(db,sid)
            except APIError as e:
                reason=e.code if e.code!='SESSION_REQUIRED' else None
                row=self.rate(db,'anonymous-create');now=self.clock()
                start,count=(row['window_start'],row['count']) if row and row['window_start']+60>now else (now,0)
                if count>=30:self.limited(start+60)
                self.rate_set(db,'anonymous-create',start,count+1)
                sid=self.new_session(db)
            account=self.account(db)
            if account['expires_at']<=self.clock():reason='TEST_CREDENTIAL_EXPIRED'
            return self.view(db,sid,reason),sid,self.lookup(db,sid)['expires_at']
    def csrf(self,sid,value):
        if not hmac.compare_digest((value or '').encode(),self.mac('csrf',sid).encode()):fail(403,'CSRF_INVALID','请刷新测试登录状态后重试')
    def login(self,sid,username,password):
        if not isinstance(username,str) or not isinstance(password,str):fail(400,'INVALID_REQUEST','用户名和密码必须是字符串')
        try:password.encode('utf-8')
        except UnicodeError:fail(400,'INVALID_REQUEST','密码字符无效')
        deferred=None;result=None
        with self.tx() as db:
            session,account=self.valid(db,sid)
            if session['account_id']:fail(409,'ALREADY_AUTHENTICATED','测试账号已登录')
            if account['expires_at']<=self.clock():fail(401,'TEST_CREDENTIAL_EXPIRED','测试账号已到期，请联系提供者')
            bucket='known-user' if username==USERNAME else 'unknown-user';now=self.clock()
            for scope in [bucket,'global-login']:
                row=self.rate(db,scope)
                if row and row['blocked_until']>now:self.limited(row['blocked_until'])
            match=hmac.compare_digest(derive(password,account['salt']),account['verifier']) and username==USERNAME
            if not match:
                blocked=0
                for scope,maximum in [(bucket,5),('global-login',30)]:
                    row=self.rate(db,scope);start,count=(row['window_start'],row['count']) if row and row['window_start']+900>now else (now,0)
                    count+=1;until=now+900 if count>=maximum else 0;self.rate_set(db,scope,start,count,until);blocked=max(blocked,until)
                deferred=APIError(429 if blocked else 401,'RATE_LIMITED' if blocked else 'INVALID_CREDENTIALS','请求过于频繁，请稍后重试' if blocked else '账号或密码不正确',{'retryAfterSeconds':900} if blocked else {})
            else:
                db.execute('UPDATE sessions SET revoked_at=? WHERE sid_mac=?',(now,self.mac('sid',sid)))
                new=self.new_session(db,account);result=(self.view(db,new),new,self.lookup(db,new)['expires_at'])
        if deferred:raise deferred # Failed counters committed before HTTP error.
        return result
    def logout(self,sid):
        with self.tx() as db:
            row=self.lookup(db,sid)
            if row is None:fail(401,'SESSION_REQUIRED','测试会话不存在')
            db.execute('UPDATE sessions SET revoked_at=COALESCE(revoked_at,?) WHERE sid_mac=?',(self.clock(),self.mac('sid',sid)))
        return {'loggedOut':True}
    def authorize(self,sid,action):
        with self.tx() as db:
            _,account=self.valid(db,sid,True)
            required=['operations.read',action] if action=='operations.download' else [action]
            if action not in ACTIONS or any(item not in json.loads(account['permissions']) for item in required):fail(403,'FORBIDDEN','测试账号没有该动作权限')
            return self.user(account),json.loads(account['permissions'])

def rotate(paths,clock=time.time):
    if (paths.directory/'authority.json').exists() or (paths.directory/'state-v2.sqlite3').exists():fail(409,'V2_AUTHORITY_REQUIRED','已迁移状态必须通过v2目标用户轮换，不写旧库')
    paths.validate()
    with paths.exclusive():
        state=TestAccounts(paths,clock,allow_delivery_inconsistent=True)
        password=secrets.token_urlsafe(24);salt=secrets.token_bytes(24);verifier=derive(password,salt);created=clock();expires=created+86400
        with state.tx() as db:
            account=state.account(db);version=account['credential_version']+1
            db.execute('UPDATE account SET salt=?,verifier=?,credential_version=?,created_at=?,expires_at=?',(salt,verifier,version,created,expires))
            db.execute('UPDATE sessions SET revoked_at=COALESCE(revoked_at,?)',(created,))
        atomic_credentials(paths,credential_document(password,version,created,expires))
        return {'mode':'test','credentialsPath':str(paths.directory/'credentials.json'),'username':USERNAME,'credentialVersion':version,'expiresAt':iso(expires)}
