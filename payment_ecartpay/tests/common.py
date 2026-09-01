# Part of Odoo. See LICENSE file for full copyright and licensing details.

from odoo.addons.payment.tests.common import PaymentCommon


class EcartPayCommon(PaymentCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        cls.ecartpay = cls._prepare_provider('ecartpay', update_values={
            'ecartpay_public_key': 'pub_test_key',
            'ecartpay_private_key': 'priv_test_key',
            'ecartpay_webhook_secret': 'whsec_test',
        })

        cls.provider = cls.ecartpay

        # The Ecart Pay order id, stored as `provider_reference` and used to match webhooks.
        cls.order_id = 'ecp_order_123456789'

        # Data created when an order is created (see `_get_specific_rendering_values`).
        cls.order_data = {
            'id': cls.order_id,
            'status': 'created',
            'pay_link': f'https://sandbox.ecartpay.com/checkout?id={cls.order_id}',
        }
        # Data carried by an order status webhook (see `_apply_updates`).
        cls.payment_data = {
            'id': cls.order_id,
            'status': 'paid',
        }
        cls.webhook_payload = {
            'event': 'orders.confirmation',
            'data': cls.payment_data,
        }
