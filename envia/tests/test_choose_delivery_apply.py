from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged("post_install", "-at_install")
class TestChooseDeliveryApply(TransactionCase):
    def _create_order_quote_wizard(self, *, price):
        partner = self.env.company.partner_id
        product = self.env["product.product"].search([("sale_ok", "=", True)], limit=1)
        order = self.env["sale.order"].create(
            {
                "partner_id": partner.id,
                "partner_invoice_id": partner.id,
                "partner_shipping_id": partner.id,
                "order_line": [(0, 0, {"product_id": product.id, "product_uom_qty": 1.0})],
            }
        )
        quote = self.env["envia.quote"].create(
            {
                "sale_order_id": order.id,
                "origin_postal_code": "67192",
                "origin_country": "MX",
                "destination_postal_code": "03100",
                "destination_country": "MX",
                "weight": 1.0,
                "content": "Test",
                "state": "quoted",
            }
        )
        service = self.env["envia.quote.service"].create(
            {
                "quote_id": quote.id,
                "service_id": "estafeta:ground",
                "carrier": "estafeta",
                "service_name": "Estafeta Terrestre",
                "price": price,
                "is_selected": True,
            }
        )
        quote.selected_service_id = service.id
        wizard = self.env["envia.quote.wizard"].create(
            {"sale_order_id": order.id, "quote_id": quote.id}
        )
        self.env["envia.quote.wizard.service"].create(
            {
                "wizard_id": wizard.id,
                "service_id": "estafeta:ground",
                "carrier": "estafeta",
                "service_name": "Estafeta Terrestre",
                "price": price,
                "is_selected": True,
            }
        )
        return order, wizard

    def test_apply_selected_envia_quote_applies_delivery_line(self):
        order, wizard = self._create_order_quote_wizard(price=123.45)
        with patch(
            "odoo.addons.envia.wizards.envia_quote_wizard.EnviaQuoteWizard._perform_get_quote",
            return_value=False,
        ):
            wizard.action_apply_shipping_to_order()
        shipping_line = order.order_line.filtered("is_delivery")
        self.assertEqual(len(shipping_line), 1)
        self.assertEqual(shipping_line.price_unit, 123.45)

    def test_apply_does_not_requote_when_service_already_selected(self):
        order, wizard = self._create_order_quote_wizard(price=99.0)
        with patch(
            "odoo.addons.envia.wizards.envia_quote_wizard.EnviaQuoteWizard._perform_get_quote",
            side_effect=AssertionError("must not re-quote"),
        ):
            wizard.action_apply_shipping_to_order()
        shipping_line = order.order_line.filtered("is_delivery")
        self.assertEqual(len(shipping_line), 1)
        self.assertEqual(shipping_line.price_unit, 99.0)

    def test_generate_label_applies_shipping_cost(self):
        """Confirm applies shipping, then asks to open the delivery for the label."""
        order, wizard = self._create_order_quote_wizard(price=77.0)
        with patch(
            "odoo.addons.envia.wizards.envia_quote_wizard.EnviaQuoteWizard._perform_get_quote",
            side_effect=AssertionError("must not re-quote"),
        ):
            wizard.action_apply_shipping_to_order()
        shipping_line = order.order_line.filtered("is_delivery")
        self.assertEqual(len(shipping_line), 1)
        self.assertEqual(shipping_line.price_unit, 77.0)
        with patch.object(
            type(wizard), "_finalize_quote_selection"
        ), patch.object(type(wizard), "_apply_shipping_cost_to_order"):
            with self.assertRaises(UserError) as error:
                wizard.action_confirm_selection()
        self.assertIn("Generate Envia Label", str(error.exception))


    def test_apply_shipping_on_confirmed_sale_order(self):
        partner = self.env.company.partner_id
        product = self.env["product.product"].create(
            {
                "name": "Confirmed Ship Merchandise",
                "sale_ok": True,
                "type": "service",
                "list_price": 10.0,
            }
        )
        order = self.env["sale.order"].create(
            {
                "partner_id": partner.id,
                "partner_invoice_id": partner.id,
                "partner_shipping_id": partner.id,
                "order_line": [(0, 0, {"product_id": product.id, "product_uom_qty": 1.0})],
            }
        )
        order.action_confirm()
        self.assertEqual(order.state, "sale")
        carrier = self.env.ref("envia.delivery_carrier_envia")
        wizard = self.env["choose.delivery.carrier"].create(
            {
                "order_id": order.id,
                "carrier_id": carrier.id,
                "delivery_price": 88.0,
            }
        )
        quote_wizard = wizard.envia_wizard_id
        self.env["envia.quote.wizard.service"].create(
            {
                "wizard_id": quote_wizard.id,
                "service_id": "estafeta:ground",
                "carrier": "estafeta",
                "carrier_name": "Estafeta",
                "service_name": "Estafeta Terrestre",
                "price": 88.0,
                "currency_name": order.currency_id.name,
                "is_selected": True,
            }
        )
        with patch.object(type(quote_wizard), "_finalize_quote_selection"), patch.object(
            type(wizard), "_sync_delivery_price_from_envia"
        ):
            wizard.button_confirm()
        shipping_line = order.order_line.filtered("is_delivery")
        self.assertEqual(len(shipping_line), 1)
        self.assertEqual(order.carrier_id, carrier)
        self.assertAlmostEqual(shipping_line.price_unit, 88.0)
