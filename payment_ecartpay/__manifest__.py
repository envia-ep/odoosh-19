{
    'name': "Payment Provider: Ecart Pay",
    'version': '19.0.1.0.0',
    'category': 'Accounting/Payment Providers',
    'sequence': 350,
    'summary': "Accept payments through Ecart Pay, a Latin American payment gateway.",
    'description': """
Ecart Pay Payment Provider
==========================

Integrate the Ecart Pay hosted checkout (redirect) with your Odoo website / eCommerce and Point of
Sale online payments. Order creation, updates and refunds go through the Ecart Pay Odoo facade
(`/api/odoo/*`), which keeps this module decoupled from Ecart Pay's internal Orders API. Order
status updates are received through a signed webhook.

Features:
 * Hosted redirect checkout for website, eCommerce and POS online payments.
 * Automatic customer, items, shipping and discount forwarding.
 * Signed webhook (HMAC-SHA256) for asynchronous status updates.
 * Automatic provisioning of the webhook secret and the accounting payment method line.
 * Ready for future flows: transparent checkout, payment intents, tokenization, recurring, wallets.
""",
    'website': 'https://ecartpay.com/',
    'depends': ['payment'],
    'data': [
        'views/payment_ecartpay_templates.xml',
        'views/payment_provider_views.xml',
        'data/payment_provider_data.xml',
    ],
    'assets': {},
    'images': ['static/description/icon.png'],
    'post_init_hook': 'post_init_hook',
    'uninstall_hook': 'uninstall_hook',
    'installable': True,
    'application': False,
    'author': "Ecart Pay",
    'license': 'LGPL-3',
}
