"""Keep the released recognition gateway, MySQL state, and workbench behind one listener."""
import http.client
import json
import os
from pathlib import Path
import re
import selectors
import signal
import subprocess
import sys
import threading
import time

from server import BUILD, ROOT, create_service
from release_check import verify_admin_chat


def start_agent(data_dir):
    # start.sh from the verified gateway release reuses its existing MySQL and model configuration.
    child = subprocess.Popen(['/bin/bash', 'start.sh'], cwd=ROOT / 'agent-service',
        env=dict(os.environ, HOST='127.0.0.1', PORT='0', PYTHONDONTWRITEBYTECODE='1'),
        stdout=subprocess.PIPE, stderr=None)
    try:
        end = time.monotonic() + 44
        buffer = b''
        with selectors.DefaultSelector() as poll:
            poll.register(child.stdout, selectors.EVENT_READ)
            while time.monotonic() < end:
                if not poll.select(max(0, end-time.monotonic())):
                    break
                data = os.read(child.stdout.fileno(), 4096)
                if not data:
                    break
                buffer += data
                while b'\n' in buffer:
                    line, buffer = buffer.split(b'\n',1)
                    match = re.fullmatch(rb'activity-intake listening on 127\.0\.0\.1:(\d{1,5})', line)
                    if match:
                        port = int(match.group(1))
                        conn = http.client.HTTPConnection('127.0.0.1', port, timeout=4)
                        try:
                            conn.request('GET','/readyz')
                            res = conn.getresponse()
                            readiness = json.loads(res.read(4097))
                            if res.status != 200 or readiness.get('ready') is not True:
                                raise RuntimeError('Agent database or provider not ready')
                        finally:
                            conn.close()
                        os.environ['JIATAN_ACTIVITY_AGENT_PORT'] = str(port)
                        return child
                if len(buffer) > 8192:
                    raise ValueError('Invalid startup record')
        raise RuntimeError('Recognition service did not start within activation deadline')
    except BaseException:
        stop_agent(child)
        raise


def stop_agent(child):
    if child.poll() is None:
        child.terminate()
        try:
            child.wait(timeout=4)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()
    child.stdout.close()


def main():
    os.umask(0o077)
    if os.environ['HOST'] != '127.0.0.1':
        raise RuntimeError('Server requires the approved HTTPS gateway')
    data_dir = Path(os.environ['ACTIVITY_DATA_DIR']).resolve(strict=True)
    trust = Path('/etc/pki/tls/certs/ca-bundle.crt')
    if not os.environ.get('SSL_CERT_FILE') and trust.is_file():
        os.environ['SSL_CERT_FILE'] = str(trust)
    import zoneinfo
    tzpath = str(ROOT / 'zoneinfo')
    os.environ['PYTHONTZPATH'] = tzpath
    zoneinfo.reset_tzpath((tzpath,))
    child = start_agent(data_dir)
    def terminate(signum, frame):
        raise SystemExit(128+signum)
    signal.signal(signal.SIGTERM, terminate)
    try:
        server = create_service('127.0.0.1', int(os.environ['PORT']), data_dir)
        def check_chat():
            try:
                server.chat_check = verify_admin_chat(server.accounts, BUILD)
            except Exception as error:
                server.chat_check = {'status': 'unavailable'}
                print('Chat check: ' + type(error).__name__, file=sys.stderr, flush=True)
        server.chat_check = {'status': 'checking'}
        threading.Thread(target=check_chat, daemon=True).start()
        # A dead private worker must not leave a stale endpoint on a reusable port.
        def watch():
            child.wait()
            server.shutdown()
        threading.Thread(target=watch, daemon=True).start()
        try:
            server.serve_forever()
        finally:
            server.server_close()
            server.business.close()
    finally:
        stop_agent(child)


if __name__ == '__main__':
    main()
