"""Same-origin HTTPS workbench served behind the site's TLS gateway."""
import json
import logging
import mimetypes
import os
import sqlite3
import sys
import tempfile
import time
import uuid
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from workbench_settings import ADMIN_PHONE, PUBLIC_HOSTS, PUBLIC_ORIGIN

ROOT = Path(__file__).resolve().parent
API = '/api/activity/v1'
ORIGIN = PUBLIC_ORIGIN
BUILD = 'jt024-hosted-20261124.5'
PUBLIC = ROOT / 'front'
sys.path.insert(0, str(ROOT / 'runtime'))


def unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate field')
        result[key] = value
    return result


class Handler(BaseHTTPRequestHandler):
    server_version = 'ActivityWorkbench'
    sys_version = ''

    def log_message(self, *args):
        pass

    def token(self):
        try:
            cookie = SimpleCookie(self.headers.get('Cookie', ''))
            item = cookie.get('activity_session')
            return item.value if item else ''
        except CookieError:
            return ''

    def session_capabilities(self, view):
        view['authMethod'] = 'phone_otp' if self.server.phone_otp else 'password'
        if self.server.phone_otp:
            view['sms'] = {'status': 'configured'}
        flags = view.setdefault('capabilities', {})
        flags['anonymousReadOnly'] = True
        flags['activityAgentChat'] = (view.get('authenticated') is True
            and view.get('anonymous') is not True
            and flags.get('fullAccessAdministrator') is True
            and (view.get('user') or {}).get('username') == ADMIN_PHONE)
        return view

    def cookie(self, sid='', expires=0):
        age = max(0, int(expires - self.server.clock()))
        return f'activity_session={sid}; Path=/api/activity/v1; HttpOnly; Secure; SameSite=Strict; Max-Age={age}'

    def body(self, limit=4096):
        from account_primitives import fail
        if self.headers.get('Origin') != ORIGIN:
            fail(403, 'ORIGIN_DENIED', '请从活动工作台提交')
        length = self.headers.get('Content-Length', '')
        if (self.headers.get_content_type() != 'application/json'
                or self.headers.get('Transfer-Encoding') or not length.isascii()
                or not length.isdigit() or len(length) > 10 or int(length) == 0):
            fail(400, 'INVALID_REQUEST', '请求需要有效的 JSON 正文')
        if int(length) > limit:
            fail(413, 'INPUT_LIMIT', '请求超过本接口允许的大小')
        try:
            data = json.loads(self.rfile.read(int(length)).decode(), object_pairs_hook=unique_pairs)
            json.dumps(data, ensure_ascii=False, allow_nan=False).encode()
            return data
        except (ValueError, UnicodeError, RecursionError):
            fail(400, 'INVALID_REQUEST', '请求的 JSON 格式无效')

    def account(self, path, query, headers):
        from account_primitives import fail
        otp = self.server.phone_otp
        post_fields = {'/account/logout': set(), '/account/auth/anonymous': set()}
        if otp:
            post_fields.update({'/account/auth/otp/request': {'phone'},
                                '/account/auth/otp/verify': {'challengeId', 'code'}})
        else:
            post_fields['/account/login'] = {'username', 'password'}
        get_paths = ('/account/session', '/account/health')
        if path not in post_fields and path not in get_paths:
            fail(404, 'NOT_FOUND', '接口不存在')
        if query or self.command != ('GET' if path in get_paths else 'POST'):
            fail(405, 'METHOD_NOT_ALLOWED', '请使用此接口支持的请求方式')
        state, sid = self.server.accounts, self.token()
        if path == '/account/health':
            return {'status': 'ok', 'authMethod': 'phone_otp' if otp else 'password',
                    'readOnly': False, 'schemaVersion': 2, 'contractVersion': '1.1'}
        if path == '/account/session':
            view, token, expires = state.session_view(sid)
            headers['Set-Cookie'] = self.cookie(token, expires)
            return self.session_capabilities(view)
        data = self.body()
        if not isinstance(data, dict) or set(data) != post_fields[path] or any(not isinstance(v, str) for v in data.values()):
            fail(400, 'INVALID_REQUEST', '请核对登录请求字段')
        with state.tx() as db:
            state.valid(db, sid)
        state.csrf(sid, self.headers.get('X-CSRF-Token', ''))
        if path == '/account/logout':
            result = state.logout(sid)
            headers['Set-Cookie'] = self.cookie()
            return result
        if path == '/account/auth/anonymous':
            view, token, expires = state.anonymous_login(sid)
            headers['Set-Cookie'] = self.cookie(token, expires)
            return self.session_capabilities(view)
        if path == '/account/auth/otp/request':
            from hosted_otp import request_sms
            return request_sms(state, sid, data, self.server.sms_transport)
        if path == '/account/auth/otp/verify':
            from hosted_otp import verify_sms
            view, token, expires = verify_sms(state, sid, data)
        else:
            view, token, expires = state.login(sid, data['username'], data['password'])
        headers['Set-Cookie'] = self.cookie(token, expires)
        return self.session_capabilities(view)

    def check_origin(self):
        from account_primitives import fail
        origin = self.headers.get('Origin')
        local = f'127.0.0.1:{self.server.server_port}'
        if self.headers.get('Host') not in (local, *PUBLIC_HOSTS):
            fail(403, 'ORIGIN_DENIED', '访问地址无效')
        if self.headers.get('Sec-Fetch-Site') == 'cross-site' or (origin and origin != ORIGIN):
            fail(403, 'ORIGIN_DENIED', '请从活动工作台打开')

    def dispatch(self, headers):
        from account_primitives import fail
        from business_routes import dispatch_business
        parsed = urlsplit(self.path)
        path = parsed.path
        if not path.startswith(API + '/'):
            if self.command not in ('GET', 'HEAD'):
                fail(405, 'METHOD_NOT_ALLOWED', '请求方法不支持')
            if path == '/healthz':
                from hosted_agent_bridge import agent_ready
                configured = 'JIATAN_ACTIVITY_AGENT_PORT' in os.environ
                ready = self.server.accounts.admin_configured() and (not configured or agent_ready(require_token=True))
                self.response_status = 200 if ready else 503
                health = {'service': 'jiatan-activity-agent', 'contract': 'activity-intake/1.0',
                          'status': 'running' if ready else 'unavailable', 'workbench': BUILD,
                          'authMethod': 'phone_otp' if self.server.phone_otp else 'password',
                          'smsGatewayConfigured': self.server.sms_gateway is not None,
                          'adminChat': getattr(self.server, 'chat_check', {'status':'unchecked'})}
                self.binary_response = json.dumps(health).encode(), {'Content-Type': 'application/json; charset=utf-8'}
                return health
            if path == '/agent-chat':
                self.response_status = 303
                headers['Location'] = API + '/agent/console'
                return None
            target = self.server.assets.get(path)
            if target is None:
                fail(404, 'NOT_FOUND', '页面不存在')
            content_type = mimetypes.guess_type(target.name)[0] or 'application/octet-stream'
            if target.suffix == '.js':
                content_type = 'text/javascript'
            self.binary_response = target.read_bytes(), {'Content-Type': content_type}
            return
        if len(parsed.query) > 8192:
            fail(422, 'INVALID_FILTER', '查询条件过长')
        try:
            fields = parse_qs(parsed.query, keep_blank_values=True, max_num_fields=100)
        except ValueError:
            fail(422, 'INVALID_FILTER', '查询条件过多')
        if any(len(v) != 1 for v in fields.values()):
            fail(422, 'INVALID_FILTER', '查询键重复')
        query = {k: v[0] for k, v in fields.items()}
        path = path[len(API):]
        if path == '/agent/console':
            if self.command not in ('GET', 'HEAD') or query:
                fail(405, 'METHOD_NOT_ALLOWED', '请求方式不正确')
            from hosted_chat import current_actor
            with self.server.accounts.tx() as db:
                current_actor(db, self.server.accounts, self.token())
            self.binary_response = (PUBLIC / 'chat.html').read_bytes(), {'Content-Type': 'text/html; charset=utf-8'}
            return None
        if path.startswith('/agent/'):
            from hosted_chat import dispatch_chat
            return dispatch_chat(self, headers, path, query)
        if path.startswith('/account/') and not path.startswith('/account/users'):
            return self.account(path, query, headers)
        result = dispatch_business(self, headers, path, query)
        if path == '/operations/context':
            result.update(mode='hosted', dataOrigin='activity_workbench')
        return result

    def handle_request(self):
        from account_primitives import APIError, iso
        self.request_id = str(uuid.uuid4())
        self.trace_actor = None
        self.binary_response = None
        self.response_status = 200
        self.meta = {'complete': False, 'fetchedAt': iso(self.server.clock())}
        headers = {}
        try:
            self.check_origin()
            if self.path.split('?',1)[0] in ('/readyz','/v1/activity-agent/analyze'):
                from hosted_agent_bridge import serve_agent_route
                if serve_agent_route(self):
                    return
            output = {'data': self.dispatch(headers)}
        except APIError as error:
            self.response_status = error.status
            self.meta['complete'] = False
            output = {'error': {'code': error.code, 'message': error.message, 'details': error.details}}
            if error.status == 429:
                headers['Retry-After'] = str(error.details.get('retryAfterSeconds', 1))
        except Exception as error:
            self.response_status = 503 if isinstance(error, sqlite3.Error) else 500
            logging.error('request_id=%s exception=%s', self.request_id, type(error).__name__)
            output = {'error': {'code': 'SERVICE_ERROR', 'message': '服务暂时无法完成请求，请提供请求编号', 'details': {}}}
        self.meta.update(mode='hosted', dataOrigin='activity_workbench')
        if 'warnings' in self.meta:
            self.meta['warnings'] = [v for v in self.meta['warnings'] if '合成测试' not in v and '隔离本地' not in v]
        raw = json.dumps(dict(output, requestId=self.request_id, meta=self.meta), ensure_ascii=False,
                         allow_nan=False).encode()
        headers.setdefault('Content-Type', 'application/json; charset=utf-8')
        if self.binary_response is not None and self.response_status < 300:
            raw, binary_headers = self.binary_response
            headers.update(binary_headers)
        self.send_response(self.response_status)
        headers.update({'Content-Length': str(len(raw)), 'Cache-Control': 'no-store',
                        'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'no-referrer',
                        'X-Frame-Options': 'DENY', 'Connection': 'close',
                        'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"})
        for name, val in headers.items():
            self.send_header(name, val)
        self.end_headers()
        self.close_connection = True
        if self.command != 'HEAD':
            try:
                self.wfile.write(raw)
            except (BrokenPipeError, ConnectionResetError):
                pass

    do_GET = do_POST = do_PUT = do_HEAD = do_PATCH = do_DELETE = do_OPTIONS = handle_request


class Service(ThreadingHTTPServer):
    daemon_threads = True
    def get_request(self):
        sock, address = super().get_request()
        sock.settimeout(35)
        return sock, address


def create_service(host, port, data_dir, *, sms_transport=None):
    from runtime_bindings import configure
    decode_material = configure(data_dir)
    if sms_transport is None:
        from workbench_sms_gateway import load_gateway
        sms_transport = load_gateway(ROOT)
        if sms_transport is None:
            from hosted_sms_transport import load_sms_transport
            sms_transport = load_sms_transport(data_dir)
    from hosted_state import open_hosted_state
    from hosted_business import build_business
    assets = {('/' if p.name == 'index.html' else '/' + p.relative_to(PUBLIC).as_posix()): p
              for p in PUBLIC.rglob('*') if p.is_file() and not p.is_symlink() and p.suffix in ('.html','.js','.css','.svg','.png','.ico','.woff2')}
    if '/' not in assets or '/src/hosted-app.js' not in assets:
        raise RuntimeError('Workbench frontend incomplete')
    assets.pop('/chat.html', None)
    # The HTML page is available only via the authenticated /agent-chat route.
    chat = PUBLIC / 'chat.html'
    if not chat.is_file():
        raise RuntimeError('Chat page missing')

    with tempfile.TemporaryDirectory(dir=data_dir) as temp:
        verifier = Path(temp) / 'verifier.json'
        fd = os.open(verifier, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, 'wb') as sink:
            sink.write((ROOT / 'bootstrap.json').read_bytes())
        accounts = open_hosted_state(data_dir, bootstrap_path=verifier)
    if not accounts.admin_configured():
        raise RuntimeError('Administrator is not configured')
    server = Service((host, port), Handler)
    server.clock, server.accounts, server.assets = time.time, accounts, assets
    server.sms_transport = sms_transport
    server.sms_gateway = sms_transport if callable(getattr(sms_transport, 'verify_code', None)) else None
    server.phone_otp = (sms_transport is not None and sms_transport.status == 'configured'
                        and getattr(sms_transport, 'login_enabled', True))
    try:
        server.business = build_business(accounts, server.clock, decode_material)
    except BaseException:
        server.server_close()
        raise
    return server


if __name__ == '__main__':
    os.umask(0o077)
    host = os.environ['HOST']
    if host != '127.0.0.1':
        raise RuntimeError('Only the configured reverse proxy may accept public traffic')
    server = create_service(host, int(os.environ['PORT']), Path(os.environ['ACTIVITY_DATA_DIR']).resolve(strict=True))
    try:
        server.serve_forever()
    finally:
        server.server_close()
        server.business.close()
