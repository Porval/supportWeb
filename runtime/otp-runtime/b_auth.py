"""Multi-user test authentication backed solely by state-v2.sqlite3."""
import contextlib
import hashlib
import hmac
import json
import secrets
import time
from account_primitives import APIError,fail,iso,ROLES
from test_accounts import TestAccounts,ITERATIONS,inconsistent,derive
from b_state import connect,credential_path,document,private_atomic,canon,PROVISION
from b_authorization import AdministratorAuthorization,ADMIN_ACTIONS

READONLY_ROLES={'business_development','supply_chain','finance','other',None}
class BAccounts(AdministratorAuthorization,TestAccounts):
    def __init__(self,paths,clock=time.time,*,allow_delivery_inconsistent=False):
        self.paths=paths;self.clock=clock;paths.validate()
        for file in ['state-v2.sqlite3','authority.json','session.key']:
            p=paths.directory/file
            if p.is_symlink() or not p.is_file() or p.stat().st_mode & 0o777 != 0o600:inconsistent()
        self.key=(paths.directory/'session.key').read_bytes()
        if len(self.key)!=32:inconsistent()
        with self.tx() as db:
            if db.execute('PRAGMA quick_check').fetchone()[0]!='ok':inconsistent()
            config=db.execute('SELECT * FROM config').fetchone()
            if config['key_sha256']!=hashlib.sha256(self.key).hexdigest():inconsistent()
            meta={r[0]:json.loads(r[1]) for r in db.execute('SELECT * FROM b_meta')}
            if meta.get('schema_version')!=2 or meta.get('authority')!='state-v2':inconsistent()
            marker=json.loads((paths.directory/'authority.json').read_text())
            if marker.get('authority')!='state-v2' or marker.get('migrationId')!=meta['migrationId']:inconsistent()
            if not allow_delivery_inconsistent and db.execute("SELECT 1 FROM provision_batches WHERE state!='ready'").fetchone():inconsistent()
            for row in db.execute('SELECT * FROM account').fetchall():
                self.validate_account(row)
                if not allow_delivery_inconsistent:
                    path=credential_path(paths,row['username'])
                    if not path.is_file() or path.is_symlink() or path.stat().st_mode & 0o777 != 0o600:inconsistent()
                    doc=json.loads(path.read_text())
                    if doc!=document(doc.get('password'),row['username'],row['credential_version'],row['created_at'],row['expires_at']) or not isinstance(doc.get('password'),str) or not hmac.compare_digest(derive(doc['password'],row['salt']),row['verifier']):inconsistent()
    @contextlib.contextmanager
    def tx(self):
        db=connect(self.paths)
        try:db.execute('BEGIN IMMEDIATE');yield db;db.commit()
        except BaseException:db.rollback();raise
        finally:db.close()
    @staticmethod
    def validate_account(row):
        import uuid
        try:uuid.UUID(row['id'][5:])
        except (ValueError,TypeError):inconsistent()
        if not row['id'].startswith('test-') or row['username'] not in {'local_reader','local_store_ops',*PROVISION} or row['algorithm']!='pbkdf2_hmac_sha256' or row['iterations']!=ITERATIONS or row['derived_length']!=32 or len(row['salt'])<16 or len(row['verifier'])!=32 or row['expires_at']!=row['created_at']+86400 or row['business_role'] not in {*ROLES,None}:inconsistent()
    def account(self,db,uid=None):
        row=db.execute('SELECT * FROM account WHERE id=?' if uid else "SELECT * FROM account WHERE username='local_reader'",(uid,) if uid else ()).fetchone()
        if row is None:inconsistent()
        self.validate_account(row);return row
    def permissions(self,db,account):
        actions={r[0] for r in db.execute('SELECT action FROM explicit_grants WHERE user_id=?',(account['id'],))}|{r[0] for r in db.execute('SELECT permission FROM system_permissions WHERE user_id=?',(account['id'],))}
        if self.full_access_enabled(db,account):return sorted(actions | ADMIN_ACTIONS)
        if account['business_role'] in READONLY_ROLES:
            actions={a for a in actions if not a.startswith(('submissions.create','submissions.edit','activities.propose','reviews.decide'))}
            actions.discard('platform_materials.write')
        if account['business_role']=='pricing':actions-={'submissions.create','submissions.edit_own','activities.propose'}
        return sorted(actions)
    def require(self,db,account,action,resource='*'):
        if action not in self.permissions(db,account):fail(403,'FORBIDDEN','当前身份没有该动作权限')
        if action=='accounts.manage' or action in ADMIN_ACTIONS and self.full_access_enabled(db,account):return
        rows={r[0] for r in db.execute('SELECT resource_id FROM explicit_grants WHERE user_id=? AND action=?',(account['id'],action))}
        if '*' not in rows and resource not in rows:fail(403,'FORBIDDEN','当前身份没有该数据范围')
    def valid(self,db,sid,require_user=False):
        session=self.lookup(db,sid)
        if not session:fail(401,'SESSION_REQUIRED','请登录本地测试账号')
        account=self.account(db,session['account_id']) if session['account_id'] else self.account(db)
        if session['account_id'] and account['expires_at']<=self.clock():fail(401,'TEST_CREDENTIAL_EXPIRED','该测试账号已到期')
        if session['revoked_at'] is not None or session['expires_at']<=self.clock() or session['account_id'] and (session['credential_version']!=account['credential_version'] or account['status']!='active'):fail(401,'SESSION_EXPIRED','登录已失效')
        if require_user and not session['account_id']:fail(401,'SESSION_REQUIRED','请登录本地测试账号')
        return session,self.bind_authorization(db,account)
    def user(self,row):return {'id':row['id'],'username':row['username'],'name':row['name'],'phone':row['phone'],'businessRole':row['business_role'],'roleLabel':ROLES.get(row['business_role']),'profileVersion':row['profile_version']}
    def view(self,db,sid,reason=None):
        session,account=self.valid(db,sid);authenticated=session['account_id'] is not None
        actions=self.permissions(db,account) if authenticated else []
        writes=any(a in actions for a in ['accounts.manage','submissions.create','submissions.edit_own','activities.propose','reviews.decide'])
        return {'authenticated':authenticated,'authMethod':'password','readOnly':not writes,'user':self.user(account) if authenticated else None,'permissions':actions,'csrfToken':self.mac('csrf',sid),'absoluteExpiresAt':iso(session['expires_at']) if authenticated else None,'credentialExpiresAt':iso(account['expires_at']) if authenticated else None,'reason':reason,'profileVersion':account['profile_version'] if authenticated else None,'capabilities':{'contractVersion':'1.1','allowedActions':actions,'fullAccessAdministrator':authenticated and self.is_review_admin(account),'businessReadOnly':not any(a in actions for a in ['submissions.create','reviews.decide','activities.propose'])}}
    def session_view(self,sid):
        with self.tx() as db:
            reason=None
            try:self.valid(db,sid)
            except APIError as e:
                reason=e.code if e.code!='SESSION_REQUIRED' else None;now=self.clock();r=self.rate(db,'anonymous-create');start,count=(r['window_start'],r['count']) if r and r['window_start']+60>now else (now,0)
                if count>=30:self.limited(start+60)
                self.rate_set(db,'anonymous-create',start,count+1);sid=self.new_session(db)
            return self.view(db,sid,reason),sid,self.lookup(db,sid)['expires_at']
    def login(self,sid,username,password):
        if not isinstance(username,str) or not isinstance(password,str):fail(422,'INVALID_REQUEST','用户名密码须为字符串')
        deferred=None;result=None
        with self.tx() as db:
            session,_=self.valid(db,sid)
            if session['account_id']:fail(409,'ALREADY_AUTHENTICATED','已登录，请先退出')
            row=db.execute('SELECT * FROM account WHERE username=?',(username,)).fetchone()
            target=row or self.account(db);bucket='known-user' if username=='local_reader' else 'user:'+row['id'] if row else 'unknown-user';now=self.clock()
            if row and row['expires_at']<=now:fail(401,'TEST_CREDENTIAL_EXPIRED','该测试账号已到期')
            if row and row['status']!='active':fail(401,'SESSION_EXPIRED','账号不可用')
            for scope in [bucket,'global-login']:
                r=self.rate(db,scope)
                if r and r['blocked_until']>now:self.limited(r['blocked_until'])
            match=hmac.compare_digest(derive(password,target['salt']),target['verifier']) and row is not None
            if not match:
                blocked=0
                for scope,maximum in [(bucket,5),('global-login',30)]:
                    r=self.rate(db,scope);start,count=(r['window_start'],r['count']) if r and r['window_start']+900>now else (now,0);count+=1;until=now+900 if count>=maximum else 0;self.rate_set(db,scope,start,count,until);blocked=max(blocked,until)
                deferred=APIError(429 if blocked else 401,'RATE_LIMITED' if blocked else 'INVALID_CREDENTIALS','请求过于频繁' if blocked else '账号或密码不正确',{'retryAfterSeconds':900} if blocked else {})
            else:
                db.execute('UPDATE sessions SET revoked_at=? WHERE sid_mac=?',(now,self.mac('sid',sid)));new=self.new_session(db,row);result=(self.view(db,new),new,self.lookup(db,new)['expires_at'])
        if deferred:raise deferred
        return result
    def authorize(self,sid,action):
        with self.tx() as db:
            _,row=self.valid(db,sid,True);self.require(db,row,action)
            if action=='operations.download':self.require(db,row,'operations.read')
            return self.user(row),self.permissions(db,row)

def rotate_target(paths,username='local_reader',clock=time.time):
    with paths.exclusive():
        state=BAccounts(paths,clock,allow_delivery_inconsistent=True);password=secrets.token_urlsafe(24);salt=secrets.token_bytes(24);created=clock()
        with state.tx() as db:
            row=db.execute('SELECT * FROM account WHERE username=?',(username,)).fetchone()
            if row is None:fail(404,'USER_NOT_FOUND','测试人员不存在')
            version=row['credential_version']+1
            db.execute('UPDATE account SET salt=?,verifier=?,credential_version=?,created_at=?,expires_at=? WHERE id=?',(salt,derive(password,salt),version,created,created+86400,row['id']))
            revoke='UPDATE sessions SET revoked_at=COALESCE(revoked_at,?) WHERE account_id=?'
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='test_otp_sessions'").fetchone():
                revoke+=' AND sid_mac NOT IN (SELECT sid_mac FROM test_otp_sessions)'
            db.execute(revoke,(created,row['id']))
        private_atomic(credential_path(paths,username),document(password,username,version,created,created+86400))
        return {'username':username,'credentialsPath':str(credential_path(paths,username)),'credentialVersion':version,'expiresAt':iso(created+86400)}
