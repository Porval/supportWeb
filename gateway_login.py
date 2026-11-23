"""Bind the existing SMS gateway verification to one hosted session and administrator."""
import math
import re
import uuid

from account_primitives import APIError, fail, iso, phone
from workbench_sms_gateway import GatewayUnavailable
from hosted_otp import (ADMIN_NUMBER, TTL, RESEND, LOCK, _admin, _anonymous, _enforce_lock,
                        _rate_limited, _reserve_quota, initialize_sms)

PENDING = 'gateway_sending'
READY = 'gateway_sent'
CHECKING = 'gateway_verifying'
INVALID = 'gateway_invalid'
USED = 'gateway_used'


def _gateway(transport):
    if transport is None or getattr(transport, 'status', None) != 'configured':
        fail(503, 'SMS_NOT_CONFIGURED', '短信服务尚未配置')
    return transport


def _provider_result(method, *args):
    try:
        result = method(*args)
        if type(result) is bool:
            return result
    except GatewayUnavailable as exc:
        if exc.code in ('SMS_GATEWAY_PROTOCOL_ERROR', 'SMS_GATEWAY_RATE_LIMITED'):
            return exc.code
    except Exception:
        pass
    return 'SMS_GATEWAY_UNAVAILABLE'


def _upstream_error(code, verifying=False):
    retry = '请重新获取验证码' if verifying else '请稍后重试'
    if code == 'SMS_GATEWAY_RATE_LIMITED':
        fail(429, code, '短信网关请求过于频繁，请稍后重试')
    if code == 'SMS_GATEWAY_PROTOCOL_ERROR':
        fail(502, code, '短信网关响应格式异常，' + retry)
    fail(502, 'SMS_GATEWAY_UNAVAILABLE', '短信网关暂时不可用，' + retry)


def _invalid():
    fail(400, 'OTP_INVALID_OR_EXPIRED', '验证码请求无效或已过期，请重新获取')


def _current(accounts, db, sid, row):
    _anonymous(accounts, db, sid)
    actor = _admin(accounts, db, ADMIN_NUMBER)
    if (not row or row['pre_session_mac'] != accounts.mac('sid', sid)
            or row['expires_at'] <= accounts.clock() or row['account_id'] != actor['id']
            or row['credential_version'] != actor['credential_version']):
        _invalid()
    return actor


def _row(db, cid):
    return db.execute('SELECT * FROM hosted_sms_login WHERE challenge_id=?', (cid,)).fetchone()


def _set_state(db, cid, state):
    db.execute('UPDATE hosted_sms_login SET state=? WHERE challenge_id=?', (state, cid))


def _failed_attempt(accounts, db, now):
    row = accounts.rate(db, 'hosted-sms/failures')
    start, count = ((row['window_start'], row['count']) if row and row['window_start'] + LOCK > now
                    else (now, 0))
    count += 1
    blocked_until = now + LOCK if count >= 5 else 0
    accounts.rate_set(db, 'hosted-sms/failures', start, count, blocked_until)
    return blocked_until


def request_gateway(accounts, sid, body, transport, *, source=None):
    if not isinstance(body, dict) or set(body) != {'phone'}:
        fail(400, 'INVALID_REQUEST', '请填写手机号')
    number = phone(body['phone'])
    gateway = _gateway(transport)
    cid = str(uuid.uuid4())
    with accounts.tx() as db:
        initialize_sms(db)
        _anonymous(accounts, db, sid)
        actor = _admin(accounts, db, number)
        now = accounts.clock()
        _enforce_lock(accounts, db, now)
        previous = db.execute('SELECT * FROM hosted_sms_login WHERE account_id=?', (actor['id'],)).fetchone()
        if previous:
            if previous['created_at'] + RESEND > now:
                _rate_limited(previous['created_at'] + RESEND, now)
            if previous['state'] in (PENDING, CHECKING) and previous['expires_at'] > now:
                fail(409, 'OTP_IN_PROGRESS', '上次短信请求正在处理，请稍后重试')
        _reserve_quota(accounts, db, 'hosted-sms/number', 5, now)
        if source:
            db.execute("DELETE FROM rate_limits WHERE scope LIKE 'hosted-sms/ip/%' AND window_start<=?", (now - 3600,))
            _reserve_quota(accounts, db, 'hosted-sms/ip/' + accounts.mac('sms-ip', source), 20, now)
        expires = now + TTL
        # code_mac is required by the existing table. This tag cannot validate any code.
        db.execute('''INSERT OR REPLACE INTO hosted_sms_login
            (account_id,credential_version,challenge_id,pre_session_mac,code_mac,state,created_at,expires_at)
            VALUES(?,?,?,?,?,?,?,?)''', (actor['id'], actor['credential_version'], cid, accounts.mac('sid', sid),
                                        'upstream-only', PENDING, now, expires))
    accepted = _provider_result(gateway.request_code, number[3:])
    error = None
    with accounts.tx() as db:
        row = _row(db, cid)
        if not row or row['state'] != PENDING:
            fail(409, 'OTP_REQUEST_SUPERSEDED', '本次短信请求已失效，请使用最新请求')
        if accepted is True:
            try:
                _current(accounts, db, sid, row)
            except APIError as exc:
                error = exc
        _set_state(db, cid, READY if accepted is True and not error else INVALID)
    if error:
        raise error
    if isinstance(accepted, str):
        _upstream_error(accepted)
    if not accepted:
        fail(503, 'SMS_DELIVERY_FAILED', '短信网关未受理发码，请稍后重新获取')
    return {'challengeId': cid, 'resendAfter': max(0, math.ceil(now + RESEND - accounts.clock())),
            'resendAt': iso(now + RESEND), 'expiresAt': iso(expires), 'delivery': 'provider_accepted'}


def verify_gateway(accounts, sid, body, transport):
    if (not isinstance(body, dict) or set(body) != {'challengeId', 'code'}
            or not isinstance(body['challengeId'], str) or not 1 <= len(body['challengeId']) <= 80
            or not isinstance(body['code'], str) or not re.fullmatch('[0-9]{6}', body['code'])):
        fail(400, 'INVALID_REQUEST', '请填写本次短信的六位验证码')
    gateway = _gateway(transport)
    cid = body['challengeId']
    with accounts.tx() as db:
        initialize_sms(db)
        row = _row(db, cid)
        _current(accounts, db, sid, row)
        _enforce_lock(accounts, db, accounts.clock())
        if row['state'] == CHECKING:
            fail(409, 'OTP_IN_PROGRESS', '本次验证码正在核验')
        if row['state'] != READY:
            _invalid()
        _set_state(db, cid, CHECKING)

    accepted = _provider_result(gateway.verify_code, ADMIN_NUMBER[3:], body['code'])
    error = None
    blocked_until = 0
    with accounts.tx() as db:
        row = _row(db, cid)
        if not row or row['state'] != CHECKING:
            _invalid()
        now = accounts.clock()
        try:
            actor = _current(accounts, db, sid, row)
            _enforce_lock(accounts, db, now)
        except APIError as exc:
            error = exc
        if error:
            _set_state(db, cid, INVALID)
        elif accepted is True:
            new_sid = accounts.new_session(db, actor)
            db.execute('UPDATE sessions SET revoked_at=? WHERE sid_mac=?', (now, row['pre_session_mac']))
            _set_state(db, cid, USED)
            db.execute("DELETE FROM rate_limits WHERE scope='hosted-sms/failures'")
            return accounts.view(db, new_sid) | {'authMethod': 'phone_otp'}, new_sid, accounts.lookup(db, new_sid)['expires_at']
        else:
            blocked_until = _failed_attempt(accounts, db, now)
            # A timeout may mean an upstream one-use code was already consumed.
            _set_state(db, cid, INVALID if blocked_until or isinstance(accepted, str) else READY)
    if error:
        raise error
    if isinstance(accepted, str):
        _upstream_error(accepted, verifying=True)
    if blocked_until:
        _rate_limited(blocked_until, now, 'OTP_ATTEMPTS_EXCEEDED')
    fail(400, 'OTP_INVALID_OR_EXPIRED', '验证码未通过核验，请核对手机短信后重新输入')
