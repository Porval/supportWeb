"""Exercise the released login routes against SQLite with SMS delivery simulated in memory."""
import http.client
import io
import json
import re
import secrets
import tempfile
import threading
import unittest
from http.cookies import SimpleCookie
from pathlib import Path
from unittest.mock import MagicMock, patch

import server
import workbench_sms_gateway as sms


class SmsDeliveryLoginTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name).resolve()
        data = root / 'data'
        data.mkdir(mode=0o700)
        (root / 'bootstrap.json').write_text(json.dumps({
            'username': server.ADMIN_PHONE, 'displayName': '隔离验收管理员',
            'saltHex': secrets.token_hex(24), 'verifierHex': secrets.token_hex(32),
        }))
        gateway = sms.SMSGateway('https://apl1.pricetan.cn/isolated', login_enabled=True)
        with patch.object(server, 'ROOT', root):
            self.app = server.create_service('127.0.0.1', 0, data, sms_transport=gateway)
        self.addCleanup(self.app.business.close)
        self.addCleanup(self.app.server_close)
        worker = threading.Thread(target=self.app.serve_forever, kwargs={'poll_interval': 0.05})
        worker.start()
        self.addCleanup(worker.join, 3)
        self.addCleanup(self.app.shutdown)

    def call(self, suffix, status, body=None, cookie='', csrf=''):
        c = http.client.HTTPConnection('127.0.0.1', self.app.server_port, timeout=5)
        try:
            headers = {'Cookie': cookie, 'X-CSRF-Token': csrf, 'Origin': server.ORIGIN}
            if body is not None:
                headers['Content-Type'] = 'application/json'
            method = 'GET' if body is None else 'POST'
            c.request(method, server.API + '/account/' + suffix,
                      body=None if body is None else json.dumps(body), headers=headers)
            response = c.getresponse()
            envelope = json.loads(response.read())
            self.assertEqual(response.status, status, envelope.get('error', {}).get('code'))
            return envelope.get('data', envelope.get('error')), response.getheader('Set-Cookie')
        finally:
            c.close()

    @staticmethod
    def cookie(header):
        return 'activity_session=' + SimpleCookie(header)['activity_session'].value

    def test_only_code_sent_in_workbench_message_grants_session(self):
        self.check_login('provider_accepted')

    def test_sms_arrived_before_gateway_timeout_allows_only_bound_code(self):
        self.check_login('unconfirmed')

    def test_explicit_gateway_rejection_invalidates_code(self):
        self.check_login('rejected')

    def test_protocol_error_invalidates_code(self):
        self.check_login('invalid')

    def check_login(self, outcome):
        pending, header = self.call('session', 200)
        self.assertEqual(pending['authMethod'], 'phone_otp')
        cookie, csrf = self.cookie(header), pending['csrfToken']
        self.call('login', 404, {'username': server.ADMIN_PHONE, 'password': 'unused'}, cookie, csrf)

        connection = MagicMock()
        reply = connection.getresponse.return_value.__enter__.return_value
        reply.status, reply.getheader.return_value = 200, None
        phone = server.ADMIN_PHONE
        delivered, returned = [], []

        def send(method, resource, payload, headers):
            self.assertEqual((method, resource), ('POST', '/isolated/api/sms/requestCode'))
            requested = json.loads(payload)
            self.assertEqual(set(requested), {'number', 'template'})
            self.assertEqual(requested['number'], phone)
            message = requested['template']
            codes = re.findall(r'(?<![0-9])[0-9]{6}(?![0-9])', message)
            self.assertEqual(len(codes), 1)
            self.assertEqual(message, sms.TEMPLATE.replace('${code}', codes[0]))
            delivered.append(codes[0])
            returned.append(f'{(int(codes[0]) + 1) % 1000000:06d}')
            response = {
                'success': True, 'smsSent': True, 'verificationCode': returned[-1],
                'expiresInMs': 300000, 'cooldownMs': 60000, 'msg': 'ok',
            }
            if outcome == 'rejected':
                response = {'success': False, 'msg': 'private provider detail'}
            elif outcome == 'invalid':
                response['smsSent'] = 'unexpected private field'
            raw = json.dumps(response).encode()
            wire = io.BytesIO(raw)
            reply.length = len(raw)
            reply.read1.side_effect = wire.read1
            reply.read.side_effect = wire.read

        connection.request.side_effect = send
        if outcome == 'unconfirmed':
            connection.getresponse.side_effect = TimeoutError('private provider detail')
        with patch.object(sms.http.client, 'HTTPSConnection', return_value=connection), \
                patch.object(sms.ssl, 'create_default_context', return_value=object()):
            if outcome in ('rejected', 'invalid'):
                failure, _ = self.call('auth/otp/request', 503, {'phone': phone}, cookie, csrf)
                self.assertEqual(failure['code'], 'SMS_DELIVERY_FAILED')
                err = 'SMS_GATEWAY_REJECTED' if outcome == 'rejected' else 'SMS_GATEWAY_PROTOCOL_ERROR'
                self.assertEqual(failure['details'], {'gatewayStatus': err})
                self.assertNotIn('private', failure['message'])
                with self.app.accounts.tx() as db:
                    row = db.execute('SELECT challenge_id, state FROM hosted_sms_login').fetchone()
                self.assertEqual(row['state'], 'failed')
                error, _ = self.call('auth/otp/verify', 400, {'challengeId': row['challenge_id'], 'code': delivered[0]},
                                     cookie, csrf)
                self.assertEqual(error['code'], 'OTP_INVALID_OR_EXPIRED')
                return
            receipt, _ = self.call('auth/otp/request', 200, {'phone': phone}, cookie, csrf)
            self.assertEqual(receipt['delivery'], outcome)
            expected = {'challengeId', 'resendAfter', 'resendAt', 'expiresAt', 'delivery'}
            if outcome == 'unconfirmed':
                self.assertEqual(receipt['reason'], 'gateway_response_timeout')
                expected.add('reason')
            self.assertEqual(set(receipt), expected)
            challenge = receipt['challengeId']
            wrong = {'challengeId': challenge, 'code': returned[0]}
            error, _ = self.call('auth/otp/verify', 400, wrong, cookie, csrf)
            self.assertEqual(error['code'], 'OTP_INVALID_OR_EXPIRED')
            correct = {'challengeId': challenge, 'code': delivered[0]}
            user, new_header = self.call('auth/otp/verify', 200, correct, cookie, csrf)
            self.assertTrue(user['authenticated'])
            self.assertEqual(user['user']['username'], phone)
            self.assertTrue(user['capabilities']['fullAccessAdministrator'])
            self.assertTrue(user['capabilities']['activityAgentChat'])
            self.assertTrue({'accounts.manage','reviews.decide','operations.read','submissions.create'} <= set(user['permissions']))
            self.assertNotEqual(cookie, self.cookie(new_header))
            self.call('auth/otp/verify', 401, correct, cookie, csrf)
            fresh, fresh_header = self.call('session', 200)
            self.call('auth/otp/verify', 400, correct, self.cookie(fresh_header), fresh['csrfToken'])
            self.assertEqual(connection.request.call_count, 1)


if __name__ == '__main__':
    unittest.main(verbosity=2)
