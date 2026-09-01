# Part of Odoo. See LICENSE file for full copyright and licensing details.

from odoo.fields import Command

from . import controllers
from . import models

from odoo.addons.payment import setup_provider, reset_payment_provider


def post_init_hook(env):
    setup_provider(env, 'ecartpay')
    # Link the default (card) payment method to every Ecart Pay provider so it can be offered at
    # checkout. Core providers are seeded with this by the `payment` module; custom providers must
    # set it themselves.
    card = env.ref('payment.payment_method_card', raise_if_not_found=False)
    if card:
        providers = env['payment.provider'].search([('code', '=', 'ecartpay')])
        for provider in providers:
            provider.payment_method_ids = [Command.link(card.id)]


def uninstall_hook(env):
    reset_payment_provider(env, 'ecartpay')
