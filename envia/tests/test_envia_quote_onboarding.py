from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged("post_install", "-at_install")
class TestEnviaQuoteOnboarding(TransactionCase):
    def test_get_quotes_onboarding_data_returns_steps(self):
        onboarding = self.env.ref("envia.onboarding_onboarding_envia_quotes")
        onboarding.with_company(self.env.company)._search_or_create_progress()
        data = self.env["envia.quote"].get_quotes_onboarding_data()
        self.assertTrue(data)
        self.assertEqual(len(data["steps"]), 4)
        self.assertEqual(data["steps"][0]["action"], "action_open_step_envia_connect")

    def test_onboarding_step_actions_return_window_actions(self):
        action = self.env["onboarding.onboarding.step"].action_open_step_envia_connect()
        self.assertEqual(action.get("type"), "ir.actions.act_window")

        action = self.env["onboarding.onboarding.step"].action_open_step_envia_sale_order()
        self.assertEqual(action.get("res_model"), "sale.order")

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
        order.action_confirm()

        action = self.env["onboarding.onboarding.step"].action_open_step_envia_get_rates()
        self.assertEqual(action.get("res_model"), "choose.delivery.carrier")
        self.assertEqual(action.get("target"), "new")
        carrier = self.env.ref("envia.delivery_carrier_envia")
        self.assertEqual(action["context"]["default_carrier_id"], carrier.id)
        self.assertTrue(action["context"]["envia_force_delivery_carrier"])
