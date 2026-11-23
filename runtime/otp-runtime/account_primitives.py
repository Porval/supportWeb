#!/usr/bin/env python3
"""JT019 internal account primitives, adapted from project JT014/JT015 Store. No HTTP API.
Original SHA256: 7ac5d2f9ef8d749aadb5657821758de3d0d36f8c6842cdcec1f7cf76f9d145c7
New private storage only; never opens the old account var by default.
"""
import argparse
import contextlib
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from http.cookies import SimpleCookie, CookieError
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, parse_qs

BASE = '/api/account/v1'
ROLES = {'operations': '门店运营', 'merchandising': '商品', 'management': '管理', 'marketing': '营销', 'business_development': '招商', 'supply_chain': '供应链', 'pricing': '价盘', 'finance': '财务', 'other': '其他'}
SESSION_SECONDS = 14 * 24 * 60 * 60
ROOT = Path(__file__).resolve().parents[2]
SCHEMA = '''
CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY);
CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY, phone TEXT UNIQUE NOT NULL, business_role TEXT CHECK(business_role IS NULL OR business_role IN ('operations','merchandising','management','marketing','business_development','supply_chain','pricing','finance','other')), status TEXT NOT NULL DEFAULT 'active', version INTEGER NOT NULL DEFAULT 1, phone_verified_at REAL NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS sessions(token_hash TEXT PRIMARY KEY,user_id TEXT REFERENCES users(id),created_at REAL NOT NULL,last_seen_at REAL NOT NULL,absolute_expires_at REAL NOT NULL,revoked_at REAL);
CREATE TABLE IF NOT EXISTS otp_challenges(id TEXT PRIMARY KEY,pre_session_hash TEXT NOT NULL REFERENCES sessions(token_hash),phone TEXT NOT NULL,code_mac TEXT NOT NULL,status TEXT NOT NULL,expires_at REAL NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS rate_limits(scope TEXT NOT NULL,key_hash TEXT NOT NULL,window_start REAL NOT NULL,count INTEGER NOT NULL,blocked_until REAL NOT NULL DEFAULT 0,PRIMARY KEY(scope,key_hash));
CREATE TABLE IF NOT EXISTS account_audit(id TEXT PRIMARY KEY,actor_user_id TEXT NOT NULL REFERENCES users(id),target_user_id TEXT NOT NULL REFERENCES users(id),event TEXT NOT NULL,old_role TEXT,new_role TEXT,request_id TEXT NOT NULL,created_at REAL NOT NULL);
INSERT OR IGNORE INTO schema_migrations VALUES(1);
'''

class APIError(Exception):
    def __init__(self, status, code, message, details=None):
        self.status, self.code, self.message, self.details = status, code, message, details or {}

def fail(status, code, message, details=None):
    raise APIError(status, code, message, details)

def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z') if t is not None else None

def phone(value):
    if not isinstance(value, str):
        fail(400, 'INVALID_PHONE', '手机号必须是字符串')
    value = value.strip()
    if value.startswith('+86'):
        value = value[3:]
    if not re.fullmatch(r'1[3-9][0-9]{9}', value):
        fail(400, 'INVALID_PHONE', '仅支持中国大陆11位手机号或+86格式')
    return '+86' + value

def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()

class DisabledTransport:
    status = 'not_configured'
    def send_login_code(self, phone, code, challenge_id, expires_at):
        fail(503, 'SMS_NOT_CONFIGURED', '短信服务尚未配置，暂不能发送验证码和完成短信登录')

class Store:
    def __init__(self, directory, transport=None, clock=time.time):
        os.umask(0o077)
        directory_path = Path(directory)
        if directory_path.is_symlink():
            raise ValueError('private directory cannot be symlink')
        self.directory = directory_path.resolve()
        original_private = ROOT / 'backend/account-system/var'
        if self.directory == original_private or original_private in self.directory.parents:
            raise ValueError('old account private storage is read-only and must not be opened')
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.directory, 0o700)
        self.dbpath = self.directory / 'accounts.sqlite3'
        keypath = self.directory / 'secret.key'
        for p in (self.dbpath, keypath, self.directory / 'account-managers.json'):
            if p.is_symlink():
                raise ValueError('private files cannot be symlinks')
        if self.dbpath.exists() and not keypath.exists():
            raise ValueError('existing database without secret.key; restore matching backup')
        if not keypath.exists():
            with keypath.open('xb') as f:
                f.write(secrets.token_bytes(32))
        os.chmod(keypath, 0o600)
        self.key = keypath.read_bytes()
        if len(self.key) != 32:
            raise ValueError('invalid secret.key')
        managers_path = self.directory / 'account-managers.json'
        if not managers_path.exists():
            with managers_path.open('x') as f:
                json.dump({'version': 1, 'phones': []}, f)
        os.chmod(managers_path, 0o600)
        cfg = json.loads(managers_path.read_text())
        if set(cfg) != {'version', 'phones'} or type(cfg['version']) is not int or cfg['version'] != 1 or not isinstance(cfg['phones'], list):
            raise ValueError('invalid manager configuration')
        self.managers = frozenset(phone(p) for p in cfg['phones'])
        if any(p not in self.managers for p in cfg['phones']):
            raise ValueError('manager phones must be normalized')
        self.transport, self.clock = transport or DisabledTransport(), clock
        with contextlib.closing(self.connect()) as db:
            existing = db.execute("SELECT name FROM sqlite_master WHERE name='schema_migrations'").fetchone()
            if existing and any(r[0] not in (1, 2) for r in db.execute('SELECT version FROM schema_migrations')):
                raise ValueError('unsupported database schema; restore compatible code')
            db.executescript(SCHEMA)
        os.chmod(self.dbpath, 0o600)

    def connect(self):
        db = sqlite3.connect(self.dbpath, timeout=3, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('PRAGMA busy_timeout=3000')
        return db

    @contextlib.contextmanager
    def tx(self):
        db = self.connect()
        try:
            db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def mac(self, value):
        return hmac.new(self.key, value.encode(), hashlib.sha256).hexdigest()

    def new_session(self, db, user_id=None):
        token = secrets.token_urlsafe(32)
        now = self.clock()
        db.execute('INSERT INTO sessions VALUES(?,?,?,?,?,NULL)', (digest(token), user_id, now, now, now + (SESSION_SECONDS if user_id else 900)))
        return token

    def session(self, db, token):
        s = db.execute('SELECT * FROM sessions WHERE token_hash=?', (digest(token),)).fetchone() if token else None
        if not s:
            fail(401, 'SESSION_REQUIRED', '请先读取账号状态并登录')
        now = self.clock()
        if s['revoked_at'] is not None or s['absolute_expires_at'] <= now:
            fail(401, 'SESSION_EXPIRED', '会话已失效，请重新登录')
        user = db.execute('SELECT * FROM users WHERE id=? AND status=?', (s['user_id'], 'active')).fetchone() if s['user_id'] else None
        if s['user_id'] and not user:
            fail(401, 'SESSION_EXPIRED', '账号不可用')
        db.execute('UPDATE sessions SET last_seen_at=? WHERE token_hash=?', (now, digest(token)))
        return s, user

    def view_user(self, u):
        return {k: u[v] for k, v in [('id','id'), ('phone','phone'), ('businessRole','business_role'), ('version','version')]} | {'createdAt': iso(u['created_at']), 'updatedAt': iso(u['updated_at'])}

    def view(self, db, token, reason=None):
        s, u = self.session(db, token)
        return {'authenticated': u is not None, 'user': self.view_user(u) if u else None, 'permissions': ['accounts.manage'] if u and u['phone'] in self.managers else [], 'csrfToken': self.mac('csrf:' + token), 'sessionExpiresAt': iso(s['absolute_expires_at']), 'idleExpiresAt': None, 'sms': {'status': self.transport.status}, 'accountManagementConfigured': bool(self.managers), 'reason': reason}

    def check_manager(self, u):
        if not u:
            fail(401, 'SESSION_REQUIRED', '请先登录')
        if u['phone'] not in self.managers:
            fail(403, 'FORBIDDEN', '无账号管理权限')

    def rate_row(self, db, scope, key):
        return db.execute('SELECT * FROM rate_limits WHERE scope=? AND key_hash=?', (scope, self.mac('rate:' + key))).fetchone()

    def rate_set(self, db, scope, key, start, count, blocked=0):
        db.execute('INSERT OR REPLACE INTO rate_limits VALUES(?,?,?,?,?)', (scope, self.mac('rate:' + key), start, count, blocked))

    def limited(self, seconds, code='RATE_LIMITED'):
        seconds = max(1, int(seconds + .999))
        fail(429, code, '请求受限，请稍后重试', {'retryAfterSeconds': seconds})

    def otp_pre_session_key(self, token):
        return digest(token)

    def otp_find_account(self, db, number, now):
        db.execute('INSERT OR IGNORE INTO users(id,phone,phone_verified_at,created_at,updated_at) VALUES(?,?,?,?,?)',
                   (str(uuid.uuid4()), number, now, now, now))
        return db.execute('SELECT * FROM users WHERE phone=?', (number,)).fetchone()

    def otp_revoke_session(self, db, token, now):
        db.execute('UPDATE sessions SET revoked_at=? WHERE token_hash=?', (now, digest(token)))

    def new_login_code(self):
        return f'{secrets.randbelow(1000000):06d}'

    def request_otp(self, token, body, ip):
        p = phone(body['phone'])
        if self.transport.status == 'not_configured':
            fail(503, 'SMS_NOT_CONFIGURED', '短信服务尚未配置，暂不能发送验证码和完成短信登录')
        now, cid, code = self.clock(), str(uuid.uuid4()), self.new_login_code()
        with self.tx() as db:
            s, u = self.session(db, token)
            if u:
                fail(409, 'ALREADY_AUTHENTICATED', '已登录，请先退出')
            lock = self.rate_row(db, 'failures', p)
            if lock and lock['blocked_until'] > now:
                self.limited(lock['blocked_until'] - now, 'OTP_ATTEMPTS_EXCEEDED')
            cool = self.rate_row(db, 'cooldown', p)
            if cool and cool['blocked_until'] > now:
                self.limited(cool['blocked_until'] - now)
            quotas = []
            for scope, key, maximum in [('phone_hour', p, 5), ('ip_hour', ip, 20)]:
                row = self.rate_row(db, scope, key)
                start, count = (row['window_start'], row['count']) if row and row['window_start'] + 3600 > now else (now, 0)
                if count >= maximum:
                    self.limited(start + 3600 - now)
                quotas.append((scope, key, start, count + 1))
            for scope, key, start, count in quotas:
                self.rate_set(db, scope, key, start, count)
            self.rate_set(db, 'cooldown', p, now, 1, now + 60)
            db.execute('INSERT INTO otp_challenges VALUES(?,?,?,?,?,?,0,?)', (cid, self.otp_pre_session_key(token), p, self.mac('otp:login:' + cid + ':' + p + ':' + code), 'pending', now + 300, now))
        try:
            result = self.transport.send_login_code(p, code, cid, iso(now + 300))
            if not isinstance(result, dict) or result.get('accepted') is not True:
                raise RuntimeError('provider rejected')
        except Exception:
            with self.tx() as db:
                db.execute("UPDATE otp_challenges SET status='failed' WHERE id=?", (cid,))
            fail(503, 'SMS_DELIVERY_FAILED', '短信服务未确认受理，请稍后重新获取')
        with self.tx() as db:
            try:
                self.session(db, token)
            except APIError:
                db.execute("UPDATE otp_challenges SET status='failed' WHERE id=?", (cid,))
                return None, APIError(401, 'SESSION_EXPIRED', '预会话已失效，请刷新后重新获取')
            db.execute("UPDATE otp_challenges SET status='superseded' WHERE phone=? AND status='sent'", (p,))
            db.execute("UPDATE otp_challenges SET status='sent' WHERE id=?", (cid,))
        return {'challengeId': cid, 'delivery': 'dev_test' if self.transport.status == 'dev_test' else 'provider_accepted', 'expiresAt': iso(now + 300), 'resendAt': iso(now + 60)}, None

    def verify(self, db, token, body):
        if self.transport.status == 'not_configured':
            fail(503, 'SMS_NOT_CONFIGURED', '短信服务尚未配置，暂不能完成短信登录')
        if not isinstance(body['code'], str) or not re.fullmatch('[0-9]{6}', body['code']) or not isinstance(body['challengeId'], str):
            fail(400, 'INVALID_REQUEST', '验证码须为六位数字字符串')
        now = self.clock()
        c = db.execute('SELECT * FROM otp_challenges WHERE id=? AND pre_session_hash=?', (body['challengeId'], self.otp_pre_session_key(token))).fetchone()
        if not c or c['status'] != 'sent' or c['expires_at'] <= now:
            fail(400, 'OTP_INVALID_OR_EXPIRED', '验证码无效或已过期，请重新获取')
        lock = self.rate_row(db, 'failures', c['phone'])
        if lock and lock['blocked_until'] > now:
            self.limited(lock['blocked_until'] - now, 'OTP_ATTEMPTS_EXCEEDED')
        if not hmac.compare_digest(c['code_mac'], self.mac('otp:login:' + c['id'] + ':' + c['phone'] + ':' + body['code'])):
            count = (lock['count'] if lock and (not lock['blocked_until'] or lock['blocked_until'] > now) else 0) + 1
            self.rate_set(db, 'failures', c['phone'], now, count, now + 900 if count >= 5 else 0)
            db.execute('UPDATE otp_challenges SET attempts=attempts+1,status=? WHERE id=?', ('locked' if c['attempts'] + 1 >= 5 or count >= 5 else 'sent', c['id']))
            return None, APIError(429 if count >= 5 else 400, 'OTP_ATTEMPTS_EXCEEDED' if count >= 5 else 'OTP_INVALID_OR_EXPIRED', '验证码错误，请稍后重新获取', {'retryAfterSeconds': 900} if count >= 5 else {})
        db.execute("UPDATE otp_challenges SET status='consumed' WHERE id=?", (c['id'],))
        self.rate_set(db, 'failures', c['phone'], now, 0)
        u = self.otp_find_account(db, c['phone'], now)
        if u is None or u['status'] != 'active':
            fail(403, 'FORBIDDEN', '账号不可用')
        self.otp_revoke_session(db, token, now)
        newtoken = self.new_session(db, u['id'])
        return (self.view(db, newtoken), newtoken), None

