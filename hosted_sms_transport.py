"""HTTPS adapter for the PriceTag sendSMS signature and a configured provider receipt."""
import hashlib
import http.client
import ipaddress
import json
import os
from pathlib import Path
import re
import ssl
import stat
import time
from urllib.parse import urlsplit


MAX_CONFIG_BYTES = 8192
MAX_RECEIPT_BYTES = 16384
CONFIG_FILE = 'workbench-sms.json'
_MISSING = object()


class SmsConfigurationError(ValueError):
    """Message is safe to include in a startup diagnostic."""


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON field')
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError('Invalid JSON number')


def _json(data):
    return json.loads(data.decode('utf-8'), object_pairs_hook=_unique_object, parse_constant=_invalid_constant)


def _read_private_config(data_dir):
    path = Path(data_dir)
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('Unsafe data path')
    directory_flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_DIRECTORY
    directory = os.open('/', directory_flags)
    try:
        for component in path.parts[1:]:
            child = os.open(component, directory_flags, dir_fd=directory)
            os.close(directory)
            directory = child
        try:
            fd = os.open(CONFIG_FILE, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        except FileNotFoundError:
            return _MISSING
        try:
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) not in (0o400, 0o600)
                    or info.st_uid != os.geteuid() or info.st_nlink != 1
                    or not 0 < info.st_size <= MAX_CONFIG_BYTES):
                raise ValueError('Unsafe configuration file')
            with os.fdopen(fd, 'rb', closefd=False) as stream:
                data = stream.read(MAX_CONFIG_BYTES + 1)
            if len(data) > MAX_CONFIG_BYTES or len(data) != info.st_size:
                raise ValueError('Invalid configuration length')
            return _json(data)
        finally:
            os.close(fd)
    finally:
        os.close(directory)


def _provider_endpoint(value):
    if (not isinstance(value, str) or not value.isascii() or not value.startswith('https://')
            or any(ord(char) <= 32 or ord(char) == 127 for char in value)
            or any(char in value for char in ('?', '#', chr(92)))):
        raise ValueError('Invalid HTTPS endpoint')
    parsed = urlsplit(value)
    if not parsed.hostname or '@' in parsed.netloc or '%' in parsed.netloc:
        raise ValueError('Invalid HTTPS endpoint')
    host = parsed.hostname
    if ":" in host:
        ipaddress.IPv6Address(host)
    elif (len(host) > 253 or any(not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label)
                                 for label in host.rstrip(".").split("."))):
        raise ValueError("Invalid hostname")
    port = parsed.port
    if port == 0:
        raise ValueError('Invalid HTTPS endpoint')
    return parsed.hostname, port, parsed.path or '/'


class SignedSmsTransport:
    status = 'configured'

    def __init__(self, config):
        fields = {'url', 'apiKey', 'secretKey', 'template', 'successPath', 'successValue'}
        if not isinstance(config, dict) or set(config) != fields:
            raise ValueError('Invalid configuration fields')
        self.host, self.port, self.path = _provider_endpoint(config['url'])
        for name in ('apiKey', 'secretKey', 'template'):
            value = config[name]
            if not isinstance(value, str) or not value.strip():
                raise ValueError('Missing configuration value')
            value.encode('utf-8')
        self.api_key, self.secret_key = config['apiKey'], config['secretKey']
        self.template = config['template']
        if (self.template.count('{code}') != 1 or '${code}' in self.template
                or len(self.template) > 1024):
            raise ValueError('Invalid code template')
        path = config['successPath']
        if (not isinstance(path, list) or not 1 <= len(path) <= 16
                or any(not isinstance(key, str) or not key for key in path)
                or type(config['successValue']) not in (str, int, bool)):
            raise ValueError('Invalid success condition')
        self.success_path, self.success_value = tuple(path), config['successValue']

    def _accepted(self, receipt):
        for key in self.success_path:
            if not isinstance(receipt, dict) or key not in receipt:
                return False
            receipt = receipt[key]
        return type(receipt) is type(self.success_value) and receipt == self.success_value

    def send_login_code(self, phone, code, challenge_id, expires_at):
        # sendSMS's client uses the mainland 11-digit number. The OTP service supplies E.164.
        if (not isinstance(phone, str) or not re.fullmatch(r'\+861[3-9][0-9]{9}', phone)
                or not isinstance(code, str) or not re.fullmatch('[0-9]{6}', code)):
            return {'accepted': False}
        payload = {'mobiles': phone[3:], 'text': self.template.replace('{code}', code),
                   'ts': int(time.time()), 'api_key': self.api_key}
        signed = '|'.join(f'{key}={payload[key]}' for key in sorted(payload)) + '_' + self.secret_key
        # The legacy provider requires this signature. Channel authentication uses verified TLS.
        payload['sign'] = hashlib.md5(signed.encode('utf-8'), usedforsecurity=False).hexdigest()
        wire = json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        # Direct HTTPSConnection has no environment-proxy or automatic redirect behavior.
        connection = None
        try:
            connection = http.client.HTTPSConnection(self.host, self.port, timeout=8, context=ssl.create_default_context())
            connection.request('POST', self.path, wire, {'Content-Type': 'application/json; charset=utf-8',
                                                          'Accept': 'application/json'})
            with connection.getresponse() as response:
                if not 200 <= response.status < 300:
                    return {'accepted': False}
                data = response.read(MAX_RECEIPT_BYTES + 1)
                if len(data) > MAX_RECEIPT_BYTES:
                    return {'accepted': False}
                return {'accepted': self._accepted(_json(data))}
        except (OSError, ValueError, RecursionError, http.client.HTTPException):
            return {'accepted': False}
        finally:
            if connection:
                connection.close()


def load_sms_transport(data_dir):
    try:
        config = _read_private_config(data_dir)
        if config is _MISSING:
            return None
        return SignedSmsTransport(config)
    except (OSError, TypeError, ValueError, RecursionError):
        raise SmsConfigurationError('短信服务私有配置无效，请核对文件、权限和供应商参数。') from None
