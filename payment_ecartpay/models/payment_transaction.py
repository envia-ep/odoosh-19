# Part of Odoo. See LICENSE file for full copyright and licensing details.

from urllib.parse import parse_qsl, urlsplit

from odoo import _, api, models, release
from odoo.exceptions import ValidationError
from odoo.fields import Domain
from odoo.tools import urls

from odoo.addons.payment import utils as payment_utils
from odoo.addons.payment.logging import get_payment_logger
from odoo.addons.payment_ecartpay import const
from odoo.addons.payment_ecartpay.controllers.main import EcartPayController


_logger = get_payment_logger(__name__)


class PaymentTransaction(models.Model):
    _inherit = 'payment.transaction'

    @api.model
    def _compute_reference(self, provider_code, prefix=None, separator='-', **kwargs):
        """ Override of `payment` to singularize references for Ecart Pay.

        Ecart Pay requires the external reference (`reference_id`) to be unique at the provider
        level, so the prefix is singularized with the current datetime.
        """
        if provider_code == 'ecartpay':
            if not prefix:
                prefix = self.sudo()._compute_reference_prefix(separator, **kwargs) or None
            prefix = payment_utils.singularize_reference_prefix(prefix=prefix, separator=separator)
        return super()._compute_reference(
            provider_code, prefix=prefix, separator=separator, **kwargs
        )

    def _get_specific_rendering_values(self, processing_values):
        """ Override of `payment` to create an Ecart Pay order and return its payment link.

        The order is created through the Odoo facade (`POST /api/odoo/orders`) instead of the
        internal Orders API, so this module only knows a stable, business-oriented contract and
        stays decoupled from Ecart Pay's internal order representation.

        Note: self.ensure_one() from `_get_processing_values`

        :param dict processing_values: The generic and specific processing values of the transaction
        :return: The dict of provider-specific rendering values.
        :rtype: dict
        """
        res = super()._get_specific_rendering_values(processing_values)
        if self.provider_code != 'ecartpay':
            return res

        base_url = self.provider_id.get_base_url()
        first_name, last_name = payment_utils.split_partner_name(self.partner_name or '')
        # Sale orders are only linked when the sale/website_sale modules are installed.
        sale_orders = self.sale_order_ids if 'sale_order_ids' in self._fields else self.env['sale.order'].browse()
        # Only send customer fields that actually have a value (POS orders may have no contact
        # details). Ecart Pay requires at least an email or a phone, so fall back to the company
        # email when neither is available.
        customer = {'first_name': first_name or self.partner_name or 'Customer'}
        if last_name:
            customer['last_name'] = last_name
        if self.partner_email:
            customer['email'] = self.partner_email
        if self.partner_phone:
            customer['phone'] = self.partner_phone
        if not customer.get('email') and not customer.get('phone') and self.company_id.email:
            customer['email'] = self.company_id.email
        lines = self._ecartpay_build_order_lines()
        line_item_names = [item['name'] for item in lines['items']]
        payload = {
            # `reference` is the singularized, unique Odoo order reference; it is used as the
            # Ecart Pay `reference_id` (which must be unique per attempt at the provider level).
            'reference': self.reference,
            'reference_id': self.reference,
            'amount': self.amount,
            'currency': self.currency_id.name,
            'customer': customer,
            'items': lines['items'],
            'urls': {
                'notify_url': urls.urljoin(base_url, EcartPayController._webhook_url),
                'redirect_url': urls.urljoin(base_url, EcartPayController._return_url),
            },
            'ecommerce': {
                'name': 'Odoo',
                'website': base_url,
                'url': base_url,
                'shop_id': str(self.company_id.id),
                'shop_name': self.company_id.name,
                'reference': self.reference,
                'transaction_id': self.id,
                'order_ids': sale_orders.ids,
                'order_references': sale_orders.mapped('name'),
                'partner_id': self.partner_id.id,
                'partner_name': self.partner_name,
                'line_item_names': line_item_names,
                'versions': {'odoo': release.version},
            },
        }
        if lines.get('shipping_items'):
            payload['shipping_items'] = lines['shipping_items']
        if lines.get('discounts'):
            payload['discounts'] = lines['discounts']
        try:
            order_data = self._send_api_request('POST', 'odoo/orders', json=payload)
        except ValidationError as error:
            self._set_error(str(error))
            return {}

        self.provider_reference = order_data['id']
        parsed_link = urlsplit(order_data['pay_link'])
        return {
            'api_url': f'{parsed_link.scheme}://{parsed_link.netloc}{parsed_link.path}',
            'url_params': dict(parse_qsl(parsed_link.query)),
        }

    def _ecartpay_build_order_lines(self):
        """ Split the linked document into items, shipping_items and discounts.

        Uses the linked sale order lines (website/eCommerce) or POS order lines (POS online
        payment) with tax-included amounts. Delivery lines (`is_delivery`) are surfaced in
        `shipping_items` with the sale order carrier as `carrier`, and reward/negative lines
        (`is_reward_line` or a negative total) become `discounts`, so Ecart Pay stores each bucket
        under its dedicated field.

        The facade reconciles `items + shipping - discounts` against the transaction amount and
        falls back to a single aggregated line if they drift, so a mismatch never blocks the
        order. Lines with a fractional quantity are not itemized (Ecart Pay expects integer
        quantities); in that case a single aggregated line by the transaction amount is returned.

        :return: A dict with keys `items` (required, non-empty) and optionally `shipping_items`
                 and `discounts`.
        :rtype: dict
        """
        self.ensure_one()
        aggregated = {'items': [{'name': self.reference, 'price': self.amount, 'quantity': 1}]}

        def _split(lines, name_of, qty_of, total_of, is_shipping_of, is_discount_of, carrier_name):
            items, shipping_items, discounts = [], [], []
            for line in lines:
                if getattr(line, 'display_type', False):
                    continue  # Skip section/note lines.
                qty = qty_of(line)
                if not qty or float(qty) != int(qty):
                    return None  # Fractional quantity: let the facade aggregate.
                qty = int(qty)
                total = round(float(total_of(line)), 2)
                name = name_of(line) or self.reference
                if is_shipping_of(line):
                    shipping_items.append({
                        'name': name,
                        'amount': round(abs(total), 2),
                        'carrier': carrier_name or name,
                    })
                elif is_discount_of(line) or total < 0:
                    discounts.append({'name': name, 'amount': round(abs(total), 2)})
                else:
                    items.append({
                        'name': name,
                        'quantity': qty,
                        'price': round(total / qty, 2) if qty else total,
                        'total': total,
                    })
            return {'items': items, 'shipping_items': shipping_items, 'discounts': discounts}

        # Sale orders (website_sale / sale).
        sale_orders = self.sale_order_ids if 'sale_order_ids' in self._fields else self.env['sale.order'].browse()
        if sale_orders:
            carrier_names = sale_orders.mapped('carrier_id.name') if 'carrier_id' in sale_orders._fields else []
            result = _split(
                sale_orders.order_line,
                lambda l: l.name or l.product_id.display_name,
                lambda l: l.product_uom_qty,
                lambda l: l.price_total,
                lambda l: bool(getattr(l, 'is_delivery', False)),
                lambda l: bool(getattr(l, 'is_reward_line', False)),
                carrier_names[0] if carrier_names else '',
            )
            if result and result['items']:
                return result
            if result is None:
                return aggregated

        # POS online payment order lines (no shipping in POS).
        if 'pos_order_id' in self._fields and self.pos_order_id:
            result = _split(
                self.pos_order_id.lines,
                lambda l: l.full_product_name or l.product_id.display_name,
                lambda l: l.qty,
                lambda l: l.price_subtotal_incl,
                lambda _l: False,
                lambda l: bool(getattr(l, 'is_reward_line', False)),
                '',
            )
            if result and result['items']:
                return result

        return aggregated

    @api.model
    def _search_by_reference(self, provider_code, payment_data):
        """ Override of `payment` to find the transaction by the Ecart Pay order id.

        Ecart Pay webhooks only carry the order `id` (stored as `provider_reference`), not the
        Odoo reference, so the lookup is done on `provider_reference`.
        """
        if provider_code != 'ecartpay':
            return super()._search_by_reference(provider_code, payment_data)

        order_id = payment_data.get('id')
        if not order_id:
            _logger.warning("Received Ecart Pay payment data with missing order id.")
            return self
        tx = self.search(
            Domain('provider_reference', '=', order_id)
            & Domain('provider_code', '=', 'ecartpay')
        )
        if not tx:
            _logger.warning("No transaction found matching Ecart Pay order id %s.", order_id)
        return tx

    def _extract_amount_data(self, payment_data):
        """ Override of `payment` to skip amount validation for Ecart Pay.

        Ecart Pay webhooks only carry the order id and status, not the amount, so the check is
        skipped (the amount was fixed when the order was created).
        """
        if self.provider_code != 'ecartpay':
            return super()._extract_amount_data(payment_data)
        return None

    def _apply_updates(self, payment_data):
        """ Override of `payment` to update the transaction based on the payment data. """
        if self.provider_code != 'ecartpay':
            return super()._apply_updates(payment_data)

        payment_status = (payment_data.get('status') or '').lower()
        if payment_status in const.PAYMENT_STATUS_MAPPING['pending']:
            self._set_pending()
        elif payment_status in const.PAYMENT_STATUS_MAPPING['done']:
            self._set_done()
        elif payment_status in const.PAYMENT_STATUS_MAPPING['cancel']:
            self._set_canceled()
        elif payment_status in const.PAYMENT_STATUS_MAPPING['error']:
            self._set_error(_(
                "An error occurred during the processing of your payment (status %s). Please try "
                "again.", payment_status
            ))
        else:
            _logger.warning(
                "Received data with invalid payment status (%s) for transaction %s.",
                payment_status, self.reference
            )
            self._set_error(_("Unknown payment status: %s", payment_status))
