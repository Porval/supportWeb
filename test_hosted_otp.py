"""Isolated regression checks; no real credentials or network transport used."""
import concurrent.futures
from contextlib import contextmanager
import json
import os
from pathlib import Path
import secrets
import sys
import tempfile
import threading
import unittest

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'runtime'))
from runtime_bindings import configure

# Only the packaged module resolver is initialized here; each test has a fresh database.
configure(str(ROOT))
from account_primitives import APIError, DisabledTransport, iso
from hosted_state import open_hosted_state
from hosted_otp import request_sms, verify_sms, ADMIN_NUMBER, TTL, RESEND, LOCK


class CaptureProvider:
    status = 'configured'

    def __init__(self, response=None):
        self.calls = []
        self.response = {'accepted': True} if response is None else response

    def send_login_code(self, phone, code, cid, expiry):
        self.calls.append((phone, code, cid, expiry))
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


class HostedSmsTests(unittest.TestCase):
    def setUp(self):
        old_mask = os.umask(0o077)
        self.addCleanup(os.umask, old_mask)
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.data = Path(temp.name).resolve()
        verifier = self.data / 'synthetic-test-admin.json'
        verifier.write_text(json.dumps({'username': ADMIN_NUMBER[3:], 'displayName': '隔离测试',
                                        'saltHex': secrets.token_hex(24), 'verifierHex': secrets.token_hex(32)}))
        verifier.chmod(0o600)
        self.now = 1795000000.0
        self.accounts = open_hosted_state(self.data, verifier, clock=lambda: self.now)
        self.sid = self.accounts.session_view('')[1]
        self.provider = CaptureProvider()

    def request(self, provider=None, sid=None):
        return request_sms(self.accounts, sid or self.sid, {'phone': ADMIN_NUMBER[3:]},
                           provider or self.provider, source='isolated-client')

    def verify(self, challenge, code, sid=None):
        return verify_sms(self.accounts, sid or self.sid, {'challengeId': challenge, 'code': code})

    @staticmethod
    def unconfirmed(reason='gateway_response_timeout'):
        return CaptureProvider({'accepted': False, 'delivery': 'unconfirmed', 'reason': reason})

    @contextmanager
    def rejects(self, *codes):
        with self.assertRaises(APIError) as caught:
            yield
        self.assertIn(caught.exception.code, codes)

    def test_current_identity_permissions_rotation_and_replay(self):
        result = self.request()
        phone, code, cid, expires = self.provider.calls[0]
        self.assertEqual(phone, ADMIN_NUMBER)
        self.assertTrue(len(code) == 6 and code.isascii() and code.isdigit())
        self.assertEqual((cid, result['resendAfter'], expires), (result['challengeId'], RESEND, iso(self.now + TTL)))
        with self.accounts.tx() as db:
            actor = self.accounts.account(db)
            old_grants = self.accounts.permissions(db, actor)
            challenge = db.execute('SELECT * FROM hosted_sms_login').fetchone()
            self.assertEqual(len(challenge['code_mac']), 64)
            self.assertEqual(challenge['state'], 'sent')
        view, sid, expiry = self.verify(cid, code)
        self.assertTrue(view['authenticated'])
        self.assertTrue(view['capabilities']['fullAccessAdministrator'])
        self.assertEqual(view['authMethod'], 'phone_otp')
        self.assertEqual(view['user']['id'], actor['id'])
        self.assertEqual(view['permissions'], old_grants)
        self.assertNotEqual(self.sid, sid)
        with self.accounts.tx() as db:
            session, actor_now = self.accounts.valid(db, sid, require_user=True)
            self.assertEqual((session['credential_version'], session['expires_at']), (actor_now['credential_version'], expiry))
            self.assertEqual(db.execute('SELECT count(*) FROM account').fetchone()[0], 1)
        with self.rejects('SESSION_EXPIRED'):
            self.verify(cid, code)
        with self.rejects('ALREADY_AUTHENTICATED'):
            self.request(sid=sid)
        with self.rejects('OTP_INVALID_OR_EXPIRED'):
            self.verify(cid, code, self.accounts.session_view('')[1])

    def test_wrong_session_expiration_and_bad_formats(self):
        cid = self.request()['challengeId']
        code = self.provider.calls[-1][1]
        different_sid = self.accounts.session_view('')[1]
        with self.rejects('OTP_INVALID_OR_EXPIRED'):
            self.verify(cid, code, different_sid)
        for bad_code in ('１２３４５６', '12345', '', '1234567'):
            with self.rejects('INVALID_REQUEST'):
                self.verify(cid, bad_code)
        self.now += TTL
        with self.rejects('OTP_INVALID_OR_EXPIRED'):
            self.verify(cid, code)

    def test_unconfirmed_reasons_only_enable_verification_of_the_bound_code(self):
        for reason in ('gateway_response_timeout', 'gateway_connection_lost', 'gateway_temporarily_unavailable',
                       'gateway_delivery_unconfirmed'):
            with self.subTest(reason=reason):
                provider = self.unconfirmed(reason)
                self.sid = self.accounts.session_view('')[1]
                result = self.request(provider)
                _, code, cid, _ = provider.calls[0]
                self.assertEqual(set(result), {'challengeId', 'resendAt', 'resendAfter', 'expiresAt', 'delivery', 'reason'})
                self.assertEqual((result['delivery'], result['reason']), ('unconfirmed', reason))
                with self.accounts.tx() as db:
                    state = db.execute('SELECT state FROM hosted_sms_login WHERE challenge_id=?', (cid,)).fetchone()[0]
                    self.assertEqual(state, 'delivery_unconfirmed')
                    self.assertIsNone(self.accounts.valid(db, self.sid)[0]['account_id'])
                with self.rejects('OTP_INVALID_OR_EXPIRED'):
                    self.verify(cid, code, self.accounts.session_view('')[1])
                wrong = f'{(int(code) + 1) % 1000000:06d}'
                with self.rejects('OTP_INVALID_OR_EXPIRED'):
                    self.verify(cid, wrong)
                self.assertTrue(self.verify(cid, code)[0]['capabilities']['fullAccessAdministrator'])
                with self.rejects('SESSION_EXPIRED'):
                    self.verify(cid, code)
                self.now += RESEND

    def test_unconfirmed_expires_and_rechecks_privileges(self):
        provider = self.unconfirmed()
        cid = self.request(provider)['challengeId']
        code = provider.calls[0][1]
        with self.rejects('RATE_LIMITED'):
            self.request(provider)
        with self.accounts.tx() as db:
            db.execute("UPDATE account SET status='disabled'")
        with self.rejects('SMS_LOGIN_UNAVAILABLE'):
            self.verify(cid, code)
        with self.accounts.tx() as db:
            db.execute("UPDATE account SET status='active'")
        self.now += TTL
        with self.rejects('OTP_INVALID_OR_EXPIRED'):
            self.verify(cid, code)

    def test_wrong_codes_committed_and_resend_does_not_clear_strikes(self):
        cid = self.request()['challengeId']
        code = self.provider.calls[-1][1]
        wrong = '000001' if code == '000000' else '000000'
        for i in range(4):
            with self.rejects('OTP_INVALID_OR_EXPIRED'):
                self.verify(cid, wrong)
        self.now += RESEND
        uncertain = self.unconfirmed()
        new_cid = self.request(uncertain)['challengeId']
        new_code = uncertain.calls[-1][1]
        wrong = '000001' if new_code == '000000' else '000000'
        with self.rejects('OTP_ATTEMPTS_EXCEEDED'):
            self.verify(new_cid, wrong)
        with self.rejects('OTP_ATTEMPTS_EXCEEDED'):
            self.verify(new_cid, new_code)
        with self.rejects('OTP_ATTEMPTS_EXCEEDED'):
            self.request()
        self.now += LOCK
        self.sid = self.accounts.session_view('')[1]
        self.assertIn('challengeId', self.request())

    def test_resend_invalidates_old_message(self):
        cid = self.request()['challengeId']
        old = self.provider.calls[-1][1]
        with self.rejects('RATE_LIMITED'):
            self.request()
        self.now += RESEND
        latest = self.request()['challengeId']
        fresh = self.provider.calls[-1][1]
        self.assertTrue(old != fresh)
        with self.rejects('OTP_INVALID_OR_EXPIRED'):
            self.verify(cid, old)
        with self.rejects('OTP_INVALID_OR_EXPIRED'):
            self.verify(latest, old)
        self.assertTrue(self.verify(latest, fresh)[0]['authenticated'])

    def test_hourly_send_limit_persists_across_sessions(self):
        for _ in range(5):
            self.request(sid=self.accounts.session_view('')[1])
            self.now += RESEND
        with self.rejects('RATE_LIMITED'):
            self.request(sid=self.accounts.session_view('')[1])
        self.assertEqual(len(self.provider.calls), 5)
        self.now += 3600
        self.assertIn('challengeId', self.request(sid=self.accounts.session_view('')[1]))

    def test_malformed_receipt_or_unclassified_failure_cannot_enable_verification(self):
        with self.rejects('SMS_NOT_CONFIGURED'):
            self.request(DisabledTransport())
        for response in ({'accepted': False}, {'accepted': 1}, {}, RuntimeError('upstream timeout'),
                         {'accepted': 0, 'delivery': 'unconfirmed', 'reason': 'gateway_response_timeout'},
                         {'delivery': 'unconfirmed', 'reason': 'gateway_response_timeout'},
                         {'accepted': True, 'delivery': 'unconfirmed', 'reason': 'gateway_response_timeout'},
                         {'accepted': False, 'delivery': 'unconfirmed', 'reason': 'untrusted text'},
                         {'accepted': False, 'delivery': 'unconfirmed', 'reason': ['gateway_response_timeout']},
                         {'accepted': False, 'delivery': 'rejected', 'reason': 'gateway_response_timeout'}):
            provider = CaptureProvider(response)
            self.sid = self.accounts.session_view('')[1]
            with self.rejects('SMS_DELIVERY_FAILED'):
                self.request(provider)
            _, captured, cid, _ = provider.calls[-1]
            with self.rejects('OTP_INVALID_OR_EXPIRED'):
                self.verify(cid, captured)
            self.now += 3600

    def test_only_known_gateway_error_codes_are_in_error_details(self):
        for code in ('SMS_GATEWAY_CONNECT_TIMEOUT', 'SMS_GATEWAY_DNS_ERROR', 'SMS_GATEWAY_TLS_ERROR',
                     'SMS_GATEWAY_RATE_LIMITED', 'SMS_GATEWAY_PROTOCOL_ERROR', 'SMS_GATEWAY_UNAVAILABLE',
                     'SMS_GATEWAY_REJECTED', 'unknown provider diagnostic'):
            with self.subTest(code=code):
                self.sid = self.accounts.session_view('')[1]
                error = RuntimeError('private response body')
                error.code = code
                with self.assertRaises(APIError) as caught:
                    self.request(CaptureProvider(error))
                failure = caught.exception
                self.assertEqual((failure.status, failure.code), (503, 'SMS_DELIVERY_FAILED'))
                expected = {} if code == 'unknown provider diagnostic' else {'gatewayStatus': code}
                self.assertEqual(failure.details, expected)
                self.assertNotIn('private response body', failure.message)
                self.assertNotIn('unknown provider diagnostic', failure.message)
                self.now += 3600

    def test_unlisted_or_unprovisioned_number_never_sends(self):
        with self.rejects('SMS_LOGIN_UNAVAILABLE'):
            request_sms(self.accounts, self.sid, {'phone': '13800000000'}, self.provider)
        with self.accounts.tx() as db:
            db.execute("UPDATE account SET username='other_admin'")
        with self.rejects('SMS_LOGIN_UNAVAILABLE'):
            self.request()
        self.assertEqual(len(self.provider.calls), 0)

    def test_account_and_grant_changes(self):
        cid = self.request()['challengeId']
        code = self.provider.calls[-1][1]
        changes = [
            ("UPDATE account SET status='disabled'", "UPDATE account SET status='active'"),
            ("UPDATE account SET phone='13800000000'", "UPDATE account SET phone=NULL"),
            ("DELETE FROM system_permissions", "INSERT INTO system_permissions SELECT id,'accounts.manage' FROM account"),
            ("UPDATE account SET expires_at=created_at", "UPDATE account SET expires_at=253402214400"),
        ]
        for mutate, restore in changes:
            with self.accounts.tx() as db:
                db.execute(mutate)
            with self.rejects('SMS_LOGIN_UNAVAILABLE'):
                self.verify(cid, code)
            with self.rejects('SMS_LOGIN_UNAVAILABLE'):
                self.request()
            with self.accounts.tx() as db:
                db.execute(restore)
        with self.accounts.tx() as db:
            db.execute('UPDATE account SET credential_version=credential_version+1')
        with self.rejects('OTP_INVALID_OR_EXPIRED'):
            self.verify(cid, code)

    def test_concurrent_send_and_verify_have_one_winner(self):
        def pair(fn):
            gate = threading.Barrier(2)
            def attempt():
                gate.wait(4)
                try:
                    return 'OK', fn()
                except APIError as exc:
                    return exc.code, None
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(attempt) for _ in range(2)]
                return [future.result(8)[0] for future in futures]
        self.assertCountEqual(pair(self.request), ['OK', 'RATE_LIMITED'])
        self.assertEqual(len(self.provider.calls), 1)
        _, code, cid, _ = self.provider.calls[0]
        self.assertCountEqual(pair(lambda: self.verify(cid, code)), ['OK', 'SESSION_EXPIRED'])
        with self.accounts.tx() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM sessions WHERE account_id IS NOT NULL').fetchone()[0], 1)

    def test_late_acknowledgement_cannot_revive_superseded_challenge(self):
        invoked = threading.Event()
        finish = threading.Event()
        class Deferred(CaptureProvider):
            def send_login_code(internal, *args):
                result = super().send_login_code(*args)
                invoked.set()
                if not finish.wait(5):
                    raise RuntimeError('test callback deadline')
                return result
        deferred = Deferred(self.unconfirmed('gateway_connection_lost').response)
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(self.request, deferred)
            try:
                self.assertTrue(invoked.wait(4))
                self.now += RESEND
                current = self.request()['challengeId']
            finally:
                finish.set()
            with self.rejects('OTP_REQUEST_SUPERSEDED'):
                pending.result(5)
        _, previous, old_id, _ = deferred.calls[0]
        with self.rejects('OTP_INVALID_OR_EXPIRED'):
            self.verify(old_id, previous)
        self.assertTrue(self.verify(current, self.provider.calls[0][1])[0]['authenticated'])


if __name__ == '__main__':
    unittest.main()
