"""One-time SMS login for the existing authorized workbench administrator."""
import hmac
import math
import re
import secrets
import uuid

from account_primitives import APIError, fail, iso, phone

from workbench_settings import ADMIN_PHONE

ADMIN_NUMBER = "+86" + ADMIN_PHONE
TTL = 300
RESEND = 60
LOCK = 900
UNCONFIRMED_REASONS = frozenset({
    "gateway_response_timeout", "gateway_connection_lost", "gateway_temporarily_unavailable",
    "gateway_delivery_unconfirmed",
})
GATEWAY_FAILURE_MESSAGES = {
    "SMS_GATEWAY_REJECTED": "短信网关拒绝了本次发送请求，请稍后重试",
    "SMS_GATEWAY_UNAVAILABLE": "短信网关暂时不可用，请稍后重试",
    "SMS_GATEWAY_RATE_LIMITED": "短信网关限制了发送频率，请稍后重试",
    "SMS_GATEWAY_PROTOCOL_ERROR": "短信网关回执格式异常，请联系工作台负责人",
    "SMS_GATEWAY_TLS_ERROR": "工作台未能与短信网关建立安全连接，请联系工作台负责人",
    "SMS_GATEWAY_CONNECT_TIMEOUT": "工作台连接短信网关超时，请稍后重试",
    "SMS_GATEWAY_DNS_ERROR": "工作台无法解析短信网关地址，请联系工作台负责人",
}
SCHEMA = """
CREATE TABLE IF NOT EXISTS hosted_sms_login (
    account_id TEXT PRIMARY KEY REFERENCES account(id),
    credential_version INTEGER NOT NULL,
    challenge_id TEXT UNIQUE NOT NULL,
    pre_session_mac TEXT NOT NULL REFERENCES sessions(sid_mac) ON DELETE CASCADE,
    code_mac TEXT NOT NULL, state TEXT NOT NULL, created_at REAL NOT NULL, expires_at REAL NOT NULL
)
"""


def initialize_sms(db):
    db.execute(SCHEMA)


def _anonymous(accounts, db, sid):
    session, _ = accounts.valid(db, sid)
    if session["account_id"] is not None or getattr(accounts, "is_anonymous_session", lambda _db, _s: False)(db, session):
        fail(409, "ALREADY_AUTHENTICATED", "当前已登录")


def _sms_account(accounts, db, number):
    row = db.execute("SELECT * FROM account WHERE username=?", (number[3:],)).fetchone()
    if (row and row["status"] == "active" and row["expires_at"] > accounts.clock()
            and row["phone"] in (None, "", number, number[3:])
            and bool(accounts.permissions(db, row))):
        return row
    fail(403, "SMS_LOGIN_UNAVAILABLE", "该手机号暂时无法登录")


def _sms_account_by_id(accounts, db, account_id):
    row = db.execute("SELECT * FROM account WHERE id=?", (account_id,)).fetchone()
    if (row and re.fullmatch(r"1[3-9][0-9]{9}", row["username"] or "")
            and row["status"] == "active" and row["expires_at"] > accounts.clock()
            and row["phone"] in (None, "", "+86" + row["username"], row["username"])
            and bool(accounts.permissions(db, row))):
        return row
    fail(403, "SMS_LOGIN_UNAVAILABLE", "该手机号暂时无法登录")


def _code_mac(accounts, challenge, code):
    return accounts.mac("hosted-sms-login/v1", challenge + ":" + code)


def _rate_limited(until, now, code="RATE_LIMITED"):
    fail(429, code, "请求过于频繁，请稍后重试", {"retryAfterSeconds": max(1, math.ceil(until - now))})


def _enforce_lock(accounts, db, now, account_id):
    row = accounts.rate(db, "hosted-sms/failures/" + account_id)
    if row and row["blocked_until"] > now:
        _rate_limited(row["blocked_until"], now, "OTP_ATTEMPTS_EXCEEDED")


def _reserve_quota(accounts, db, scope, maximum, now):
    row = accounts.rate(db, scope)
    start, count = (row["window_start"], row["count"]) if row and row["window_start"] + 3600 > now else (now, 0)
    if count >= maximum:
        _rate_limited(start + 3600, now)
    accounts.rate_set(db, scope, start, count + 1)


def request_sms(accounts, sid, body, transport, *, source=None):
    if not isinstance(body, dict) or set(body) != {"phone"}:
        fail(400, "INVALID_REQUEST", "请填写手机号")
    number = phone(body["phone"])
    if transport is None or getattr(transport, "status", None) != "configured":
        fail(503, "SMS_NOT_CONFIGURED", "短信服务尚未配置")
    cid = str(uuid.uuid4())
    sid_mac = accounts.mac("sid", sid)
    with accounts.tx() as db:
        initialize_sms(db)
        _anonymous(accounts, db, sid)
        actor = _sms_account(accounts, db, number)
        now = accounts.clock()
        _enforce_lock(accounts, db, now, actor["id"])
        prior = db.execute("SELECT * FROM hosted_sms_login WHERE account_id=?", (actor["id"],)).fetchone()
        if prior and prior["created_at"] + RESEND > now:
            _rate_limited(prior["created_at"] + RESEND, now)
        _reserve_quota(accounts, db, "hosted-sms/number/" + actor["id"], 5, now)
        if source:
            db.execute("DELETE FROM rate_limits WHERE scope LIKE 'hosted-sms/ip/%' AND window_start<=?", (now - 3600,))
            _reserve_quota(accounts, db, "hosted-sms/ip/" + accounts.mac("sms-ip", source), 20, now)
        code = f"{secrets.randbelow(1000000):06d}"
        while prior and hmac.compare_digest(prior["code_mac"], _code_mac(accounts, prior["challenge_id"], code)):
            code = f"{secrets.randbelow(1000000):06d}"
        expires = now + TTL
        db.execute("""INSERT OR REPLACE INTO hosted_sms_login
            (account_id,credential_version,challenge_id,pre_session_mac,code_mac,state,created_at,expires_at)
            VALUES(?,?,?,?,?,'pending',?,?)""", (actor["id"], actor["credential_version"], cid,
                       sid_mac, _code_mac(accounts, cid, code), now, expires))
    delivery_state = "failed"
    reason = None
    gateway_error = None
    issued_code = None
    try:
        result = transport.send_login_code(number, code, cid, iso(expires))
        if isinstance(result, dict):
            raw_issued = result.get("issuedCode")
            if isinstance(raw_issued, str) and re.fullmatch("[0-9]{6}", raw_issued):
                issued_code = raw_issued
            core = {k: v for k, v in result.items() if k != "issuedCode"}
            if set(core) == {"accepted"} and core["accepted"] is True:
                delivery_state = "sent"
            elif (set(core) == {"accepted", "delivery", "reason"} and core["accepted"] is False
                  and core["delivery"] == "unconfirmed"
                  and isinstance(core["reason"], str) and core["reason"] in UNCONFIRMED_REASONS):
                delivery_state, reason = "delivery_unconfirmed", core["reason"]
            elif set(core) == {"accepted"} and core["accepted"] is False:
                gateway_error = "SMS_GATEWAY_REJECTED"
    except Exception as error:
        code_name = getattr(error, "code", None)
        if isinstance(code_name, str) and code_name in GATEWAY_FAILURE_MESSAGES:
            gateway_error = code_name
    # The transport can finish late. A superseded, expired or revoked request must never become valid.
    with accounts.tx() as db:
        if delivery_state != "failed":
            try:
                _anonymous(accounts, db, sid)
                current = _sms_account(accounts, db, number)
                valid = (current["id"] == actor["id"] and current["credential_version"] == actor["credential_version"]
                         and accounts.clock() < expires)
            except APIError:
                valid = False
        else:
            valid = False
        final_mac = _code_mac(accounts, cid, issued_code) if (valid and issued_code) else None
        if final_mac is not None:
            updated = db.execute("UPDATE hosted_sms_login SET state=?,code_mac=? WHERE challenge_id=? AND state='pending'",
                                 (delivery_state, final_mac, cid)).rowcount
        else:
            updated = db.execute("UPDATE hosted_sms_login SET state=? WHERE challenge_id=? AND state='pending'",
                                 (delivery_state if valid else "failed", cid)).rowcount
    if delivery_state == "failed":
        if gateway_error:
            fail(503, "SMS_DELIVERY_FAILED", GATEWAY_FAILURE_MESSAGES[gateway_error], {"gatewayStatus": gateway_error})
        fail(503, "SMS_DELIVERY_FAILED", "本次验证码请求失败，请稍后重新获取")
    if not valid or not updated:
        fail(409, "OTP_REQUEST_SUPERSEDED", "本次验证码请求已失效，请使用最新请求")
    receipt = {"challengeId": cid, "resendAfter": max(0, math.ceil(now + RESEND - accounts.clock())),
               "resendAt": iso(now + RESEND), "expiresAt": iso(expires)}
    if delivery_state == "delivery_unconfirmed":
        return receipt | {"delivery": "unconfirmed", "reason": reason}
    return receipt | {"delivery": "provider_accepted"}


def verify_sms(accounts, sid, body):
    if (not isinstance(body, dict) or set(body) != {"challengeId", "code"}
            or not isinstance(body["challengeId"], str) or not 1 <= len(body["challengeId"]) <= 80
            or not isinstance(body["code"], str) or not re.fullmatch("[0-9]{6}", body["code"])):
        fail(400, "INVALID_REQUEST", "请填写本次短信的六位验证码")
    with accounts.tx() as db:
        initialize_sms(db)
        _anonymous(accounts, db, sid)
        sid_mac = accounts.mac("sid", sid)
        cid = body["challengeId"]
        row = db.execute("SELECT * FROM hosted_sms_login WHERE challenge_id=? AND pre_session_mac=?",
                         (cid, sid_mac)).fetchone()
        if not row:
            fail(400, "OTP_INVALID_OR_EXPIRED", "验证码无效或已过期，请重新获取")
        current = _sms_account_by_id(accounts, db, row["account_id"])
        now = accounts.clock()
        _enforce_lock(accounts, db, now, current["id"])
        if (row["state"] not in ("sent", "delivery_unconfirmed")
                or row["expires_at"] <= now or row["account_id"] != current["id"]
                or row["credential_version"] != current["credential_version"]):
            fail(400, "OTP_INVALID_OR_EXPIRED", "验证码无效或已过期，请重新获取")
        failure_scope = "hosted-sms/failures/" + current["id"]
        if hmac.compare_digest(row["code_mac"], _code_mac(accounts, cid, body["code"])):
            new_sid = accounts.new_session(db, current)
            db.execute("UPDATE sessions SET revoked_at=? WHERE sid_mac=?", (now, sid_mac))
            db.execute("UPDATE hosted_sms_login SET state='used' WHERE challenge_id=?", (cid,))
            db.execute("DELETE FROM rate_limits WHERE scope=?", (failure_scope,))
            return accounts.view(db, new_sid) | {"authMethod": "phone_otp"}, new_sid, accounts.lookup(db, new_sid)["expires_at"]
        strikes = accounts.rate(db, failure_scope)
        start, attempts = ((strikes["window_start"], strikes["count"])
                           if strikes and strikes["window_start"] + LOCK > now else (now, 0))
        attempts += 1
        blocked = now + LOCK if attempts >= 5 else 0
        accounts.rate_set(db, failure_scope, start, attempts, blocked)
        if blocked:
            db.execute("UPDATE hosted_sms_login SET state='locked' WHERE challenge_id=?", (cid,))
    # Raising inside the transaction would discard the failed-attempt count.
    if blocked:
        _rate_limited(blocked, now, "OTP_ATTEMPTS_EXCEEDED")
    fail(400, "OTP_INVALID_OR_EXPIRED", "验证码不正确，请核对手机短信后重新输入")
