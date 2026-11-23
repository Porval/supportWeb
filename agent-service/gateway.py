"""Loopback HTTP transport for the existing activity-intake worker."""
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import shutil
import stat
import subprocess
import sys
from threading import BoundedSemaphore

APP = Path(__file__).resolve().parent
PRIVATE = Path(os.environ.get('ACTIVITY_AGENT_CONFIG_DIR', str(Path.home() / '.activityagent')))
sys.path.insert(0, str(APP / 'context'))
import service

service.imports(APP)
from business_agent.workbench_deepseek import adapter
from memory import CaseMemory, ContextUnavailable

MODEL = PRIVATE / 'deepseek.json'
MYSQL_PROFILE = PRIVATE / 'runtime.cnf'
TOKEN = PRIVATE / 'http.token'
SLOTS = BoundedSemaphore(2)


def init_token():
    PRIVATE.mkdir(mode=0o700, exist_ok=True)
    if PRIVATE.is_symlink() or PRIVATE.stat().st_mode & 0o077 or PRIVATE.stat().st_uid != os.getuid():
        raise ValueError('Private config directory must be owned by service user and mode 0700')
    if not TOKEN.exists():
        with open(TOKEN, 'x', opener=lambda p, f: os.open(p, f, 0o600)) as out:
            out.write(secrets.token_urlsafe(36) + '\n')
    info = TOKEN.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError('Invalid HTTP token permissions')
    token = TOKEN.read_bytes().strip()
    if len(token) < 32:
        raise ValueError('Invalid HTTP token')
    return b'Bearer ' + token


def readiness():
    client = shutil.which(os.environ.get('ACTIVITY_AGENT_MYSQL', 'mysql'))
    try:
        model = adapter.load_private_config(MODEL).model
    except adapter.AdapterError:
        model = None
    case_ready = False
    if client and MYSQL_PROFILE.is_file():
        try:
            count = CaseMemory(client, MYSQL_PROFILE).execute('SELECT COUNT(*) FROM approved_case WHERE active = TRUE').strip()
            case_ready = count.isdigit() and int(count) > 0
        except ContextUnavailable:
            pass
    return {'ready':bool(model and case_ready), 'configuredModel':model, 'mysqlClient':bool(client),
            'mysqlProfile':MYSQL_PROFILE.is_file(), 'activeCasesAvailable':case_ready}


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(30)
    def log_message(self, *args):
        pass
    def send(self, status, data):
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)
    def reply(self, http_status, **values):
        self.send(http_status, service.json_bytes(values))
    def do_GET(self):
        if self.path in ('/', '/healthz'):
            return self.reply(200, service='jiatan-activity-agent', contract=service.CONTRACT, status='running')
        if self.path == '/readyz':
            state = readiness()
            return self.reply(200 if state['ready'] else 503, **state)
        self.reply(404, error='NOT_FOUND')
    def do_POST(self):
        if self.path != '/v1/activity-agent/analyze':
            return self.reply(404, error='NOT_FOUND')
        auth = self.headers.get('Authorization', '').encode('latin-1')
        if not hmac.compare_digest(auth, self.server.bearer):
            return self.reply(401, error='UNAUTHORIZED')
        if self.headers.get_content_type() != 'application/json' or self.headers.get('Transfer-Encoding'):
            return self.reply(415, error='JSON_BODY_REQUIRED')
        length = self.headers.get('Content-Length', '')
        if not length.isascii() or not length.isdigit() or len(length) > 9 or not 0 < int(length) <= service.LIMIT:
            return self.reply(413, error='REQUEST_SIZE_INVALID')
        if not SLOTS.acquire(blocking=False):
            return self.reply(429, error='AGENT_BUSY')
        try:
            raw = self.rfile.read(int(length))
            if len(raw) != int(length):
                return self.reply(400, error='INCOMPLETE_REQUEST')
            body = adapter.decode(raw)
            if (not isinstance(body, dict) or body.get('operation') != 'analyze'
                    or body.get('contractVersion') != service.CONTRACT or not isinstance(body.get('requestId'), str)
                    or not isinstance(body.get('payload'), dict)):
                return self.reply(400, error='INVALID_ENVELOPE')
            if not readiness()['ready']:
                return self.reply(503, error='AGENT_DEPENDENCIES_UNAVAILABLE', requestId=body['requestId'])
            deadline = service.run_budget(body['payload'])
            command = [sys.executable,'-B',str(APP/'context/service.py'),'worker','--release',str(APP),
                       '--enable-large-materials','--deepseek-settings',str(MODEL),'--runtime-profile',str(MYSQL_PROFILE),
                       '--mysql',shutil.which(os.environ.get('ACTIVITY_AGENT_MYSQL','mysql'))]
            proc = subprocess.run(command,input=raw,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=deadline)
            if proc.returncode or not 0 < len(proc.stdout) <= service.RESPONSE_LIMIT:
                return self.reply(502, error='INVALID_WORKER_RESPONSE')
            self.send(200,proc.stdout)
        except subprocess.TimeoutExpired:
            self.reply(504, error='AGENT_TIMEOUT')
        except (adapter.AdapterError, ValueError, KeyError, TypeError):
            self.reply(400, error='INVALID_REQUEST')
        finally:
            SLOTS.release()


if __name__ == '__main__':
    host = os.environ.get('HOST', '127.0.0.1')
    if host != '127.0.0.1':
        raise ValueError('Only the deployment loopback listener is supported')
    with ThreadingHTTPServer((host, int(os.environ.get('PORT', '18766'))), Handler) as server:
        server.bearer = init_token()
        print(f'activity-intake listening on {host}:{server.server_port}', flush=True)
        server.serve_forever()
