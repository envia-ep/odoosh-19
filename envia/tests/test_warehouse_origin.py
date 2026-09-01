from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase

from odoo.addons.envia.services.envia_client import EnviaClient
from unittest.mock import MagicMock


@tagged("post_install", "-at_install")
class TestEnviaWarehouseOrigin(TransactionCase):
    def test_normalize_shop_addresses_from_data_list(self):
        body = {
            "data": [
                {
                    "address_id": 99,
                    "name": "WH Origin",
                    "street": "Calle 1",
                    "number": "10",
                    "city": "Monterrey",
                    "postal_code": "64000",
                    "country": "MX",
                    "state": "NL",
                }
            ]
        }
        addresses = EnviaClient.normalize_shop_addresses(body)
        self.assertEqual(len(addresses), 1)
        self.assertEqual(addresses[0]["id"], "99")
        self.assertIn("Calle 1 10", addresses[0]["label"])
        self.assertEqual(addresses[0]["country_code"], "MX")

    @patch("odoo.addons.envia.services.envia_client.requests.get")
    def test_get_shop_default_addresses_uses_shop_base_and_token(self, mock_get):
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "data": [
                    {
                        "address_id": "55",
                        "name": "Shop Origin",
                        "street": "Av Reforma 100",
                        "city": "CDMX",
                        "zip": "06600",
                    }
                ]
            },
            text="{}",
        )
        base = "https://queries.test.envia.com/"
        client = EnviaClient(base, "shipping-token")
        addresses = client.get_shop_default_addresses("34107")
        self.assertEqual(addresses[0]["id"], "55")
        self.assertEqual(
            mock_get.call_args.args[0],
            f"{base.rstrip('/')}/shop-default-address/34107",
        )
        self.assertIn(
            "Bearer shipping-token",
            mock_get.call_args.kwargs["headers"]["Authorization"],
        )

    @patch(
        "odoo.addons.envia.wizards.envia_warehouse_origin_wizard"
        ".EnviaWarehouseOriginWizard._fetch_origin_addresses"
    )
    def test_wizard_save_creates_match_and_updates_partner(self, mock_fetch):
        company = self.env.company
        company.write(
            {
                "envia_shop_id": "34107",
                "envia_api_token": "shipping-token",
            }
        )
        mock_fetch.return_value = [
            {
                "id": "55",
                "label": "Shop Origin · Av Reforma 100 · CDMX · 06600",
                "name": "Shop Origin",
                "street": "Av Reforma 100",
                "city": "CDMX",
                "zip": "06600",
                "phone": "5555555555",
                "email": "origin@example.com",
                "country_code": "MX",
                "state_code": "DF",
            }
        ]
        warehouse = self.env["stock.warehouse"].search(
            [("company_id", "=", company.id)],
            limit=1,
        )
        self.assertTrue(warehouse)
        action = self.env["envia.warehouse.origin.wizard"].action_open_wizard()
        wizard = self.env["envia.warehouse.origin.wizard"].browse(action["res_id"])
        self.assertEqual(len(wizard.address_line_ids), 1)
        wizard.write(
            {
                "warehouse_id": warehouse.id,
                "address_line_id": wizard.address_line_ids[:1].id,
                "update_warehouse_partner": True,
            }
        )
        wizard.action_save()
        match = self.env["envia.warehouse.origin"].search(
            [("warehouse_id", "=", warehouse.id)],
            limit=1,
        )
        self.assertTrue(match)
        self.assertEqual(match.envia_address_id, "55")
        self.assertEqual(warehouse.partner_id.street, "Av Reforma 100")
        self.assertEqual(warehouse.partner_id.zip, "06600")

    @patch(
        "odoo.addons.envia.wizards.envia_warehouse_origin_wizard"
        ".EnviaWarehouseOriginWizard._fetch_origin_addresses"
    )
    def test_wizard_save_can_skip_partner_update(self, mock_fetch):
        company = self.env.company
        company.write(
            {
                "envia_shop_id": "34107",
                "envia_api_token": "shipping-token",
            }
        )
        mock_fetch.return_value = [
            {
                "id": "88",
                "label": "Origin B",
                "name": "Origin B",
                "street": "Other Street 9",
                "city": "Guadalupe",
                "zip": "67192",
                "phone": "",
                "email": "",
                "country_code": "MX",
                "state_code": "NL",
            }
        ]
        warehouse = self.env["stock.warehouse"].search(
            [("company_id", "=", company.id)],
            limit=1,
        )
        original_street = warehouse.partner_id.street
        action = self.env["envia.warehouse.origin.wizard"].action_open_wizard()
        wizard = self.env["envia.warehouse.origin.wizard"].browse(action["res_id"])
        wizard.write(
            {
                "warehouse_id": warehouse.id,
                "address_line_id": wizard.address_line_ids[:1].id,
                "update_warehouse_partner": False,
            }
        )
        wizard.action_save()
        match = self.env["envia.warehouse.origin"].search(
            [("warehouse_id", "=", warehouse.id)],
            limit=1,
        )
        self.assertEqual(match.envia_address_id, "88")
        self.assertEqual(warehouse.partner_id.street, original_street)

    def test_wizard_fetch_requires_shop_and_token(self):
        company = self.env.company
        company.write({"envia_shop_id": False, "envia_api_token": False})
        with self.assertRaises(UserError):
            self.env["envia.warehouse.origin.wizard"]._fetch_origin_addresses(company)

    @patch(
        "odoo.addons.envia.wizards.envia_warehouse_origin_wizard"
        ".EnviaWarehouseOriginWizard._fetch_origin_addresses",
        return_value=[],
    )
    def test_warehouse_form_opens_wizard_prefilled(self, _mock_fetch):
        warehouse = self.env["stock.warehouse"].search(
            [("company_id", "=", self.env.company.id)],
            limit=1,
        )
        self.assertTrue(warehouse)
        action = warehouse.action_envia_link_origin()
        wizard = self.env["envia.warehouse.origin.wizard"].browse(action["res_id"])
        self.assertEqual(wizard.warehouse_id, warehouse)
        self.assertTrue(wizard.warehouse_readonly)

    @patch(
        "odoo.addons.envia.wizards.envia_warehouse_origin_wizard"
        ".EnviaWarehouseOriginWizard._fetch_origin_addresses",
        return_value=[],
    )
    def test_match_edit_opens_wizard_for_warehouse(self, _mock_fetch):
        company = self.env.company
        warehouse = self.env["stock.warehouse"].search(
            [("company_id", "=", company.id)],
            limit=1,
        )
        match = self.env["envia.warehouse.origin"].upsert_match(
            company,
            warehouse,
            {"id": "55", "label": "Origin A"},
            update_partner=False,
        )
        action = match.action_edit_origin()
        wizard = self.env["envia.warehouse.origin.wizard"].browse(action["res_id"])
        self.assertEqual(wizard.warehouse_id, warehouse)
        self.assertTrue(wizard.warehouse_readonly)

    def test_create_origin_action_passes_warehouse_id(self):
        warehouse = self.env["stock.warehouse"].search(
            [("company_id", "=", self.env.company.id)],
            limit=1,
        )
        self.assertTrue(warehouse)
        action = warehouse.action_envia_create_origin()
        self.assertEqual(action["params"].get("warehouse_id"), warehouse.id)

    def test_quote_origin_uses_linked_address_id(self):
        company = self.env.company
        warehouse = self.env["stock.warehouse"].search(
            [("company_id", "=", company.id)],
            limit=1,
        )
        self.assertTrue(warehouse)
        partner = warehouse.partner_id
        partner.write(
            {
                "street": partner.street or "Calle 1",
                "city": partner.city or "Monterrey",
                "zip": partner.zip or "64000",
                "country_id": partner.country_id.id or self.env.ref("base.mx").id,
                "phone": partner.phone or "8181234567",
                "email": partner.email or "wh@example.com",
            }
        )
        self.env["envia.warehouse.origin"].upsert_match(
            company,
            warehouse,
            {
                "id": "7295564",
                "label": "WH Origin",
                "street": partner.street,
                "city": partner.city,
                "zip": partner.zip,
                "country_code": partner.country_id.code,
            },
            update_partner=False,
        )
        wizard = self.env["envia.quote.wizard"].create(
            {
                "origin_warehouse_id": warehouse.id,
                "origin_partner_id": partner.id,
                "origin_street": partner.street,
                "origin_city": partner.city,
                "origin_postal_code": partner.zip,
                "origin_country_id": partner.country_id.id,
                "origin_state_id": partner.state_id.id,
                "destination_partner_id": partner.id,
                "destination_street": partner.street,
                "destination_city": partner.city,
                "destination_postal_code": partner.zip,
                "destination_country_id": partner.country_id.id,
                "weight": 1.0,
                "content": "Test",
            }
        )
        self.assertFalse(wizard.origin_envia_sync_warning)
        contact = wizard._build_contact_for_side("origin")
        self.assertEqual(contact.address_id, "7295564")

    def test_quote_warns_when_warehouse_origin_missing(self):
        warehouse = self.env["stock.warehouse"].search(
            [("company_id", "=", self.env.company.id)],
            limit=1,
        )
        self.assertTrue(warehouse)
        self.env["envia.warehouse.origin"].search(
            [("warehouse_id", "=", warehouse.id)]
        ).unlink()
        wizard = self.env["envia.quote.wizard"].create(
            {"origin_warehouse_id": warehouse.id}
        )
        self.assertTrue(wizard.origin_envia_sync_warning)
