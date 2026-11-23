"""One-time model connectivity check for this release; keeps user conversations clean."""
from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile
import time


def verify_admin_chat(accounts, build):
    from hosted_chat import call_agent
    from agent_admin_chat import __file__ as cli
    stamp = sha256(Path(cli).read_bytes() + build.encode()).hexdigest()
    mark = accounts.paths.directory / ('release-chat-' + stamp + '.json')
    if mark.exists():
        saved = json.loads(mark.read_bytes())
        if saved.get('stamp') == stamp:
            return {'status': 'verified' if saved.get('verified') is True else 'unavailable',
                    'model': saved.get('model'), 'code': saved.get('code')}
    reply = call_agent([{'role':'user','content':'连接验证。请只回复 READY。'}])
    receipt = reply.get('receipt', {})
    success = bool(reply.get('reply') and reply.get('model') and receipt.get('source') == 'deepseek')
    document = {'stamp': stamp, 'verified': success, 'model': reply.get('model'),
                'source': receipt.get('source'), 'requestId': receipt.get('requestId'),
                'verifiedAt': int(time.time()), 'code': None if success else reply.get('error','CHAT_NO_MODEL_RECEIPT')}
    with tempfile.NamedTemporaryFile(mode='w',dir=mark.parent,delete=False) as f:
        json.dump(document, f)
        f.flush()
        os.fsync(f.fileno())
    os.replace(f.name, mark)
    return {'status': 'verified' if success else 'unavailable', 'model': document['model'], 'code': document['code']}
