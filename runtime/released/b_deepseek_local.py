"""Private local settings and the last verified outcome for the current key."""

from datetime import datetime, timezone
import getpass
import json
import os
from pathlib import Path
import stat
import sys
from urllib import error, request
import warnings

from b_model_worker import SETTINGS_ENV
from b_state import private_atomic
from business_agent.workbench_deepseek.adapter import (
    AdapterError, DeepSeekConfig, NoRedirect, checked_config, load_private_config,
)

MODELS_ENDPOINT = 'https://api.deepseek.com/models'


def settings_path(data_dir):
    override = os.environ.get(SETTINGS_ENV)
    return Path(override) if override else Path(data_dir) / 'deepseek' / 'settings.json'


def generation(path):
    """Identify an atomic settings revision without reading or hashing the key."""
    try:
        info = path.lstat()
        if stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o600:
            return [info.st_dev, info.st_ino, info.st_ctime_ns, info.st_mtime_ns, info.st_size]
    except OSError:
        pass
    return None


def status_file(path):
    return path.with_name(path.name + '.auth.json')


def authentication(path):
    current = generation(path)
    if current is not None:
        saved = status_file(path)
        try:
            if generation(saved) is not None:
                with os.fdopen(os.open(saved, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), 'rb') as stream:
                    state = json.loads(stream.read(2048))
                if (state.get('generation') == current == generation(path)
                        and state.get('status') in ('authorized', 'rejected', 'unreachable')):
                    return state['status']
        except (OSError, ValueError, AttributeError):
            pass
    return 'unchecked'


def record_authentication(path, expected, status):
    if expected is not None and expected == generation(path):
        # A concurrent rotation with different file metadata invalidates this result.
        parent = path.parent.lstat()
        if stat.S_ISDIR(parent.st_mode) and parent.st_uid == os.getuid() and stat.S_IMODE(parent.st_mode) == 0o700:
            private_atomic(status_file(path), {'status': status, 'generation': expected,
                                               'checkedAt': datetime.now(timezone.utc).isoformat()})


def probe(config):
    """Check the key with a read-only request that carries no activity materials."""
    opener = request.build_opener(request.ProxyHandler({}), NoRedirect())
    query = request.Request(MODELS_ENDPOINT, headers={'Authorization': 'Bearer ' + config.api_key})
    try:
        with opener.open(query, timeout=6) as response:
            return ('authorized' if response.status == 200 else 'unreachable'), response.status
    except error.HTTPError as exc:
        with exc:
            return ('rejected' if exc.code in (401, 403) else 'unreachable'), exc.code
    except (OSError, ValueError):
        return 'unreachable', None


def install(path, config):
    config = checked_config(config)
    path.parent.mkdir(mode=0o700, exist_ok=True)
    parent = path.parent.lstat()
    if not stat.S_ISDIR(parent.st_mode) or parent.st_uid != os.getuid() or stat.S_IMODE(parent.st_mode) != 0o700:
        raise PermissionError('DeepSeek settings directory must be private')
    private_atomic(path, {'key': config.api_key, 'model': config.model})


def manage(path, *, replace):
    try:
        if replace:
            if not sys.stdin.isatty():
                print('请在本机终端交互输入新密钥。')
                return 2
            with warnings.catch_warnings():
                warnings.simplefilter('error', getpass.GetPassWarning)
                key = getpass.getpass('DeepSeek 新密钥（输入不回显）: ').strip()
            try:
                # Preserve the existing choice of model when rotating the key.
                new_config = DeepSeekConfig(key, load_private_config(path).model)
            except AdapterError:
                new_config = DeepSeekConfig(key)
            new_config = checked_config(new_config)
            state, http_status = probe(new_config)
            if state == 'authorized':
                install(path, new_config)
                record_authentication(path, generation(path), state)
        else:
            current = generation(path)
            state, http_status = probe(load_private_config(path))
            record_authentication(path, current, state)
    except (AdapterError, getpass.GetPassWarning, OSError, EOFError):
        print('无法读取或保存本机私有配置，或密钥格式无效。')
        return 2
    print(json.dumps({'authenticationStatus': state, 'httpStatus': http_status}))
    return 0 if state == 'authorized' else 1
