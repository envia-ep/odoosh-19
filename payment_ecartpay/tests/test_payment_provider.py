# Part of Odoo. See LICENSE file for full copyright and licensing details.

from unittest.mock import patch

from odoo.tests import tagged

from odoo.addons.payment_ecartpay.tests.common import EcartPayCommon


@tagged('post_install', '-at_install')
class TestPaymentProvider(EcartPayCommon):

    def test_incompatible_with_unsupported_currencies(self):
        compatible_providers = self.env['payment.provider']._get_compatible_providers(
            self.company_id, self.partner.id, self.amount, currency_id=self.env.ref('base.AFN').id
        )
        self.assertNotIn(self.ecartpay, compatible_providers)

    def test_incompatible_with_validation_transactions(self):
        compatible_providers = self.env['payment.provider']._get_compatible_providers(
            self.company_id, self.partner.id, 0., is_validation=True
        )
        self.assertNotIn(self.ecartpay, compatible_providers)

    def test_build_request_url_uses_sandbox_when_not_enabled(self):
        self.ecartpay.state = 'test'
        url = self.ecartpay._build_request_url('orders')
        self.assertEqual(url, 'https://sandbox.ecartpay.com/api/orders')

    def test_build_request_url_uses_production_when_enabled(self):
        self.ecartpay.state = 'enabled'
        url = self.ecartpay._build_request_url('orders')
        self.assertEqual(url, 'https://ecartpay.com/api/orders')

    def test_bearer_token_is_cached(self):
        """ Test that the bearer token is fetched once and reused until it expires. """
        with patch(
            'odoo.addons.payment.models.payment_provider.PaymentProvider._send_api_request',
            return_value={'token': 'a_token'},
        ) as request_mock:
            first = self.ecartpay._ecartpay_get_bearer_token()
            second = self.ecartpay._ecartpay_get_bearer_token()
        self.assertEqual(first, 'a_token')
        self.assertEqual(second, 'a_token')
        self.assertEqual(request_mock.call_count, 1)

    def test_token_request_uses_basic_auth(self):
        """ Test that the token exchange authenticates with the public/private keys. """
        auth = self.ecartpay._build_request_auth(ecartpay_token_request=True)
        self.assertEqual(auth, ('pub_test_key', 'priv_test_key'))

    def test_regular_request_uses_bearer_header(self):
        """ Test that regular requests send the bearer token in the Authorization header. """
        with patch(
            'odoo.addons.payment_ecartpay.models.payment_provider.PaymentProvider'
            '._ecartpay_get_bearer_token', return_value='a_token'
        ):
            headers = self.ecartpay._build_request_headers('POST', 'orders', {})
        self.assertEqual(headers['Authorization'], 'a_token')
