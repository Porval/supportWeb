"""Isolated tests for anonymous read-only viewing, account creation, multi-user OTP, and signed SMS gateway."""
import hashlib
import http.client
import io
import json
from pathlib import Path
import secrets
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import server as hosted_server
import workbench_sms_gateway as gw_mod


class DummySmsTransport:
    status = 'configured'
    login_enabled = True

    def __init__(self):
        self.last_code = None
        self.last_number = None

    def send_login_code(self, number, code, cid, expires):
        self.last_number = number
        self.last_code = code
        return {'accepted': True}


class FakeSocket:
    def __init__(self, raw_request):
        self.rfile = io.BytesIO(raw_request)
        self.wfile = io.BytesIO()

    def makefile(self, mode, *args, **kwargs):
        return self.rfile if 'r' in mode else self.wfile

    def sendall(self, data):
        self.wfile.write(data)


class Client:
    def __init__(self, server):
        self.server = server
        self.port = server.server_port
        self.cookie = ''
        self.csrf = ''

    def request(self, method, path, body=None, *, csrf=None, origin=hosted_server.ORIGIN, idem_key=None):
        headers = {'Host': f'127.0.0.1:{self.port}'}
        if self.cookie:
            headers['Cookie'] = self.cookie
        if origin is not None:
            headers['Origin'] = origin
        if idem_key is not None:
            headers['Idempotency-Key'] = idem_key
        payload = b''
        if body is not None:
            payload = json.dumps(body, ensure_ascii=False).encode()
            headers['Content-Type'] = 'application/json'
            headers['Content-Length'] = str(len(payload))
            token = self.csrf if csrf is None else csrf
            if token:
                headers['X-CSRF-Token'] = token
        head = f'{method} {path} HTTP/1.1\r\n' + ''.join(f'{k}: {v}\r\n' for k, v in headers.items()) + '\r\n'
        sock = FakeSocket(head.encode() + payload)
        hosted_server.Handler(sock, ('127.0.0.1', 12345), self.server)
        raw_resp = sock.wfile.getvalue()
        resp = http.client.HTTPResponse(type('S', (), {'makefile': lambda self_, *a, **k: io.BytesIO(raw_resp)})())
        resp.begin()
        raw = resp.read()
        set_cookie = resp.getheader('Set-Cookie')
        if set_cookie and 'activity_session=' in set_cookie:
            pair = set_cookie.split(';', 1)[0]
            self.cookie = '' if pair == 'activity_session=' else pair
        ctype = resp.getheader('Content-Type') or ''
        parsed = json.loads(raw.decode()) if ctype.startswith('application/json') else raw
        if isinstance(parsed, dict) and isinstance(parsed.get('data'), dict) and parsed['data'].get('csrfToken'):
            self.csrf = parsed['data']['csrfToken']
        return resp.status, parsed


def seed_formal_and_draft_data(accounts):
    from b_state import canon
    now = accounts.clock()
    formal_bytes = b'formal material content'
    draft_bytes = b'draft-only secret content'
    formal_sha = hashlib.sha256(formal_bytes).hexdigest()
    draft_sha = hashlib.sha256(draft_bytes).hexdigest()
    with accounts.tx() as db:
        admin = db.execute("SELECT id FROM account LIMIT 1").fetchone()[0]
        db.execute("UPDATE metadata SET value='true' WHERE key='relationsComplete'")
        db.execute("INSERT INTO brands(id,body) VALUES(?,?)", ('BR-1', canon({'id': 'BR-1', 'name': '测试品牌', 'benefit': None})))
        db.execute("INSERT INTO products(id,body) VALUES(?,?)", ('PRD-1', canon({
            'id': 'PRD-1', 'internalCode': 'INT-001', 'upc': '6900000000001', 'name': '测试商品', 'spec': '10片',
            'brandId': 'BR-1', 'relationComplete': True,
        })))
        db.execute("INSERT INTO material_blobs VALUES(?,?,?,?,?,?,?,?,?)",
                   ('MAT-1', admin, 'formal.txt', 'text/plain', formal_sha, len(formal_bytes), formal_bytes, now, 'supported'))
        db.execute("INSERT INTO material_blobs VALUES(?,?,?,?,?,?,?,?,?)",
                   ('MAT-DRAFT', admin, 'draft.txt', 'text/plain', draft_sha, len(draft_bytes), draft_bytes, now, 'supported'))
        db.execute("INSERT INTO b_submissions VALUES(?,?,?,?,?,?,?,?)",
                   ('SUB-1', admin, 1, 'resolved', 'ACT-1', 'VER-1', None, now))
        db.execute("INSERT INTO submission_revisions VALUES(?,?,?,?)",
                   ('SUB-1', 1, 'd1', canon({'form': {}, 'text': 'formal text', 'materialIds': ['MAT-1']})))
        db.execute("INSERT INTO b_submissions VALUES(?,?,?,?,?,?,?,?)",
                   ('SUB-DRAFT', admin, 1, 'needs_confirmation', None, None, None, now))
        db.execute("INSERT INTO submission_revisions VALUES(?,?,?,?)",
                   ('SUB-DRAFT', 1, 'd2', canon({'form': {}, 'text': 'draft text', 'materialIds': ['MAT-DRAFT']})))
        db.execute("INSERT INTO analysis_runs VALUES(?,?,?,?,?,?,?,?,?,?)",
                   ('RUN-DRAFT', 'SUB-DRAFT', 1, 'd2', 'hosted-catalog-v1', 'succeeded', canon({'phase': 'done'}), now, now, now))
        activity = {
            'id': 'ACT-1', 'name': '测试满减活动', 'type': 'brand', 'platformCode': 'meituan',
            'projectName': None, 'level': None, 'startAt': '2026-11-01T00:00:00+08:00',
            'endAt': '2026-11-30T23:59:59+08:00', 'rawDate': '2026-11-01至2026-11-30',
            'submitter': admin, 'submittedAt': '2026-11-24T00:00:00+08:00', 'externalIds': [],
            'currentReviewVersionId': 'VER-1', 'displayVersionId': 'VER-1', 'reviewVersionId': 'VER-1',
            'approvedVersionId': None, 'effectiveVersionId': None, 'effectiveExecutionVersionId': None,
            'pendingChangeVersionIds': [], 'revision': 1, 'provenance': {'kind': 'confirmed'},
            'brandIds': ['BR-1'], 'ruleSummary': '满29减15', 'rules': '满29减15', 'brandBenefit': '品牌承担',
            'costEvidence': {'state': 'yes'}, 'review': {'stage': 'initial', 'result': 'pending'},
            'registration': {'state': 'unknown'}, 'execution': {'state': 'unknown'}, 'expiry': {'state': 'current'},
            'productApplicability': {'kind': 'selected_products'}, 'productListState': 'complete',
            'pendingItems': [], 'reviewState': 'initial_pending', 'reviewBlockedReason': None,
            'activityMethod': 'threshold_coupon',
        }
        version = {
            'id': 'VER-1', 'activityId': 'ACT-1', 'versionNo': 1, 'submissionId': 'SUB-1',
            'previousVersionId': None, 'basedOnVersionId': None, 'revision': 1,
            'materialIds': ['MAT-1'], 'changeSummary': '首次建档',
            'snapshot': {
                'resolvedFields': {
                    'activityName': '测试满减活动', 'type': 'brand', 'platformCode': 'meituan',
                    'brandIds': ['BR-1'], 'startAt': '2026-11-01T00:00:00+08:00',
                    'endAt': '2026-11-30T23:59:59+08:00', 'rulesText': '满29减15',
                },
                'applicablePlatformCodes': ['meituan'],
                'products': ['PRD-1'], 'pendingItems': [], 'reviewState': 'initial_pending',
                'activityMethod': 'threshold_coupon',
            },
        }
        db.execute("INSERT INTO activities(id,body) VALUES(?,?)", ('ACT-1', canon(activity)))
        db.execute("INSERT INTO activity_versions(id,body) VALUES(?,?)", ('VER-1', canon(version)))
        db.execute("INSERT INTO version_sources(version_id,submission_id,input_revision,material_ids) VALUES(?,?,?,?)",
                   ('VER-1', 'SUB-1', 1, canon(['MAT-1'])))
        db.execute("INSERT INTO activity_brands(activity_id,brand_id,body) VALUES(?,?,?)",
                   ('ACT-1', 'BR-1', canon({'activityId': 'ACT-1', 'brandId': 'BR-1'})))
        db.execute("INSERT INTO activity_products(activity_id,product_id,body) VALUES(?,?,?)",
                   ('ACT-1', 'PRD-1', canon({'activityId': 'ACT-1', 'productId': 'PRD-1', 'complete': True, 'platformCodes': ['meituan']})))
        db.execute("INSERT INTO version_reviews VALUES(?,?,?,?,?,?)",
                   ('VER-1', 'ACT-1', 1, 'initial_pending', 'manual', 'SUB-1'))
        db.execute("INSERT INTO review_tasks VALUES(?,?,?,?,?,?)",
                   ('TASK-1', 'VER-1', 'initial', admin, 1, 'pending'))


class AnonymousAndAccountsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        root = Path(cls.temp.name).resolve()
        data_dir = root / 'data'
        data_dir.mkdir(mode=0o700)
        (root / 'bootstrap.json').write_text(json.dumps({
            'username': hosted_server.ADMIN_PHONE, 'displayName': '隔离测试管理员',
            'saltHex': secrets.token_hex(24), 'verifierHex': secrets.token_hex(32),
        }))
        cls.sms = DummySmsTransport()
        with patch.object(hosted_server, 'ROOT', root):
            cls.server = hosted_server.create_service('127.0.0.1', 0, data_dir, sms_transport=cls.sms)
        seed_formal_and_draft_data(cls.server.accounts)

    @classmethod
    def tearDownClass(cls):
        cls.server.server_close()
        cls.server.business.close()
        cls.temp.cleanup()

    def test_signed_remote_gateway_config_and_redacted_error(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / gw_mod.ROUTE_FILE).write_text(json.dumps({
                'baseUrl': 'https://apl1.pricetan.cn/api/sample_account',
                'mode': 'signed_remote',
                'appKey': 'synthetic_test_app_key',
                'appSecret': 'synthetic_test_app_secret_value',
                'loginEnabled': True,
            }))
            gw = gw_mod.load_gateway(root)
            self.assertIsNotNone(gw)
            self.assertTrue(gw.login_enabled)
            with patch.object(gw_mod.http.client, 'HTTPSConnection') as dial:
                conn = dial.return_value
                resp = MagicMock()
                resp.__enter__.return_value = resp
                resp.status = 200
                resp.length = None
                resp.getheader.return_value = None
                resp.read1.side_effect = [
                    json.dumps({'success': True, 'smsSent': True, 'msg': 'ok', 'verificationCode': '654321',
                                'expiresInMs': 300000, 'cooldownMs': 60000}).encode(),
                    b'',
                ]
                conn.getresponse.return_value = resp
                result = gw.send_login_code('+86' + hosted_server.ADMIN_PHONE, '111111', 'cid-1', '2026-11-24T10:00:00Z')
                self.assertEqual(result, {'accepted': True, 'issuedCode': '654321'})
                method, path, raw, _ = conn.request.call_args.args
                self.assertEqual((method, path), ('POST', '/api/sample_account/api/sms/requestCode'))
                sent = json.loads(raw)
                self.assertEqual(set(sent), {'number', 'appKey', 'date', 'sign'})
                self.assertEqual(sent['number'], hosted_server.ADMIN_PHONE)
                self.assertEqual(len(sent['sign']), 64)

                resp.status = 400
                resp.read.return_value = json.dumps({'success': False, 'error': '签名错误 654321'}).encode()
                with self.assertRaises(gw_mod.GatewayUnavailable) as exc:
                    gw.send_login_code('+86' + hosted_server.ADMIN_PHONE, '111111', 'cid-2', '2026-11-24T10:00:00Z')
                self.assertEqual(exc.exception.code, 'SMS_GATEWAY_REJECTED')
                self.assertNotIn('654321', exc.exception.detail)

    def test_anonymous_readonly_lifecycle(self):
        c = Client(self.server)
        status, body = c.request('GET', '/api/activity/v1/account/session')
        self.assertEqual(status, 200)
        self.assertFalse(body['data']['authenticated'])
        self.assertFalse(body['data']['anonymous'])
        self.assertTrue(body['data']['capabilities']['anonymousReadOnly'])

        status, _ = c.request('GET', '/api/activity/v1/operations/activities')
        self.assertEqual(status, 401)

        status, _ = c.request('POST', '/api/activity/v1/account/auth/anonymous', {}, csrf='bad')
        self.assertEqual(status, 403)
        status, _ = c.request('POST', '/api/activity/v1/account/auth/anonymous', {}, origin='https://evil.example')
        self.assertEqual(status, 403)

        old_cookie = c.cookie
        status, body = c.request('POST', '/api/activity/v1/account/auth/anonymous', {})
        self.assertEqual(status, 200)
        self.assertNotEqual(c.cookie, old_cookie)
        data = body['data']
        self.assertTrue(data['authenticated'])
        self.assertTrue(data['anonymous'])
        self.assertTrue(data['readOnly'])
        self.assertFalse(data['capabilities']['fullAccessAdministrator'])
        self.assertFalse(data['capabilities']['activityAgentChat'])

        for path in (
            '/api/activity/v1/operations/context',
            '/api/activity/v1/operations/capabilities',
            '/api/activity/v1/operations/catalogs',
            '/api/activity/v1/operations/activities',
            '/api/activity/v1/operations/activities/ACT-1',
            '/api/activity/v1/operations/activities/ACT-1/versions/VER-1',
            '/api/activity/v1/operations/brands',
            '/api/activity/v1/operations/products',
            '/api/activity/v1/operations/materials/MAT-1',
            '/api/activity/v1/operations/exports/snapshot?kind=activity_products&activityId=ACT-1',
        ):
            status, payload = c.request('GET', path)
            self.assertEqual(status, 200, f'Expected 200 for {path}, got {status}: {payload}')

        for path, expected in (
            ('/api/activity/v1/operations/submissions', 403),
            ('/api/activity/v1/operations/analysis-runs/RUN-DRAFT', 403),
            ('/api/activity/v1/operations/reviews?view=pending', 403),
            ('/api/activity/v1/operations/materials/MAT-DRAFT', 404),
            ('/api/activity/v1/account/users', 403),
            ('/api/activity/v1/schedule/context', 403),
            ('/api/activity/v1/agent/chat', 403),
        ):
            status, payload = c.request('GET', path)
            self.assertEqual(status, expected, f'Expected {expected} for GET {path}, got {status}: {payload}')

        status, payload = c.request('POST', '/api/activity/v1/operations/submissions', {'textInput': 'x'})
        self.assertEqual(status, 403)
        self.assertEqual(payload['error']['code'], 'ANONYMOUS_READ_ONLY')

        status, body = c.request('POST', '/api/activity/v1/account/logout', {})
        self.assertEqual(status, 200)
        self.assertTrue(body['data']['loggedOut'])

    def test_create_account_and_multi_user_otp(self):
        admin = Client(self.server)
        admin.request('GET', '/api/activity/v1/account/session')
        status, req = admin.request('POST', '/api/activity/v1/account/auth/otp/request', {'phone': hosted_server.ADMIN_PHONE})
        self.assertEqual(status, 200, req)
        status, ver = admin.request('POST', '/api/activity/v1/account/auth/otp/verify',
                                    {'challengeId': req['data']['challengeId'], 'code': self.sms.last_code})
        self.assertEqual(status, 200, ver)

        status, err = admin.request('POST', '/api/activity/v1/account/users', {'name': '张三', 'phone': '12345'})
        self.assertEqual(status, 400)
        self.assertEqual(err['error']['code'], 'INVALID_PHONE')

        status, created = admin.request('POST', '/api/activity/v1/account/users', {
            'name': '运营成员', 'phone': '13900001235', 'businessRole': 'operations',
            'remark': '测试账号', 'accountManagementAuthorized': False,
        })
        self.assertEqual(status, 201, created)
        user = created['data']['user']
        self.assertEqual(user['username'], '13900001235')
        self.assertFalse(user['accountManagementAuthorized'])

        status, dup = admin.request('POST', '/api/activity/v1/account/users', {
            'name': '重复成员', 'phone': '13900001235', 'businessRole': 'marketing',
        })
        self.assertEqual(status, 409)
        self.assertEqual(dup['error']['code'], 'ACCOUNT_EXISTS')

        member = Client(self.server)
        member.request('GET', '/api/activity/v1/account/session')
        status, mreq = member.request('POST', '/api/activity/v1/account/auth/otp/request', {'phone': '13900001235'})
        self.assertEqual(status, 200, mreq)
        status, mver = member.request('POST', '/api/activity/v1/account/auth/otp/verify',
                                      {'challengeId': mreq['data']['challengeId'], 'code': self.sms.last_code})
        self.assertEqual(status, 200, mver)
        self.assertIn('submissions.create', mver['data']['permissions'])
        self.assertNotIn('accounts.manage', mver['data']['permissions'])

        status, _ = member.request('POST', '/api/activity/v1/account/users', {'name': '越权', 'phone': '13900001236'})
        self.assertEqual(status, 403)

        status, updated = admin.request(
            'PUT', f"/api/activity/v1/account/users/{user['id']}/profile",
            {'expectedVersion': 1, 'name': '运营成员', 'phone': '+8613900001235', 'businessRole': 'finance', 'remark': None},
            idem_key='22222222-2222-4222-8222-222222222222',
        )
        self.assertEqual(status, 200, updated)
        status, msess = member.request('GET', '/api/activity/v1/account/session')
        self.assertEqual(status, 200)
        self.assertNotIn('submissions.create', msess['data']['permissions'])
        self.assertIn('operations.read', msess['data']['permissions'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
