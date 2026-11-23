#!/usr/bin/env python3
"""JT020 test-only password API. Never constructs normal/demo authentication."""
import argparse
from http.cookies import SimpleCookie,CookieError
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
import sqlite3
import sys
import time
import uuid
from urllib.parse import urlsplit,parse_qs
from account_primitives import APIError,fail,iso,ROLES
from test_accounts import TestAccounts,TestPaths,EXPECTED
from storage import Snapshots
from queries import Query,TYPES,PLATFORMS,validate

PREFIX='/api/test/v1'
ORIGIN='http://127.0.0.1:52669'
COOKIE='jt020_test_session'
NOTICE='隔离本地测试／合成数据／仅查询导出'

def audit_event(method,path,status,rid,elapsed,code=None,actor=None,command=None):
    static={'api','test','v1','account','operations','health','session','login','logout','users','profile','context','capabilities','catalogs','activities','brands','products','materials','download','exports','snapshot','submissions','draft','analysis-runs','cancel','confirm','reviews','versions','history','decisions','start-review','commands','schedule','boards','shared','requirements','move','comments','images','delete','reset-preview','reset'}
    route='/'.join(p if p in static else ':id' for p in path.split('?')[0].split('/') if p)
    return {'event':'http.request','requestId':rid,'mode':'test','method':method,'routeTemplate':'/'+route,'status':status,'durationMs':round(elapsed*1000,3),'errorCode':code,'actorId':actor,'commandId':command}


class TestService(ThreadingHTTPServer):
    daemon_threads=True
    def __init__(self,address,*,paths,origin=ORIGIN,clock=time.time,cookie_name=COOKIE,phone_otp=False):
        if address[0]!='127.0.0.1' or origin!=ORIGIN:raise ValueError('test is exact loopback origin only')
        if paths.fixture and address[1]!=0:raise ValueError('offline fixture only uses a random port')
        self.request_logging=not paths.fixture
        self.mode='test';self.public_origin=ORIGIN;self.cookie_name=cookie_name;self.clock=clock;self.phone_otp=phone_otp
        self.lock_context=paths.exclusive();self.lock_context.__enter__();self.lock_held=True
        try:
            from b_state import STATE,MARKER
            self.business=None;self.jobs=None;self.product_recognition=None;self.model_recognition=None;self.primary_recognition=None
            if (paths.directory/STATE).exists() or (paths.directory/MARKER).exists():
                from b_auth import BAccounts
                from b_jobs import Jobs
                from b_api import Business
                from b_platform_materials import PlatformMaterials, schema_ready, migrate as platform_migrate
                from api import decode_material
                if phone_otp:
                    from b_test_otp import OtpAccounts
                    self.accounts=OtpAccounts(paths,clock)
                else:
                    self.accounts=BAccounts(paths,clock)
                with self.accounts.tx() as db:
                    platform_migrate(db)
                    platforms = PlatformMaterials(self.accounts, clock, decode_material, PREFIX + '/operations') if schema_ready(db) else None
                from b_model_recognition import ModelRecognition
                from b_model_worker import ModelWorker
                from b_deepseek_local import settings_path
                from pathlib import Path
                import os
                local_only = os.environ.get('JIATAN_LOCAL_ACTIVITY_INTAKE') == '1'
                self.model_recognition = self.primary_recognition = model_agent = None
                if not local_only:
                    from b_primary_recognition import PrimaryRecognition
                    from b_primary_worker import PrimaryWorker
                    from b_model_intake import ModelIntakeWorker
                    deepseek_settings = settings_path(paths.directory)
                    agent_root = Path(__file__).parent / 'released/business_agent'
                    self.model_recognition = ModelRecognition(self.accounts, clock, ModelWorker(
                        agent_root / 'workbench_deepseek', deepseek_settings))
                    self.primary_recognition = PrimaryRecognition(self.accounts, clock, PrimaryWorker(
                        agent_root / 'activity_primary_recognition', deepseek_settings))
                    model_agent = ModelIntakeWorker(agent_root / 'activity_model_first', deepseek_settings)
                self.jobs = Jobs(self.accounts, clock, model_agent=model_agent, local_only=local_only)
                from b_sales_worker import SalesWorker, SETTINGS_ENV as SALES_SETTINGS_ENV
                sales_worker = SalesWorker(Path(__file__).parent/'released/business_agent/activity_sales_comparison',
                                           os.environ.get(SALES_SETTINGS_ENV))
                self.business=Business(self.accounts,self.jobs,clock,require_activity_name=True,
                                       allow_submission_delete=True,allow_submission_declarations=True,
                                       first_review_start=True,platform_materials=platforms,model_recognition=self.model_recognition,primary_recognition=self.primary_recognition,
                                       activity_date_decoder=decode_material,sales_worker=sales_worker)
                from b_product_recognition import ProductRecognition
                self.product_recognition=ProductRecognition(
                    __import__('os').environ.get('JIATAN_UPC_SOURCE', 'data/product-upc.json'),
                    paths.directory/'product-recognition')
                self.business.product_recognition=self.product_recognition
                from b_supply_prices import AgentSupplyPrices
                from business_agent.supply_price_evidence.adapter import load_export, lookup_supply_prices
                from pathlib import Path
                import hashlib
                supply_module=Path(__file__).parent/'released/business_agent/supply_price_evidence/adapter.py'
                self.business.supply_prices=AgentSupplyPrices(
                    __import__('os').environ.get('JIATAN_SUPPLY_SOURCE','data/supply-prices.xls'),
                    load_export,lookup_supply_prices,hashlib.sha256(supply_module.read_bytes()).hexdigest())
                from b_product_metrics import AgentProductMetrics
                metrics_data=Path(__file__).parent/'released/data'
                self.business.product_metrics=AgentProductMetrics(metrics_data/'meituan-sales-20261111.json',
                    metrics_data/'meituan-sales-binding.json',clock)
                from b_registration_draft import migrate
                from b_registration_review import migrate as review_registration_migrate
                with self.accounts.tx() as db:
                    migrate(db)
                    review_registration_migrate(db)
                    from b_coupon_review import migrate as coupon_selection_migrate
                    coupon_selection_migrate(db)
                self.product_recognition.start()
                self.snapshots=None
            else:
                if phone_otp:fail(503,'TEST_OTP_NOT_READY','测试手机号绑定尚未就绪')
                self.accounts=TestAccounts(paths,clock);self.snapshots=Snapshots(paths.directory,'test')
            super().__init__(address,TestHandler)
        except BaseException:
            if self.primary_recognition:self.primary_recognition.close()
            if self.model_recognition:self.model_recognition.close()
            if self.product_recognition:self.product_recognition.close()
            if self.jobs:self.jobs.close()
            self.release_lock();raise
    def release_lock(self):
        if self.lock_held:self.lock_held=False;self.lock_context.__exit__(None,None,None)
    def server_close(self):
        try:
            if self.primary_recognition:self.primary_recognition.close()
            if self.model_recognition:self.model_recognition.close()
            if self.product_recognition:self.product_recognition.close()
            if self.jobs:self.jobs.close()
            super().server_close()
        finally:self.release_lock()
    def get_request(self):
        connection,address=super().get_request();connection.settimeout(5);return connection,address

class TestHandler(BaseHTTPRequestHandler):
    server_version='JT020Test/1'
    def log_message(self,*args):pass
    def do_GET(self):self.api()
    def do_POST(self):self.api()
    def do_PUT(self):self.api()
    def do_PATCH(self):self.api()
    def do_DELETE(self):self.api()
    def do_HEAD(self):self.api()
    def do_OPTIONS(self):self.api()
    def cookie(self,sid='',expires=None,clear=False):
        ttl=0 if clear else max(0,int(expires-self.server.clock()))
        return f'{self.server.cookie_name}={sid}; Path={PREFIX}; HttpOnly; SameSite=Strict; Max-Age={ttl}'
    def token(self):
        try:
            jar=SimpleCookie(self.headers.get('Cookie',''));return jar[self.server.cookie_name].value if self.server.cookie_name in jar else ''
        except CookieError:return ''
    def api(self):
        rid=str(uuid.uuid4());self.request_id=rid;headers={};started=time.monotonic();self.trace_actor=None
        self.meta={'mode':'test','dataOrigin':'synthetic_test','datasetRevision':'jt020-test-r1','fetchedAt':iso(self.server.clock()),'complete':False,'warnings':[NOTICE]}
        if urlsplit(self.path).path.startswith(PREFIX+'/schedule'):
            from schedule_api import safe_meta
            self.meta=safe_meta(self.server.clock)
        self.response_status=200;self.binary_response=None
        try:data=self.dispatch(headers);status=self.response_status;payload={'data':data,'requestId':rid,'meta':self.meta}
        except APIError as e:
            if self.meta.get('dataOrigin')=='local_schedule':
                self.meta['complete']=False
                if e.status in [401,403]:
                    from schedule_api import safe_meta
                    self.meta=safe_meta(self.server.clock)
            status=e.status;payload={'error':{'code':e.code,'message':e.message,'details':e.details},'requestId':rid,'meta':self.meta}
            if status==429:headers['Retry-After']=str(e.details.get('retryAfterSeconds',1))
        except sqlite3.Error as e:
            status=503;payload={'error':{'code':'DATABASE_BUSY' if 'locked' in str(e) else 'TEST_STATE_INCONSISTENT','message':'测试状态暂不可用，请联系提供者','details':{}},'requestId':rid,'meta':self.meta}
        except Exception:
            status=500;payload={'error':{'code':'INTERNAL_ERROR','message':'测试服务处理失败','details':{}},'requestId':rid,'meta':self.meta}
        if self.server.request_logging:
            command=payload.get('data',{}).get('commandId') if isinstance(payload.get('data'),dict) else None
            event=audit_event(self.command,self.path,status,rid,time.monotonic()-started,payload.get('error',{}).get('code'),self.trace_actor,command)
            try:print(json.dumps(event,ensure_ascii=False),file=sys.stderr,flush=True)
            except OSError:pass
        raw=json.dumps(payload,ensure_ascii=False,allow_nan=False).encode()
        if self.binary_response is not None and status<300:
            raw,binary_headers=self.binary_response;headers.update(binary_headers)
        self.send_response(status)
        for k,v in {'Content-Type':'application/json; charset=utf-8','Content-Length':str(len(raw)),'Cache-Control':'no-store','X-Content-Type-Options':'nosniff','Connection':'close',**headers}.items():self.send_header(k,v)
        self.end_headers();self.close_connection=True
        try:
            if self.command!='HEAD':self.wfile.write(raw)
        except (BrokenPipeError,ConnectionResetError):pass
    def body(self,limit=4096):
        if self.headers.get('Origin')!=ORIGIN:fail(403,'ORIGIN_DENIED','请从隔离本机测试入口操作')
        if self.headers.get_content_type()!='application/json' or self.headers.get('Transfer-Encoding'):fail(400,'INVALID_REQUEST','仅支持JSON')
        length=self.headers.get('Content-Length','')
        if not length.isdigit() or not 0<int(length):fail(400,'INVALID_REQUEST','缺少有效JSON长度')
        if int(length)>limit:fail(400 if limit==4096 else 413,'INVALID_REQUEST' if limit==4096 else 'INPUT_LIMIT','JSON超过此路由预算')
        def pairs(items):
            result={}
            for k,v in items:
                if k in result:raise ValueError()
                result[k]=v
            return result
        try:
            value=json.loads(self.rfile.read(int(length)).decode('utf-8'),object_pairs_hook=pairs,parse_constant=lambda _:(_ for _ in ()).throw(ValueError()))
            json.dumps(value,ensure_ascii=False).encode('utf-8')
            return value
        except (ValueError,UnicodeError):fail(400 if limit==4096 else 422,'INVALID_REQUEST','JSON无效')
    def dispatch(self,headers):
        if self.headers.get('Host')!=f'127.0.0.1:{self.server.server_port}':fail(403,'ORIGIN_DENIED','Host不合法')
        origin=self.headers.get('Origin')
        if self.headers.get('Sec-Fetch-Site')=='cross-site' or origin is not None and origin!=ORIGIN:fail(403,'ORIGIN_DENIED','来源不属于本机测试入口')
        split=urlsplit(self.path)
        if len(split.query)>8192:fail(422,'INVALID_FILTER','查询过长')
        parsed=parse_qs(split.query,keep_blank_values=True)
        if any(len(v)!=1 for v in parsed.values()):fail(422,'INVALID_FILTER','查询键重复')
        q={k:v[0] for k,v in parsed.items()}
        if not split.path.startswith(PREFIX+'/'):fail(404,'NOT_FOUND','测试模式不提供该路由')
        path=split.path[len(PREFIX):]
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
        if path.startswith('/operations/'):
            if self.command!='GET':fail(405,'METHOD_NOT_ALLOWED','测试仅支持查询及导出')
            subpath=path[len('/operations'):]
            action='operations.download' if subpath=='/exports/snapshot' or subpath.startswith('/materials/') else 'operations.read'
            actor,permissions=self.server.accounts.authorize(self.token(),action)
            with self.server.snapshots.read() as db:
                query=Query(self.server.snapshots.load(db),'demo',self.server.clock())
                query.meta.update(mode='test',dataOrigin='synthetic_test',warnings=[NOTICE,'报名/销量/费用来源未接入，未知不按0'])
                self.meta=query.meta
                if subpath=='/context':
                    validate(q,set());return {'mode':'test','authenticated':True,'readOnly':True,'actor':actor,'permissions':permissions,'dataOrigin':'synthetic_test','datasetRevision':'jt020-test-r1'}
                if subpath=='/capabilities':
                    validate(q,set());return {'readOnly':True,'implemented':['activities','brands','products','details','exports.snapshot'],'authMethod':'password','blockedReasons':{'accountManagement':'TEST_READ_ONLY','submissions':'TEST_READ_ONLY','reviews':'TEST_READ_ONLY','sms':'TEST_PASSWORD_ONLY'}}
                if subpath=='/catalogs':
                    validate(q,set());return {'activityTypes':TYPES,'platforms':PLATFORMS,'brands':query.data['brands'],'roles':[{'code':k,'label':v} for k,v in ROLES.items()]}
                return query.route(subpath,q)
        allowed={'/account/health':'GET','/account/session':'GET','/account/logout':'POST'}
        otp_paths={'/account/auth/otp/request':{'phone'},'/account/auth/otp/verify':{'challengeId','code'}}
        if self.server.phone_otp:
            allowed.update(dict.fromkeys(otp_paths,'POST'))
        else:
            allowed['/account/login']='POST'
        if path not in allowed:fail(404,'NOT_FOUND','测试不提供账号管理、OTP或其他认证路由')
        if self.command!=allowed[path]:fail(405,'METHOD_NOT_ALLOWED','请求方法不支持')
        validate(q,set())
        state=self.server.accounts;sid=self.token()
        if path=='/account/health':
            auth_method='phone_otp' if self.server.phone_otp else 'password'
            return {'status':'ok','authMethod':auth_method,'readOnly':not bool(business),'schemaVersion':2 if business else 1,'contractVersion':'1.1' if business else '1.0'}
        if path=='/account/session':
            view,new,expires=state.session_view(sid);headers['Set-Cookie']=self.cookie(new,expires);return view
        body=self.body(8192 if self.server.phone_otp else 4096)
        fields={'username','password'} if path=='/account/login' else otp_paths.get(path,set())
        if not isinstance(body,dict) or set(body)!=fields:fail(400,'INVALID_REQUEST','请求字段缺失或未知')
        with state.tx() as db:
            if self.server.phone_otp:state.valid(db,sid)
            elif not state.lookup(db,sid):fail(401,'SESSION_REQUIRED','请先读取测试登录状态')
        state.csrf(sid,self.headers.get('X-CSRF-Token',''))
        if path=='/account/logout':
            result=state.logout(sid);headers['Set-Cookie']=self.cookie(clear=True);return result
        if path=='/account/auth/otp/request':
            return state.request_otp(sid,body,self.client_address[0])
        if path=='/account/auth/otp/verify':
            view,new,expires=state.verify_otp(sid,body);headers['Set-Cookie']=self.cookie(new,expires);return view
        view,new,expires=state.login(sid,body['username'],body['password']);headers['Set-Cookie']=self.cookie(new,expires);return view

class SafeParser(argparse.ArgumentParser):
    def error(self,message):self.exit(2,'INVALID_REQUEST: 测试服务参数不合法，不回显输入\n')

def main():
    parser=SafeParser(description=__doc__)
    parser.add_argument('--mode',choices=['test'],default='test');parser.add_argument('--host',choices=['127.0.0.1'],default='127.0.0.1');parser.add_argument('--port',type=int,default=52670);parser.add_argument('--public-origin',choices=[ORIGIN],default=ORIGIN);parser.add_argument('--data-dir',default=str(EXPECTED))
    parser.add_argument('--phone-otp',action='store_true')
    args=parser.parse_args()
    try:
        with TestService((args.host,args.port),paths=TestPaths(args.data_dir),origin=args.public_origin,phone_otp=args.phone_otp) as service:
            print(json.dumps({'mode':'test','port':service.server_port,'pid':__import__('os').getpid(),'authMethod':'phone_otp' if args.phone_otp else 'password','readOnly':not bool(service.business),'schemaVersion':2 if service.business else 1}),flush=True)
            try:service.serve_forever()
            except KeyboardInterrupt:pass
    except APIError as e:parser.exit(1,e.code+': '+e.message+'\n')
    except Exception:parser.exit(1,'TEST_STATE_INCONSISTENT: 测试服务未启动，请核对状态及端口，不清库或换端口\n')
if __name__=='__main__':main()
