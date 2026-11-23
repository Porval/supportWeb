"""Server-only client for the existing PriceTag SMS challenge and verification API."""
import http.client
import json
import os
from pathlib import Path
import re
import socket
import ssl
import stat
from urllib.parse import urlsplit


HOST = 'apl1.pricetan.cn'
ROUTE_FILE = 'workbench_sms_route.json'
TEMPLATE = '【上海蜜田科技】注册绑定手机号验证码是${code}'
MAX_RECEIPT = 16 * 1024
_PHONE = re.compile(r'1[3-9][0-9]{9}')
_CODE = re.compile(r'[0-9]{6}')
_SECRET_FIELDS = {'code', 'otp', 'otpcode', 'smscode', 'verifycode', 'verificationcode', 'vcode', 'logincode'}
_IDENTITY_FIELDS = {'user', 'userid', 'username', 'account', 'accountid', 'role', 'roles', 'permissions',
                    'credential', 'authorization', 'jwt', 'bearer', 'cookie', 'setcookie', 'openid', 'unionid',
                    'sid', 'session', 'sessionid', 'sessionkey', 'password', 'profile'}


class SmsRouteError(ValueError):
    def __init__(self):
        super().__init__('工作台短信网关配置无效')


class GatewayUnavailable(Exception):
    def __init__(self, code='SMS_GATEWAY_UNAVAILABLE'):
        self.code = code
        super().__init__(code)


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Invalid JSON')
        result[key] = value
    return result


def _nonfinite(_):
    raise ValueError('Invalid JSON')


def _json(data):
    return json.loads(data.decode('utf-8'), object_pairs_hook=_unique, parse_constant=_nonfinite)


def _route(value):
    if (not isinstance(value, str) or not value.isascii() or any(ord(c) <= 32 or ord(c) == 127 for c in value)
            or any(c in value for c in ('%', '?', '#', chr(92)))):
        raise SmsRouteError()
    try:
        parsed = urlsplit(value)
    except ValueError:
        raise SmsRouteError() from None
    if parsed.scheme != 'https' or parsed.netloc not in (HOST, HOST + ':443'):
        raise SmsRouteError()
    path = parsed.path.removesuffix('/')
    if len(path) > 1024 or (path and (
            path[0] != '/' or any(segment in ('.', '..') or not re.fullmatch(r'[A-Za-z0-9._~-]+', segment)
                                  for segment in path[1:].split('/')))):
        raise SmsRouteError()
    return path


def load_gateway(root):
    try:
        fd = os.open(Path(root) / ROUTE_FILE, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    except OSError:
        raise SmsRouteError() from None
    try:
        with os.fdopen(fd, 'rb') as source:
            info = os.fstat(source.fileno())
            if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= 2048:
                raise SmsRouteError()
            data = source.read(2049)
            if len(data) != info.st_size:
                raise SmsRouteError()
        config = _json(data)
        if not isinstance(config, dict):
            raise SmsRouteError()
        if set(config) == {'baseUrl'}:
            return SMSGateway(config['baseUrl'])
        if (set(config) != {'baseUrl', 'mode', 'template', 'phoneField', 'codeField', 'loginEnabled'}
                or config['mode'] != 'remote' or config['phoneField'] != 'number'
                or config['codeField'] != 'code' or config['template'] != TEMPLATE
                or type(config['loginEnabled']) is not bool):
            raise SmsRouteError()
        return SMSGateway(config['baseUrl'], template=config['template'], login_enabled=config['loginEnabled'])
    except (OSError, ValueError, TypeError, RecursionError):
        raise SmsRouteError() from None


def _foreign_or_sensitive(value):
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = key.lower().replace('_', '').replace('-', '')
            if normalized in _IDENTITY_FIELDS or 'token' in normalized:
                return True
            if normalized in _SECRET_FIELDS and isinstance(item, (str, int)) and _CODE.fullmatch(str(item)):
                return True
            if _foreign_or_sensitive(item):
                return True
    if isinstance(value, list):
        return any(_foreign_or_sensitive(item) for item in value)
    return False


def _confirmed(reply):
    if not isinstance(reply, dict) or _foreign_or_sensitive(reply) or type(reply.get('success')) is not bool:
        raise GatewayUnavailable('SMS_GATEWAY_PROTOCOL_ERROR')
    if reply['success'] is False:
        return False
    if (reply.get('error') is not None and reply['error'] is not False) or (
            'verified' in reply and type(reply['verified']) is not bool):
        raise GatewayUnavailable('SMS_GATEWAY_PROTOCOL_ERROR')
    return reply.get('verified') is not False


def _unconfirmed(reason):
    return {'accepted': False, 'delivery': 'unconfirmed', 'reason': reason}


def _delivery_result(reply, workbench_code):
    if not isinstance(reply, dict) or type(reply.get('success')) is not bool:
        raise GatewayUnavailable('SMS_GATEWAY_PROTOCOL_ERROR')
    if not reply['success']:
        return {'accepted': False}
    allowed = {'success', 'smsSent', 'msg', 'verificationCode', 'expiresInMs', 'cooldownMs', 'error'}
    if not set(reply) <= allowed or (reply.get('error') is not None and reply['error'] is not False):
        raise GatewayUnavailable('SMS_GATEWAY_PROTOCOL_ERROR')
    if 'msg' in reply and (not isinstance(reply['msg'], str) or workbench_code in reply['msg']):
        raise GatewayUnavailable('SMS_GATEWAY_PROTOCOL_ERROR')
    for field in ('expiresInMs', 'cooldownMs'):
        if field in reply and (type(reply[field]) is not int or reply[field] < 0):
            raise GatewayUnavailable('SMS_GATEWAY_PROTOCOL_ERROR')
    if 'verificationCode' in reply:
        gateway_code = reply['verificationCode']
        if type(gateway_code) is int and 0 <= gateway_code <= 999999:
            gateway_code = f'{gateway_code:06d}'
        if (not isinstance(gateway_code, str) or not _CODE.fullmatch(gateway_code)
                or gateway_code == workbench_code):
            raise GatewayUnavailable('SMS_GATEWAY_PROTOCOL_ERROR')
    if 'smsSent' not in reply:
        # A lone success flag cannot identify this gateway's delivery receipt.
        if not {'verificationCode', 'expiresInMs', 'cooldownMs'} <= set(reply):
            raise GatewayUnavailable('SMS_GATEWAY_PROTOCOL_ERROR')
        return _unconfirmed('gateway_delivery_unconfirmed')
    if type(reply['smsSent']) is not bool:
        raise GatewayUnavailable('SMS_GATEWAY_PROTOCOL_ERROR')
    if not reply['smsSent']:
        return _unconfirmed('gateway_delivery_unconfirmed')
    return {'accepted': True}


def _proxy_delivery_result(status, raw, workbench_code):
    # A proxy error cannot confirm what the SMS service already processed.
    text = raw.decode('utf-8').strip().lower()
    phrase = http.client.responses[status].lower()
    status_line = f'{status} {phrase}'
    if text.startswith('<'):
        text = re.sub(r'</?(?:html|head|body|title|h1|center)>|<hr\s*/?>', ' ', text)
        text = ' '.join(text.split())
    if text not in ('', phrase, status_line, f'{status_line} {status_line} nginx'):
        result = _delivery_result(_json(raw), workbench_code)
        if result == {'accepted': False}:
            return result
    return _unconfirmed('gateway_temporarily_unavailable')


class SMSGateway:
    status = 'configured'

    def __init__(self, base_url, *, template=TEMPLATE, login_enabled=False):
        self._base_path = _route(base_url)
        self._template = template
        self.login_enabled = login_enabled

    def request_code(self, phone):
        if not isinstance(phone, str) or not _PHONE.fullmatch(phone):
            raise ValueError('手机号无效')
        return self._post('requestCode', {'number': phone, 'template': self._template})

    def send_login_code(self, phone, code, challenge_id, expires_at):
        if not isinstance(phone, str) or not phone.startswith('+86') or not _PHONE.fullmatch(phone[3:]):
            raise ValueError('手机号无效')
        if not isinstance(code, str) or not _CODE.fullmatch(code):
            raise ValueError('验证码格式无效')
        if self._template != TEMPLATE:
            raise SmsRouteError()
        message = self._template.replace('${code}', code)
        return self._post('requestCode', {'number': phone[3:], 'template': message}, workbench_code=code)

    def verify_code(self, phone, code):
        if not isinstance(phone, str) or not _PHONE.fullmatch(phone) or not isinstance(code, str) or not _CODE.fullmatch(code):
            raise ValueError('手机号或验证码格式无效')
        return self._post('verifyCode', {'number': phone, 'code': code})

    def _post(self, endpoint, data, *, workbench_code=None):
        connection = None
        body_written = False
        awaiting_delivery = False
        try:
            connection = http.client.HTTPSConnection(HOST, timeout=8, context=ssl.create_default_context())
            body = json.dumps(data, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
            connection.request('POST', self._base_path + '/api/sms/' + endpoint, body,
                               {'Content-Type': 'application/json; charset=utf-8', 'Accept': 'application/json'})
            # HTTPSConnection.request() returns only after writing the entire body.
            body_written = True
            awaiting_delivery = workbench_code is not None
            with connection.getresponse() as response:
                awaiting_delivery = False
                if response.status == 429:
                    raise GatewayUnavailable('SMS_GATEWAY_RATE_LIMITED')
                verify_rejection = endpoint == 'verifyCode' and response.status in (400, 401, 403, 422)
                proxy_error = workbench_code is not None and response.status in (502, 503, 504)
                if workbench_code is not None and 400 <= response.status < 500:
                    raise GatewayUnavailable('SMS_GATEWAY_REJECTED')
                if not (200 <= response.status < 300 or verify_rejection or proxy_error):
                    raise GatewayUnavailable()
                if response.getheader('Set-Cookie'):
                    raise GatewayUnavailable('SMS_GATEWAY_PROTOCOL_ERROR')
                awaiting_delivery = workbench_code is not None
                if workbench_code is None:
                    raw = response.read(MAX_RECEIPT + 1)
                else:
                    # Keep a partially received body distinguishable from no reply at all.
                    expected = response.length
                    if type(expected) is int and expected > MAX_RECEIPT:
                        raise GatewayUnavailable('SMS_GATEWAY_PROTOCOL_ERROR')
                    raw = bytearray()
                    while len(raw) <= MAX_RECEIPT:
                        part = response.read1(MAX_RECEIPT + 1 - len(raw))
                        if not part:
                            break
                        awaiting_delivery = False
                        raw.extend(part)
                    if type(expected) is int and len(raw) != expected:
                        if not raw:
                            return _unconfirmed('gateway_connection_lost')
                        raise GatewayUnavailable('SMS_GATEWAY_PROTOCOL_ERROR')
                awaiting_delivery = False
                if len(raw) > MAX_RECEIPT:
                    raise GatewayUnavailable('SMS_GATEWAY_PROTOCOL_ERROR')
            try:
                if proxy_error:
                    return _proxy_delivery_result(response.status, raw, workbench_code)
                reply = _json(raw)
                if verify_rejection:
                    if isinstance(reply, dict) and not _foreign_or_sensitive(reply) and (
                            reply.get('success') is False or reply.get('verified') is False):
                        return False
                    raise GatewayUnavailable()
                if workbench_code is not None:
                    return _delivery_result(reply, workbench_code)
                return _confirmed(reply)
            except (ValueError, RecursionError):
                raise GatewayUnavailable('SMS_GATEWAY_PROTOCOL_ERROR') from None
        except TimeoutError:
            if awaiting_delivery:
                return _unconfirmed('gateway_response_timeout')
            if workbench_code is not None and not body_written:
                raise GatewayUnavailable('SMS_GATEWAY_CONNECT_TIMEOUT') from None
            raise GatewayUnavailable() from None
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError, http.client.IncompleteRead) as exc:
            if awaiting_delivery and not getattr(exc, 'partial', b''):
                return _unconfirmed('gateway_connection_lost')
            raise GatewayUnavailable() from None
        except ssl.SSLError:
            raise GatewayUnavailable('SMS_GATEWAY_TLS_ERROR' if workbench_code is not None else
                                     'SMS_GATEWAY_UNAVAILABLE') from None
        except socket.gaierror:
            raise GatewayUnavailable('SMS_GATEWAY_DNS_ERROR' if workbench_code is not None else
                                     'SMS_GATEWAY_UNAVAILABLE') from None
        except (OSError, http.client.HTTPException):
            raise GatewayUnavailable() from None
        finally:
            if connection is not None:
                connection.close()
