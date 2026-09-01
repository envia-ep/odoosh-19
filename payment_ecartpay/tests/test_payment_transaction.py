# Part of Odoo. See LICENSE file for full copyright and licensing details.

from unittest.mock import patch

from freezegun import freeze_time

from odoo.tests import tagged
from odoo.tools import mute_logger

from odoo.addons.payment_ecartpay.tests.common import EcartPayCommon


@tagged('post_install', '-at_install')
class TestPaymentTransaction(EcartPayCommon):

    @freeze_time('2011-11-02 12:00:21')  # Freeze time for consistent singularization behavior.
    def test_reference_is_singularized(self):
        """ Test that transaction references are unique at the provider level. """
        reference = self.env['payment.transaction']._compute_reference(self.ecartpay.code)
        self.assertEqual(reference, 'tx-20111102120021')

    def test_rendering_values_return_pay_link_and_store_order_id(self):
        """ Test that the order is created and its pay link is split into url and params. """
        tx = self._create_transaction(flow='redirect')
        with patch(
            'odoo.addons.payment.models.payment_provider.PaymentProvider._send_api_request',
            return_value=self.order_data,
        ):
            rendering_values = tx._get_specific_rendering_values(None)
        self.assertEqual(rendering_values, {
            'api_url': 'https://sandbox.ecartpay.com/checkout',
            'url_params': {'id': self.order_id},
        })
        self.assertEqual(tx.provider_reference, self.order_id)

    @mute_logger('odoo.addons.payment.models.payment_transaction')
    def test_no_input_missing_from_redirect_form(self):
        """ Test that the `api_url` key is not omitted from the rendering values. """
        tx = self._create_transaction(flow='redirect')
        with patch(
            'odoo.addons.payment_ecartpay.models.payment_transaction.PaymentTransaction'
            '._get_specific_rendering_values',
            return_value={'api_url': 'https://dummy.com', 'url_params': {'id': 'abc'}}
        ):
            processing_values = tx._get_processing_values()
        form_info = self._extract_values_from_html_form(processing_values['redirect_form_html'])
        self.assertEqual(form_info['action'], 'https://dummy.com')
        self.assertEqual(form_info['method'], 'get')
        self.assertDictEqual(form_info['inputs'], {'id': 'abc'})

    def test_apply_updates_confirms_transaction(self):
        """ Test that the transaction state is set to 'done' on a successful order. """
        tx = self._create_transaction(flow='redirect')
        tx._apply_updates(self.payment_data)
        self.assertEqual(tx.state, 'done')

    @mute_logger('odoo.addons.payment_ecartpay.models.payment_transaction')
    def test_apply_updates_cancels_transaction(self):
        """ Test that the transaction state is set to 'cancel' on a cancelled order. """
        tx = self._create_transaction(flow='redirect')
        tx._apply_updates({'id': self.order_id, 'status': 'cancelled'})
        self.assertEqual(tx.state, 'cancel')

    def test_search_by_reference_matches_on_order_id(self):
        """ Test that transactions are matched by the Ecart Pay order id. """
        tx = self._create_transaction(flow='redirect')
        tx.provider_reference = self.order_id
        found = self.env['payment.transaction']._search_by_reference('ecartpay', self.payment_data)
        self.assertEqual(found, tx)
