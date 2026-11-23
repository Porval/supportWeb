import http.client
import json
import os
from pathlib import Path
import socket
import ssl
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import workbench_sms_gateway as gateway


BASE = 'https://' + gateway.HOST + '/sample-route'
PHONE = '13900000000'
OTP = '312764'


class GatewayTest(unittest.TestCase):
    def setUp(self):
        self.sandbox = tempfile.TemporaryDirectory()
        self.addCleanup(self.sandbox.cleanup)
        self.root = Path(self.sandbox.name)
        self.route = self.root / gateway.ROUTE_FILE
        fake = patch.object(http.client, 'HTTPSConnection')
        self.dial = fake.start()
        self.addCleanup(fake.stop)
        self.connection = self.dial.return_value
        self.response = MagicMock()
        self.response.status = 200
        self.response.__enter__.return_value = self.response
        self.response.getheader.return_value = None
        self.connection.getresponse.return_value = self.response
        self.client = gateway.SMSGateway(BASE)

    def reply(self, value):
        self.response.read.return_value = json.dumps(value).encode()

    def test_optional_route_requires_valid_private_service_resource(self):
        self.assertIsNone(gateway.load_gateway(self.root))
        self.route.write_text(json.dumps({'baseUrl': BASE + '/'}))
        self.assertTrue(gateway.load_gateway(self.root))
        for raw in ['{}', '{"baseUrl":null}', '{"baseUrl":false}', '{"baseUrl":"x","baseUrl":"y"}',
                    '{"baseUrl":"x","token":"confidential"}', 'x'*2050, 'null']:
            self.route.write_text(raw)
            with self.assertRaisesRegex(gateway.SmsRouteError, '^工作台短信网关配置无效$'):
                gateway.load_gateway(self.root)
        self.route.unlink()
        self.route.symlink_to(self.root / 'absent')
        with self.assertRaises(gateway.SmsRouteError):
            gateway.load_gateway(self.root)
        self.route.unlink()
        os.mkfifo(self.route, 0o600)
        with self.assertRaises(gateway.SmsRouteError):
            gateway.load_gateway(self.root)
        self.dial.assert_not_called()

    def test_strict_origin_and_path(self):
        invalid = ['http://' + gateway.HOST, 'https://' + gateway.HOST + '.x', 'https://x@' + gateway.HOST,
                   BASE + '?q=private', BASE + '#secret', BASE + '/..', BASE + '/./b', BASE + '//b',
                   BASE + '/%2e%2e', BASE + '/x' + chr(92) + 'y', BASE + '\nfoo',
                   'https://' + gateway.HOST + ':444', 'https://[invalid', 123]
        for url in invalid:
            with self.subTest(url_type=type(url).__name__), self.assertRaises(gateway.SmsRouteError):
                gateway.SMSGateway(url)
        with self.assertRaises(ValueError):
            self.client.request_code(PHONE + '\n')
        with self.assertRaises(ValueError):
            self.client.verify_code(PHONE, '２３４５６７')
        self.dial.assert_not_called()

    def test_request_uses_verified_https_and_exact_body_then_verify(self):
        self.reply({'success': True})
        with patch.dict(os.environ, {'HTTPS_PROXY': 'http://ignore.invalid:9000'}):
            self.assertIs(self.client.request_code(PHONE), True)
        args, kwargs = self.dial.call_args
        self.assertEqual(args, (gateway.HOST,))
        self.assertTrue(kwargs['context'].check_hostname)
        self.assertEqual(kwargs['context'].verify_mode, ssl.CERT_REQUIRED)
        method, path, body, headers = self.connection.request.call_args.args
        self.assertEqual((method, path), ('POST', '/sample-route/api/sms/requestCode'))
        self.assertEqual(json.loads(body), {'number': PHONE, 'template': gateway.TEMPLATE})
        self.assertNotIn('Authorization', headers)
        self.reply({'success': True, 'error': False, 'verified': True})
        self.assertIs(self.client.verify_code(PHONE, OTP), True)
        self.assertEqual(self.connection.request.call_args.args[1], '/sample-route/api/sms/verifyCode')
        self.assertEqual(json.loads(self.connection.request.call_args.args[2]), {'number': PHONE, 'code': OTP})
        self.assertEqual(self.connection.close.call_count, 2)

    def test_business_rejection_is_distinct_from_unavailable(self):
        self.reply({'success': False, 'error': True})
        self.assertIs(self.client.request_code(PHONE), False)
        self.reply({'success': True, 'verified': False})
        self.assertIs(self.client.verify_code(PHONE, OTP), False)
        for shape in [{}, [], {'error': False}, {'success': 1}, {'success':'true'},
                      {'success': True, 'error': True}, {'success': True, 'error': 'failure'},
                      {'success': True, 'verified': 'false'}]:
            self.reply(shape)
            with self.assertRaises(gateway.GatewayUnavailable) as error:
                self.client.verify_code(PHONE, OTP)
            self.assertEqual(error.exception.code, 'SMS_GATEWAY_PROTOCOL_ERROR')

    def test_credentials_or_plaintext_code_in_receipt_rejected_in_both_stages(self):
        for suspicious in [{'code': OTP}, {'otp': int(OTP)}, {'vCode': OTP}, {'verify_code': OTP},
                           {'accessToken': 'private'}, {'user_id': 'forbidden'}, {'sessionId': 'private'},
                           {'account': {}}, {'openId': 'forbidden'}]:
            self.reply({'success': True, 'data': [{'nested': suspicious}]})
            for operation in [lambda: self.client.request_code(PHONE), lambda: self.client.verify_code(PHONE, OTP)]:
                with self.assertRaisesRegex(gateway.GatewayUnavailable, '^SMS_GATEWAY_PROTOCOL_ERROR$'):
                    operation()
        self.reply({'success': True})
        self.response.getheader.return_value = 'account_session=private'
        with self.assertRaises(gateway.GatewayUnavailable):
            self.client.verify_code(PHONE, OTP)

    def test_malformed_and_oversized_responses(self):
        for raw in [b'{"success":true,"success":false}', b'non JSON', b'\xff', b'{' * (gateway.MAX_RECEIPT + 1),
                    b'{"success":true,"timing":Infinity}']:
            self.response.read.return_value = raw
            with self.assertRaisesRegex(gateway.GatewayUnavailable, '^SMS_GATEWAY_PROTOCOL_ERROR$'):
                self.client.request_code(PHONE)
            self.response.read.assert_called_with(gateway.MAX_RECEIPT + 1)

    def test_http_errors_and_timeouts_do_not_expose_payload_or_follow_redirects(self):
        self.reply({'success': True, 'private': OTP})
        for status in (302, 401, 429, 500):
            self.response.status = status
            with self.assertRaises(gateway.GatewayUnavailable) as caught:
                self.client.verify_code(PHONE, OTP)
            self.assertEqual(caught.exception.code, 'SMS_GATEWAY_RATE_LIMITED' if status == 429 else 'SMS_GATEWAY_UNAVAILABLE')
        self.assertEqual(self.connection.request.call_count, 4)
        self.assertEqual(self.response.read.call_count, 1)
        for failure in (socket.timeout(OTP), ssl.SSLError(BASE), http.client.RemoteDisconnected(PHONE)):
            self.connection.request.side_effect = failure
            with self.assertRaisesRegex(gateway.GatewayUnavailable, '^SMS_GATEWAY_UNAVAILABLE$'):
                self.client.verify_code(PHONE, OTP)

    def test_explicit_verify_rejections_on_http_errors_allow_retry(self):
        for status in (400, 401, 403, 422):
            self.response.status = status
            for rejection in ({'success': False}, {'verified': False}, {'success': True, 'verified': False}):
                self.reply(rejection)
                self.assertIs(self.client.verify_code(PHONE, OTP), False)
                with self.assertRaises(gateway.GatewayUnavailable):
                    self.client.request_code(PHONE)
            for ambiguous in ({'error': 'access denied'}, {'success': True}, {'success':'false'},
                              {'success': False, 'data': {'access_token': 'secret'}}):
                self.reply(ambiguous)
                with self.assertRaises(gateway.GatewayUnavailable):
                    self.client.verify_code(PHONE, OTP)


if __name__ == '__main__':
    unittest.main()
