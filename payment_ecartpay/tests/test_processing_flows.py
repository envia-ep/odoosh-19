# Part of Odoo. See LICENSE file for full copyright and licensing details.

import hashlib
import hmac
import json
from types import SimpleNamespace
from unittest.mock import patch

from werkzeug.exceptions import Forbidden

from odoo.tests import tagged
from odoo.tools import mute_logger

from odoo.addons.payment.tests.http_common import PaymentHttpCommon
from odoo.addons.payment_ecartpay.controllers.main import EcartPayController
from odoo.addons.payment_ecartpay.tests.common import EcartPayCommon


@tagged('post_install', '-at_install')
class TestProcessingFlows(EcartPayCommon, PaymentHttpCommon):

    def _build_signature_headers(self, data, secret=None, timestamp='1642234567890',
                                 webhook_id='hook_1'):
        """ Build valid webhook signature headers for the given data. """
        secret = secret if secret is not None else self.provider.ecartpay_webhook_secret
        signed_payload = f'{timestamp}.{webhook_id}.{json.dumps(data, separators=(",", ":"))}'
        signature = hmac.new(
            secret.encode(), signed_payload.encode(), hashlib.sha256
        ).hexdigest()
        return {
            'x-pay-timestamp': timestamp,
            'x-pay-webhook-id': webhook_id,
            'x-pay-signature': f'SHA256={signature}',
        }

    @mute_logger('odoo.addons.payment_ecartpay.controllers.main')
    def test_webhook_notification_triggers_processing(self):
        """ Test that a valid webhook notification triggers the processing of the payment data. """
        tx = self._create_transaction('redirect')
        tx.provider_reference = self.order_id
        url = self._build_url(EcartPayController._webhook_url)
        with patch(
            'odoo.addons.payment_ecartpay.controllers.main.EcartPayController._verify_signature'
        ), patch(
            'odoo.addons.payment.models.payment_transaction.PaymentTransaction._process'
        ) as process_mock:
            self._make_json_request(url, data=self.webhook_payload)
        self.assertEqual(process_mock.call_count, 1)

    @mute_logger('odoo.addons.payment_ecartpay.controllers.main')
    def test_webhook_notification_triggers_signature_check(self):
        """ Test that receiving a webhook notification triggers a signature check. """
        tx = self._create_transaction('redirect')
        tx.provider_reference = self.order_id
        url = self._build_url(EcartPayController._webhook_url)
        with patch(
            'odoo.addons.payment_ecartpay.controllers.main.EcartPayController._verify_signature'
        ) as signature_check_mock, patch(
            'odoo.addons.payment.models.payment_transaction.PaymentTransaction._process'
        ):
            self._make_json_request(url, data=self.webhook_payload)
            self.assertEqual(signature_check_mock.call_count, 1)

    def test_accept_webhook_notification_with_valid_signature(self):
        """ Test the verification of a webhook notification with a valid signature. """
        tx = self._create_transaction('redirect')
        headers = self._build_signature_headers(self.payment_data)
        fake_request = SimpleNamespace(headers=headers)
        self._assert_does_not_raise(
            Forbidden,
            EcartPayController._verify_signature,
            fake_request,
            self.payment_data,
            tx,
        )

    @mute_logger('odoo.addons.payment_ecartpay.controllers.main')
    def test_reject_notification_with_missing_signature(self):
        """ Test the verification of a notification with missing signature headers. """
        tx = self._create_transaction('redirect')
        fake_request = SimpleNamespace(headers={})
        self.assertRaises(
            Forbidden, EcartPayController._verify_signature, fake_request, self.payment_data, tx
        )

    @mute_logger('odoo.addons.payment_ecartpay.controllers.main')
    def test_reject_notification_with_invalid_signature(self):
        """ Test the verification of a notification with an invalid signature. """
        tx = self._create_transaction('redirect')
        headers = self._build_signature_headers(self.payment_data, secret='wrong_secret')
        fake_request = SimpleNamespace(headers=headers)
        self.assertRaises(
            Forbidden, EcartPayController._verify_signature, fake_request, self.payment_data, tx
        )
