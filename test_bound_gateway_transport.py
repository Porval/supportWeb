import http.client
import json
import socket
import ssl
import unittest
from unittest.mock import MagicMock, patch

from workbench_sms_gateway import GatewayUnavailable, SMSGateway, SmsRouteError


class IndependentCodeDeliveryTest(unittest.TestCase):
    def setUp(self):
        dial = patch('workbench_sms_gateway.http.client.HTTPSConnection')
        self.connection = dial.start().return_value
        self.addCleanup(dial.stop)
        self.response = MagicMock()
        self.response.__enter__.return_value = self.response
        self.response.status = 200
        self.response.length = None
        self.response.getheader.return_value = None
        self.connection.getresponse.return_value = self.response
        self.client = SMSGateway('https://apl1.pricetan.cn/test-route')
        self.good = {'success': True, 'smsSent': True, 'msg': 'OK', 'verificationCode': '812903',
                     'expiresInMs': 300000, 'cooldownMs': 60000}

    def send(self, receipt):
        self.response.read1.side_effect = [json.dumps(receipt).encode(), b'']
        return self.send_prepared()

    def send_prepared(self):
        return self.client.send_login_code('+8613900000000', '003671', 'local-challenge', '2026-11-23T12:00:00Z')

    def uncertain(self, reason):
        return {'accepted': False, 'delivery': 'unconfirmed', 'reason': reason}

    def test_render_the_one_workbench_code_and_discard_gateway_code(self):
        self.assertEqual(self.send(self.good), {'accepted': True})
        method, path, raw, headers = self.connection.request.call_args.args
        self.assertEqual((method, path), ('POST', '/test-route/api/sms/requestCode'))
        self.assertEqual(json.loads(raw), {'number': '13900000000', 'template': '【上海蜜田科技】注册绑定手机号验证码是003671'})
        self.assertEqual(headers['Content-Type'], 'application/json; charset=utf-8')

    def test_positive_receipt_can_omit_gateway_code(self):
        self.assertEqual(self.send({'success': True, 'smsSent': True}), {'accepted': True})

    def test_gateway_business_rejection(self):
        for sent in (False, True):
            self.assertEqual(self.send({'success': False, 'smsSent': sent}), {'accepted': False})

    def test_gateway_success_without_delivery_confirmation(self):
        for receipt in (self.good | {'smsSent': False}, {'success': True, 'smsSent': False},
                        {key: value for key, value in self.good.items() if key != 'smsSent'}):
            self.assertEqual(self.send(receipt), self.uncertain('gateway_delivery_unconfirmed'))
            for change in ({'account': {}}, {'verificationCode': '003671'}, {'msg': '短信是003671'}):
                with self.assertRaisesRegex(GatewayUnavailable, '^SMS_GATEWAY_PROTOCOL_ERROR$'):
                    self.send(receipt | change)
        for field in ('verificationCode', 'expiresInMs', 'cooldownMs'):
            partial = {key: value for key, value in self.good.items() if key not in (field, 'smsSent')}
            with self.assertRaisesRegex(GatewayUnavailable, '^SMS_GATEWAY_PROTOCOL_ERROR$'):
                self.send(partial)

    def test_never_accept_ambiguous_receipt_or_workbench_code_echo(self):
        for change in ({'success': 1}, {'smsSent': 1}, {'error': 0}, {'error': True}, {'verificationCode': 3671},
                       {'verificationCode': '003671'}, {'verificationCode': 'xx'}, {'verificationCode': None},
                       {'msg': '【上海蜜田科技】注册绑定手机号验证码是003671'}, {'msg': {'otp': '003671'}},
                       {'otp': '003671'}, {'account': {}}, {'token': 'sensitive'}, {'cooldownMs': '003671'}):
            with self.subTest(keys=list(change)), self.assertRaisesRegex(GatewayUnavailable, '^SMS_GATEWAY_PROTOCOL_ERROR$'):
                self.send(self.good | change)
        for partial in ({'success': True}, {'smsSent': True}, {}):
            with self.assertRaisesRegex(GatewayUnavailable, '^SMS_GATEWAY_PROTOCOL_ERROR$'):
                self.send(partial)

    def test_request_not_sent(self):
        for error, code in ((TimeoutError(), 'SMS_GATEWAY_CONNECT_TIMEOUT'), (ssl.SSLError(), 'SMS_GATEWAY_TLS_ERROR'),
                            (socket.gaierror(), 'SMS_GATEWAY_DNS_ERROR'), (ConnectionResetError(), 'SMS_GATEWAY_UNAVAILABLE')):
            self.connection.request.side_effect = error
            with self.assertRaisesRegex(GatewayUnavailable, '^' + code + '$'):
                self.send(self.good)
        self.connection.getresponse.assert_not_called()

    def test_request_fully_sent_but_no_response(self):
        for error, reason in ((TimeoutError(), 'gateway_response_timeout'),
                              (http.client.RemoteDisconnected(), 'gateway_connection_lost'),
                              (http.client.IncompleteRead(b''), 'gateway_connection_lost')):
            self.connection.getresponse.side_effect = error
            self.assertEqual(self.send(self.good), self.uncertain(reason))
            self.connection.getresponse.side_effect = None
            self.response.read1.side_effect = error
            self.assertEqual(self.send_prepared(), self.uncertain(reason))
        self.assertEqual(self.connection.request.call_count, 6)

    def test_known_send_rejection_stays_failed(self):
        for status, code in ((401, 'SMS_GATEWAY_REJECTED'), (422, 'SMS_GATEWAY_REJECTED'),
                             (429, 'SMS_GATEWAY_RATE_LIMITED'), (307, 'SMS_GATEWAY_UNAVAILABLE'),
                             (500, 'SMS_GATEWAY_UNAVAILABLE')):
            self.response.status = status
            with self.assertRaisesRegex(GatewayUnavailable, '^' + code + '$'):
                self.send(self.good)

    def test_temporary_http_errors_only_allow_recognizable_body(self):
        for status in (502, 503, 504):
            self.response.status = status
            line = f'{status} {http.client.responses[status]}'.encode()
            for raw in (b'', line, b'<html><head><title>' + line + b'</title></head><body><center><h1>' +
                        line + b'</h1></center><hr><center>nginx</center></body></html>'):
                self.response.read1.side_effect = [raw, b'']
                self.assertEqual(self.send_prepared(), self.uncertain('gateway_temporarily_unavailable'))
            self.assertEqual(self.send(self.good), self.uncertain('gateway_temporarily_unavailable'))
            self.assertEqual(self.send({'success': False}), {'accepted': False})
            for raw in (b'{"success":true,"token":"sensitive"}', b'{"success":true,"smsSent":true,"msg":"003671"}',
                        b'{"success":true,"success":false}', b'not JSON', b'<html>other</html>'):
                self.response.read1.side_effect = [raw, b'']
                with self.assertRaisesRegex(GatewayUnavailable, '^SMS_GATEWAY_PROTOCOL_ERROR$'):
                    self.send_prepared()
        self.response.getheader.return_value = 'private'
        with self.assertRaisesRegex(GatewayUnavailable, '^SMS_GATEWAY_PROTOCOL_ERROR$'):
            self.send(self.good)

    def test_partial_or_oversize_body_never_keeps_challenge(self):
        for chunks in ([b'{"token":', TimeoutError()], [http.client.IncompleteRead(b'{"otp":')],
                       [b'{' * (16 * 1024 + 1)]):
            self.response.read1.side_effect = chunks
            with self.assertRaises(GatewayUnavailable):
                self.send_prepared()
        self.response.length = 300
        with self.assertRaisesRegex(GatewayUnavailable, '^SMS_GATEWAY_PROTOCOL_ERROR$'):
            self.send(self.good)
        self.response.read1.side_effect = [b'']
        self.assertEqual(self.send_prepared(), self.uncertain('gateway_connection_lost'))

    def test_reject_bad_number_or_code_or_unapproved_template_without_network(self):
        for number, secret in (('13900000000','003671'), ('+8613900000000','3a3671')):
            with self.assertRaises(ValueError):
                self.client.send_login_code(number, secret, '', '')
        with self.assertRaises(SmsRouteError):
            SMSGateway('https://apl1.pricetan.cn/sample', template='new-template').send_login_code(
                '+8613900000000','003671','','')
        self.connection.request.assert_not_called()


if __name__ == '__main__':
    unittest.main()
