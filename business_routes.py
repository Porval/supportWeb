"""Business endpoints from the pinned workbench HTTP contract."""
import re
from account_primitives import fail
from hosted_state import is_anonymous_actor
from queries import validate

_SEG = r'[^/]{1,200}'
ANONYMOUS_READ_PATTERNS = tuple(re.compile(pattern) for pattern in (
    r'/context\Z',
    r'/capabilities\Z',
    r'/catalogs\Z',
    r'/activities\Z',
    rf'/activities/{_SEG}\Z',
    rf'/activities/{_SEG}/products\Z',
    rf'/activities/{_SEG}/versions/{_SEG}\Z',
    rf'/activities/{_SEG}/versions/{_SEG}/history\Z',
    rf'/activities/{_SEG}/versions/{_SEG}/registration-drafts/(?:meituan|taobao_instant|jd)\Z',
    rf'/activities/{_SEG}/versions/{_SEG}/registration-drafts/(?:meituan|taobao_instant|jd)/final-list\Z',
    rf'/activities/{_SEG}/platforms/(?:meituan|taobao_instant)/sales-comparison\Z',
    rf'/activities/{_SEG}/platforms/{_SEG}(?:/materials(?:/{_SEG}/(?:comparison|download|check-screenshot))?)?\Z',
    r'/brands\Z',
    rf'/brands/{_SEG}\Z',
    rf'/brands/{_SEG}/activities\Z',
    rf'/brands/{_SEG}/products\Z',
    r'/products\Z',
    rf'/products/{_SEG}/activities\Z',
    r'/products/sales-history\Z',
    r'/exports/snapshot\Z',
    rf'/materials/{_SEG}(?:/download)?\Z',
))


def _anonymous_viewer(accounts, sid):
    with accounts.tx() as db:
        _, actor = accounts.valid(db, sid, True)
    return is_anonymous_actor(actor)


def dispatch_business(self, headers, path, q):
    if path=='/schedule' or path.startswith('/schedule/'):
        if _anonymous_viewer(self.server.accounts, self.token()):
            fail(403, 'FORBIDDEN', '匿名查看模式不可访问开发排期')
        from schedule_api import dispatch
        return dispatch(self,headers,path[len('/schedule'):],q)
    business=self.server.business
    if business and (path.startswith('/operations/') or path=='/account/users' or path.startswith('/account/users/')):
        from b_platform_materials import PlatformMaterials, ROUTE, SUBMIT, VERIFY, JSON_LIMIT
        sid=self.token()
        account_path=path.startswith('/account/')
        subpath=path[len('/account'):] if account_path else path[len('/operations'):]
        anonymous = _anonymous_viewer(self.server.accounts, sid)
        if anonymous:
            if self.command != 'GET':
                fail(403, 'ANONYMOUS_READ_ONLY', '匿名查看模式不支持修改操作，请使用管理员账号登录')
            if account_path:
                fail(403, 'FORBIDDEN', '匿名查看模式不可访问账号管理')
            if not any(pattern.fullmatch(subpath) for pattern in ANONYMOUS_READ_PATTERNS):
                fail(403, 'FORBIDDEN', '匿名查看模式仅支持查看已有活动版本')
        platform_route = not account_path and ROUTE.fullmatch(subpath)
        if platform_route and not business.platform_materials:
            with self.server.accounts.tx() as db:
                self.server.accounts.valid(db,sid,True)
            fail(503,'PLATFORM_MATERIALS_NOT_READY','平台资料迁移尚未就绪')
        if self.command=='GET':
            if account_path:
                with self.server.accounts.tx() as db:
                    _,actor=self.server.accounts.valid(db,sid,True);self.trace_actor=actor['id']
                    return business.profile_read(db,actor,subpath,q)
            result = business.read(sid,subpath,q,self)
            if subpath == '/capabilities':
                result['registrationDrafts']={'contractVersion':'activity-registration-draft/1.0','enabled':True,'formalListEnabled':True}
                result['platformMaterials'] = PlatformMaterials.capability(bool(business.platform_materials))
                if self.server.phone_otp:
                    result['authMethod'] = 'phone_otp'
                    result['blockedReasons'].pop('sms',None)
            if subpath in ('/capabilities','/context'):
                actions = result.get('allowedActions',result.get('permissions',[]))
                if business.platform_materials and 'platform_materials.write' in actions:
                    result.update(readOnly=False,businessReadOnly=False)
            if subpath == '/context':
                result['anonymous'] = anonymous
            return result
        if self.command not in ['POST','PUT']:fail(405,'METHOD_NOT_ALLOWED','不支持的方法')
        validate(q,set())
        platform_write = platform_route and self.command == 'POST' and SUBMIT.fullmatch(subpath)
        with self.server.accounts.tx() as db:
            _,actor=self.server.accounts.valid(db,sid,True);self.trace_actor=actor['id']
        self.server.accounts.csrf(sid,self.headers.get('X-CSRF-Token',''))
        if platform_write:
            with self.server.accounts.tx() as db:
                _,actor=self.server.accounts.valid(db,sid,True)
                business.platform_materials.authorize(db,actor,*platform_write.groups(),write=True,selected=False)
        body=self.body(JSON_LIMIT if subpath=='/materials' or platform_write or VERIFY.fullmatch(subpath) else 256*1024)
        if not isinstance(body,dict):fail(422,'INVALID_REQUEST','正文须为对象')
        if account_path and subpath=='/users' and self.command=='POST':
            with self.server.accounts.tx() as db:
                _,actor=self.server.accounts.valid(db,sid,True);self.trace_actor=actor['id']
                profile=self.server.accounts.create_user(db,actor,body)
            self.response_status=201
            return {'user':profile}
        status,result,replay=business.write(sid,subpath,self.command,body,self.headers.get('Idempotency-Key'))
        if account_path and self.command=='PUT' and not replay and isinstance(result,dict) and isinstance(result.get('user'),dict) and result.get('changed'):
            updated_user=result['user']
            with self.server.accounts.tx() as db:
                self.server.accounts.sync_role_grants(db,updated_user['id'],updated_user.get('businessRole'))
        self.response_status=status
        if replay:headers['Idempotency-Replayed']='true'
        return result
    fail(404, 'NOT_FOUND', '接口不存在')
