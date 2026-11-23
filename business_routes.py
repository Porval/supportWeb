"""Business endpoints from the pinned workbench HTTP contract."""
from account_primitives import fail
from queries import validate

def dispatch_business(self, headers, path, q):
    if path=='/schedule' or path.startswith('/schedule/'):
        from schedule_api import dispatch
        return dispatch(self,headers,path[len('/schedule'):],q)
    business=self.server.business
    if business and (path.startswith('/operations/') or path=='/account/users' or path.startswith('/account/users/')):
        from b_platform_materials import PlatformMaterials, ROUTE, SUBMIT, VERIFY, JSON_LIMIT
        sid=self.token()
        account_path=path.startswith('/account/')
        subpath=path[len('/account'):] if account_path else path[len('/operations'):]
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
        status,result,replay=business.write(sid,subpath,self.command,body,self.headers.get('Idempotency-Key'))
        self.response_status=status
        if replay:headers['Idempotency-Replayed']='true'
        return result
    fail(404, 'NOT_FOUND', '接口不存在')
