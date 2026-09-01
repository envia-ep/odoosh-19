from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged("post_install", "-at_install")
class TestEnviaQuoteOnboardingWizard(TransactionCase):
    def setUp(self):
        super().setUp()
        self.env.company.envia_enable_branches = True

    def test_app_entry_shows_settings_when_configured(self):
        self.env.company.write(
            {
                "envia_api_token": "envia-shipping-token-test",
                "envia_quote_onboarding_pending": True,
            }
        )
        action = self.env["envia.plugin.connect.wizard"].action_envia_app_entry()
        self.assertEqual(action["res_model"], "res.config.settings")
        self.assertFalse(self.env.company.envia_quote_onboarding_pending)

    def test_app_entry_opens_settings_when_onboarding_completed(self):
        self.env.company.write(
            {
                "envia_api_token": "envia-shipping-token-test",
                "envia_quote_onboarding_pending": False,
            }
        )
        action = self.env["envia.plugin.connect.wizard"].action_envia_app_entry()
        self.assertEqual(action["res_model"], "res.config.settings")

    def test_dismiss_on_go_to_quotes(self):
        wizard = self.env["envia.quote.onboarding.wizard"].create(
            {"company_id": self.env.company.id}
        )
        action = wizard.action_go_to_quotes()
        self.assertFalse(self.env.company.envia_quote_onboarding_pending)
        self.assertEqual(action["res_model"], "res.config.settings")

    def test_standalone_quote_wizard_opens_without_sale_order(self):
        action = self.env["envia.quote.wizard"].action_open_quote_wizard()
        self.assertEqual(action["res_model"], "envia.quote.wizard")
        self.assertEqual(action["target"], "current")
        wizard = self.env["envia.quote.wizard"].create(
            self.env["envia.quote.wizard"].default_get([])
        )
        self.assertFalse(wizard.sale_order_id)
        self.assertFalse(wizard.picking_id)
        self.assertTrue(wizard.is_standalone)

    def test_first_quote_dismisses_onboarding(self):
        self.env.company.write({"envia_quote_onboarding_pending": True})
        self.env["envia.quote"].create(
            {
                "origin_postal_code": "67192",
                "origin_country": "MX",
                "destination_postal_code": "03100",
                "destination_country": "MX",
                "weight": 1.0,
                "content": "Test package",
            }
        )
        self.assertFalse(self.env.company.envia_quote_onboarding_pending)

    def test_get_branch_carrier_codes_includes_mx_carriers(self):
        mexico = self.env.ref("base.mx")
        wizard = self.env["envia.quote.wizard"].create({})
        codes = wizard._get_branch_carrier_codes(mexico)
        self.assertIn("dhl", codes)
        self.assertIn("paquetexpress", codes)

    def test_get_quote_carriers_returns_all_for_address_route(self):
        self.env.company.envia_default_carriers = "dhl,fedex,estafeta"
        wizard = self.env["envia.quote.wizard"].create({})
        self.assertEqual(wizard._get_quote_carriers(), "all")

    def test_get_quote_carriers_returns_branch_carrier(self):
        wizard = self.env["envia.quote.wizard"].create(
            {
                "origin_location_type": "address",
                "destination_location_type": "branch",
            }
        )
        self.env["envia.quote.wizard.branch"].create(
            {
                "wizard_id": wizard.id,
                "side": "destination",
                "name": "Branch Estafeta",
                "carrier": "estafeta",
                "is_selected": True,
            }
        )
        # Branch-first: the selected pickup branch fixes the carrier to quote.
        self.assertEqual(wizard._get_quote_carriers(), "estafeta")

    def test_get_quote_carriers_ignores_selected_rate_without_branch(self):
        """Re-quote after picking a rate must still request all carriers."""
        wizard = self.env["envia.quote.wizard"].create(
            {
                "origin_location_type": "address",
                "destination_location_type": "address",
            }
        )
        self.env["envia.quote.wizard.service"].create(
            {
                "wizard_id": wizard.id,
                "service_id": "fedex:1",
                "carrier": "fedex",
                "carrier_name": "FedEx",
                "service_name": "Economy",
                "price": 100.0,
                "is_selected": True,
            }
        )
        self.assertEqual(wizard._pickup_carrier_code(), "fedex")
        self.assertEqual(wizard._get_quote_carriers(), "all")

    def test_get_quote_carriers_ignores_stale_branch_on_address_route(self):
        """Update shipping can leave a selected branch line after switching to Ship."""
        wizard = self.env["envia.quote.wizard"].create(
            {
                "origin_location_type": "address",
                "destination_location_type": "address",
            }
        )
        self.env["envia.quote.wizard.branch"].create(
            {
                "wizard_id": wizard.id,
                "side": "destination",
                "name": "Stale Estafeta",
                "carrier": "estafeta",
                "is_selected": True,
            }
        )
        self.assertEqual(wizard._pickup_carrier_code(), "estafeta")
        self.assertEqual(wizard._get_quote_carriers(), "all")
