from unittest.mock import patch
import json

from odoo.exceptions import UserError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase

from odoo.addons.envia.services.envia_client import EnviaApiError, EnviaClient

_SAMPLE_SCHEMA = [
    {
        "fieldId": "address1",
        "fieldName": "street",
        "fieldLabel": "Address1",
        "fieldType": "text",
        "dataType": "string",
        "visible": True,
        "rules": {"required": True},
    },
    {
        "fieldId": "identificationNumber",
        "fieldName": "identification_number",
        "fieldLabel": "Identification Number",
        "fieldType": "text",
        "dataType": "string",
        "visible": True,
        "rules": {"required": False},
    },
    {
        "fieldId": "state",
        "fieldName": "state",
        "fieldLabel": "State",
        "fieldType": "select",
        "dataType": "string",
        "visible": True,
        "rules": {"required": True, "validationType": "select"},
        "on_change": {
            "set_options": "city",
            "clear_fields": ["city", "city_select"],
        },
    },
    {
        "fieldId": "city",
        "fieldName": "city",
        "fieldLabel": "City",
        "fieldType": "text",
        "dataType": "string",
        "visible": False,
        "geocode": "https://queries.envia.com/provinces/$state",
        "rules": {"required": False},
    },
    {
        "fieldId": "city_select",
        "fieldName": "city_select",
        "fieldLabel": "City",
        "fieldType": "select",
        "dataType": "string",
        "visible": True,
        "rules": {"required": True},
        "on_change": {
            "set_fields": {"postal_code": "{{$city}}"},
        },
    },
    {
        "fieldId": "postalCode",
        "fieldName": "postal_code",
        "fieldLabel": "Zip Code",
        "fieldType": "text",
        "dataType": "string",
        "visible": False,
        "rules": {"required": False},
    },
    {
        "fieldId": "reference",
        "fieldName": "reference",
        "fieldLabel": "Reference",
        "fieldType": "text",
        "dataType": "string",
        "visible": True,
        "rules": {"required": False},
    },
]

_SAMPLE_STATES = [
    {"name": "Antioquia", "code_2_digits": "AN", "country_code": "CO"},
    {"name": "Amazonas", "code_2_digits": "AM", "country_code": "CO"},
]

_SAMPLE_PROVINCES = [
    {"name": "ABEJORRAL", "state_code": "AN", "code": "05002000"},
    {"name": "ABRIAQUI", "state_code": "AN", "code": "05004000"},
]


@tagged("post_install", "-at_install")
class TestEnviaBillingInfoWizard(TransactionCase):
    def setUp(self):
        super().setUp()
        self.company = self.env.company
        self.company.envia_environment = "sandbox"
        co = self.env["res.country"].search([("code", "=", "CO")], limit=1)
        self.assertTrue(co)
        self.company.country_id = co

    def _create_loaded_wizard(self):
        """Backend helper: create transient + load schema (OWL UI uses RPC instead)."""
        wizard = self.env["envia.billing.info.wizard"].create(
            {
                "company_id": self.company.id,
                "country_id": self.company.country_id.id,
                "phone_dial_code": "+57",
            }
        )
        wizard._load_form_lines()
        return wizard

    def test_action_open_returns_owl_client_action(self):
        action = self.env["envia.billing.info.wizard"].action_open_billing_info_wizard()
        self.assertEqual(action["type"], "ir.actions.client")
        self.assertEqual(action["tag"], "envia_generic_form")
        self.assertEqual(action["params"]["country_code"], "CO")
        self.assertEqual(action["params"]["form"], "address_info")
        self.assertTrue(action["params"]["queries_base_url"])
        self.assertTrue(action["params"].get("warehouse_id"))
        self.assertNotIn("initial_values", action["params"])

    def test_action_open_prefills_from_partner_address(self):
        mx = self.env.ref("base.mx")
        state = self.env["res.country.state"].search(
            [("country_id", "=", mx.id), ("code", "=", "NL")],
            limit=1,
        )
        partner = self.env["res.partner"].create(
            {
                "name": "ALMACEN PRUEBA",
                "street": "Aurora boreal",
                "street2": "Centro",
                "city": "Monterrey",
                "zip": "64000",
                "country_id": mx.id,
                "state_id": state.id if state else False,
                "email": "wh@example.com",
                "phone": "+528181234567",
            }
        )
        action = self.env["envia.billing.info.wizard"].action_open_billing_info_wizard(
            partner=partner
        )
        params = action["params"]
        self.assertEqual(params["country_code"], "MX")
        self.assertEqual(params["phone_code"], f"+{mx.phone_code}")
        initial = params["initial_values"]
        self.assertEqual(initial["identity"]["name"], "ALMACEN PRUEBA")
        self.assertEqual(initial["identity"]["email"], "wh@example.com")
        self.assertEqual(initial["identity"]["phone"], "8181234567")
        self.assertEqual(initial["values"]["street"], "Aurora boreal")
        self.assertEqual(initial["values"]["district"], "Centro")
        self.assertEqual(initial["values"]["postal_code"], "64000")
        self.assertEqual(initial["values"]["city"], "Monterrey")
        if state:
            self.assertEqual(initial["values"]["state"], "NL")

    @patch(
        "odoo.addons.envia.wizards.envia_billing_info_wizard.EnviaClient.get_generic_form",
        return_value=_SAMPLE_SCHEMA,
    )
    def test_fetch_generic_form_proxies_envia(self, mock_form):
        result = self.env["envia.billing.info.wizard"].fetch_generic_form("CO", "address_info")
        self.assertEqual(result, _SAMPLE_SCHEMA)
        mock_form.assert_called_once()
        self.assertEqual(mock_form.call_args.args[1], "CO")
        self.assertEqual(mock_form.call_args.kwargs.get("form"), "address_info")

    def test_state_city_domains_use_wizard_id(self):
        """Promoted M2O options are flushed to DB; domain filters by wizard_id."""
        Wizard = self.env["envia.billing.info.wizard"]
        self.assertIn("wizard_id", Wizard._fields["state_option_id"].domain)
        self.assertIn("wizard_id", Wizard._fields["city_option_id"].domain)
        self.assertIn("'state'", Wizard._fields["state_option_id"].domain)
        self.assertIn("'city'", Wizard._fields["city_option_id"].domain)

    @patch(
        "odoo.addons.envia.wizards.envia_billing_info_wizard.EnviaClient.get_states",
        return_value=_SAMPLE_STATES,
    )
    @patch(
        "odoo.addons.envia.wizards.envia_billing_info_wizard.EnviaClient.get_address_structure",
        return_value=_SAMPLE_SCHEMA,
    )
    def test_country_onchange_persists_state_options(self, _mock_form, _mock_states):
        wizard = self._create_loaded_wizard()
        mx = self.env["res.country"].search([("code", "=", "MX")], limit=1)
        self.assertTrue(mx)
        wizard.country_id = mx
        wizard._onchange_country_id()
        # After onchange sync, options must be real DB rows for this wizard.
        db_states = self.env["envia.billing.info.option"].search(
            [("wizard_id", "=", wizard.id), ("kind", "=", "state")]
        )
        self.assertTrue(db_states)
        self.assertEqual(set(db_states.mapped("key")), {"AN", "AM"})
        self.assertTrue(all(isinstance(opt.id, int) for opt in db_states))

    @patch(
        "odoo.addons.envia.wizards.envia_billing_info_wizard.EnviaClient.get_states",
        return_value=_SAMPLE_STATES,
    )
    @patch(
        "odoo.addons.envia.wizards.envia_billing_info_wizard.EnviaClient.get_address_structure",
        return_value=_SAMPLE_SCHEMA,
    )
    def test_open_loads_visible_lines_and_state_options(self, mock_form, mock_states):
        wizard = self._create_loaded_wizard()
        self.assertEqual(wizard.country_id.code, "CO")
        self.assertTrue(wizard.schema_json)
        self.assertTrue(wizard.show_state_field)
        self.assertTrue(wizard.show_city_field)
        field_ids = set(wizard.line_ids.mapped("field_id"))
        self.assertEqual(
            field_ids,
            {"address1", "identificationNumber", "reference", "alias"},
        )
        self.assertNotIn("state", field_ids)
        self.assertNotIn("city_select", field_ids)
        self.assertNotIn("city", field_ids)
        self.assertNotIn("postalCode", field_ids)
        self.assertEqual(
            set(wizard.option_ids.filtered(lambda opt: opt.kind == "state").mapped("key")),
            {"AN", "AM"},
        )
        mock_form.assert_called()
        self.assertEqual(mock_form.call_args.args[1], "CO")
        mock_states.assert_called()

    @patch(
        "odoo.addons.envia.wizards.envia_billing_info_wizard.EnviaClient._public_get_json",
        return_value={"data": _SAMPLE_PROVINCES},
    )
    @patch(
        "odoo.addons.envia.wizards.envia_billing_info_wizard.EnviaClient.get_states",
        return_value=_SAMPLE_STATES,
    )
    @patch(
        "odoo.addons.envia.wizards.envia_billing_info_wizard.EnviaClient.get_address_structure",
        return_value=_SAMPLE_SCHEMA,
    )
    def test_state_change_uses_geocode_url_as_is(
        self, _mock_form, _mock_states, mock_get_json
    ):
        # Override city geocode to heroku-style URL with country query.
        schema = json.loads(json.dumps(_SAMPLE_SCHEMA))
        for field in schema:
            if field["fieldId"] == "city":
                field["geocode"] = (
                    "https://queries-stage.herokuapp.com/provinces/$state?country=$country"
                )
        with patch(
            "odoo.addons.envia.wizards.envia_billing_info_wizard.EnviaClient.get_address_structure",
            return_value=schema,
        ):
            wizard = self._create_loaded_wizard()
        wizard.state_option_id = wizard.option_ids.filtered(
            lambda opt: opt.kind == "state" and opt.key == "AN"
        )
        wizard._onchange_state_option_id()
        self.assertEqual(
            set(wizard.option_ids.filtered(lambda opt: opt.kind == "city").mapped("key")),
            {"05002000", "05004000"},
        )
        mock_get_json.assert_called()
        called_url = mock_get_json.call_args.args[0]
        self.assertEqual(
            called_url,
            "https://queries-stage.herokuapp.com/provinces/AN?country=CO",
        )

    @patch(
        "odoo.addons.envia.wizards.envia_billing_info_wizard.EnviaClient.get_provinces",
        return_value=_SAMPLE_PROVINCES,
    )
    @patch(
        "odoo.addons.envia.wizards.envia_billing_info_wizard.EnviaClient.get_states",
        return_value=_SAMPLE_STATES,
    )
    @patch(
        "odoo.addons.envia.wizards.envia_billing_info_wizard.EnviaClient.get_address_structure",
        return_value=_SAMPLE_SCHEMA,
    )
    def test_state_change_loads_city_options_from_geocode(
        self, _mock_form, _mock_states, _mock_provinces
    ):
        wizard = self._create_loaded_wizard()
        wizard.state_option_id = wizard.option_ids.filtered(
            lambda opt: opt.kind == "state" and opt.key == "AN"
        )
        with patch(
            "odoo.addons.envia.wizards.envia_billing_info_wizard.EnviaClient._public_get_json",
            return_value={"data": _SAMPLE_PROVINCES},
        ) as mock_get_json:
            wizard._onchange_state_option_id()
            mock_get_json.assert_called()
            self.assertIn("/provinces/AN", mock_get_json.call_args.args[0])
        self.assertEqual(
            set(wizard.option_ids.filtered(lambda opt: opt.kind == "city").mapped("key")),
            {"05002000", "05004000"},
        )

    @patch(
        "odoo.addons.envia.wizards.envia_billing_info_wizard.EnviaClient.get_states",
        return_value=_SAMPLE_STATES,
    )
    @patch(
        "odoo.addons.envia.wizards.envia_billing_info_wizard.EnviaClient.get_address_structure",
        return_value=_SAMPLE_SCHEMA,
    )
    def test_validate_requires_visible_fields(self, _mock_form, _mock_states):
        wizard = self._create_loaded_wizard()
        with self.assertRaises(UserError):
            wizard.action_validate()
        wizard.write(
            {
                "name": "Malcom Prado",
                "email": "malcom.prado@envia.com",
                "phone": "8121211454",
            }
        )
        with self.assertRaises(UserError) as error:
            wizard.action_validate()
        self.assertIn("Address1", str(error.exception))

    @patch(
        "odoo.addons.envia.wizards.envia_billing_info_wizard.EnviaClient._public_get_json",
        return_value={"data": _SAMPLE_PROVINCES},
    )
    @patch(
        "odoo.addons.envia.wizards.envia_billing_info_wizard.EnviaClient.get_states",
        return_value=_SAMPLE_STATES,
    )
    @patch(
        "odoo.addons.envia.wizards.envia_billing_info_wizard.EnviaClient.get_address_structure",
        return_value=_SAMPLE_SCHEMA,
    )
    def test_validate_succeeds_when_required_visible_filled(
        self, _mock_form, _mock_states, _mock_get_json
    ):
        wizard = self._create_loaded_wizard()
        address = wizard.line_ids.filtered(lambda line: line.field_id == "address1")
        wizard.write(
            {
                "name": "Malcom Prado",
                "email": "malcom.prado@envia.com",
                "phone": "8121211454",
                "phone_dial_code": "+57",
            }
        )
        address.value = "Calle 10"
        wizard.state_option_id = wizard.option_ids.filtered(
            lambda opt: opt.kind == "state" and opt.key == "AN"
        )
        wizard._onchange_state_option_id()
        wizard.city_option_id = wizard.option_ids.filtered(
            lambda opt: opt.kind == "city"
        )[:1]
        result = wizard.action_validate()
        self.assertEqual(result["params"]["type"], "success")

    @patch(
        "odoo.addons.envia.wizards.envia_billing_info_wizard.EnviaClient.get_address_structure",
        side_effect=EnviaApiError("Envia API error (422): Invalid data."),
    )
    def test_api_error_sets_warning_without_raising(self, _mock_form):
        wizard = self._create_loaded_wizard()
        self.assertFalse(wizard.line_ids)
        self.assertTrue(wizard.schema_warning)
        self.assertIn("CO", wizard.schema_warning)

    @patch(
        "odoo.addons.envia.wizards.envia_billing_info_wizard.EnviaClient.get_states",
        return_value=[],
    )
    @patch(
        "odoo.addons.envia.wizards.envia_billing_info_wizard.EnviaClient.get_address_structure",
        return_value=[
            {
                "fieldId": "street",
                "fieldName": "street",
                "fieldLabel": "Street",
                "fieldType": "text",
                "visible": True,
                "rules": {"required": True},
            },
        ],
    )
    def test_any_country_loads_form_from_api(self, mock_form, _mock_states):
        """No hardcoded country allowlist — Envia generic-form decides."""
        es = self.env["res.country"].search([("code", "=", "ES")], limit=1)
        self.assertTrue(es)
        self.company.country_id = es
        wizard = self._create_loaded_wizard()
        mock_form.assert_called_once()
        self.assertEqual(mock_form.call_args.args[1], "ES")
        self.assertFalse(wizard.schema_warning)
        self.assertTrue(wizard.line_ids.filtered(lambda line: line.name == "street"))

    @patch(
        "odoo.addons.envia.wizards.envia_billing_info_wizard.EnviaGeocodesClient.lookup_zipcode",
        return_value=[
            {
                "locality": "Monterrey",
                "state": {"code": {"2digit": "NL", "3digit": "NLE"}},
                "suburbs": ["Centro", "Obispado"],
            }
        ],
    )
    @patch(
        "odoo.addons.envia.wizards.envia_billing_info_wizard.EnviaClient.get_states",
        return_value=[
            {"name": "Nuevo León", "code_2_digits": "NL", "country_code": "MX"},
        ],
    )
    @patch(
        "odoo.addons.envia.wizards.envia_billing_info_wizard.EnviaClient.get_address_structure",
        return_value=[
            {
                "fieldId": "postalCode",
                "fieldName": "postal_code",
                "fieldLabel": "Zip Code",
                "fieldType": "text",
                "visible": True,
                "rules": {"required": True},
                "on_change": [
                    {
                        "url": "https://geocodes.envia.com/zipcode/{{values.country}}/{{values.postal_code}}",
                        "name": "geocodes",
                        "action": "request",
                        "condition": "/(^[0-9]{5})$/",
                        "data_path": "[0]",
                    },
                    {
                        "action": "set_temporal_context",
                        "values": {
                            "state_code": {
                                "path": [
                                    "geocodes.state.code.2digit",
                                    "geocodes.state.code.3digit",
                                ]
                            }
                        },
                    },
                    {
                        "action": "set_fields",
                        "fields": {
                            "city": "{{geocodes.locality}}",
                            "state": "{{state_code}}",
                            "district": "{{geocodes.suburbs[0]}}",
                        },
                    },
                    {
                        "action": "set_options",
                        "fields": {"district": "geocodes.suburbs"},
                    },
                ],
            },
            {
                "fieldId": "city",
                "fieldName": "city",
                "fieldLabel": "City",
                "fieldType": "text",
                "visible": True,
                "rules": {"required": True},
            },
            {
                "fieldId": "state",
                "fieldName": "state",
                "fieldLabel": "State",
                "fieldType": "select",
                "visible": True,
                "rules": {"required": True},
            },
            {
                "fieldId": "district",
                "fieldName": "district",
                "fieldLabel": "Neighborhood",
                "fieldType": "text",
                "visible": True,
                "rules": {"required": False},
            },
        ],
    )
    def test_postal_code_geocodes_fills_city_state_district(
        self, _mock_form, _mock_states, mock_zip
    ):
        mx = self.env["res.country"].search([("code", "=", "MX")], limit=1)
        self.company.country_id = mx
        wizard = self._create_loaded_wizard()
        postal = wizard.line_ids.filtered(lambda line: line.field_id == "postalCode")
        city = wizard.line_ids.filtered(lambda line: line.field_id == "city")
        district = wizard.line_ids.filtered(lambda line: line.field_id == "district")
        postal.value = "64000"
        wizard.apply_line_on_change(postal)
        mock_zip.assert_called_once_with("MX", "64000")
        self.assertEqual(city.value, "Monterrey")
        self.assertTrue(wizard.show_state_field)
        self.assertEqual(wizard.state_option_id.key, "NL")
        self.assertEqual(district.value, "Centro")

    def test_extract_address_id_from_nested_data(self):
        self.assertEqual(
            EnviaClient.extract_address_id({"data": {"id": 3834910}}),
            "3834910",
        )
        self.assertEqual(
            EnviaClient.extract_address_id({"address_id": "99"}),
            "99",
        )

    def test_save_billing_address_requires_shop_and_token(self):
        Wizard = self.env["envia.billing.info.wizard"]
        warehouse = self.env["stock.warehouse"].search(
            [("company_id", "=", self.company.id)],
            limit=1,
        )
        self.assertTrue(warehouse)
        self.company.envia_shop_id = False
        self.company.envia_api_token = "token"
        with self.assertRaises(UserError):
            Wizard.save_billing_address({"name": "A", "country": "MX"}, warehouse.id)
        self.company.envia_shop_id = "34107"
        self.company.envia_api_token = False
        with self.assertRaises(UserError):
            Wizard.save_billing_address({"name": "A", "country": "MX"}, warehouse.id)

    def test_save_billing_address_requires_warehouse(self):
        self.company.envia_shop_id = "34107"
        self.company.envia_api_token = "shipping-token"
        with self.assertRaises(UserError):
            self.env["envia.billing.info.wizard"].save_billing_address(
                {"name": "A", "country": "MX", "company": "AA", "phone": "1", "email": "a@b.co"}
            )

    def test_get_origin_warehouse_options_includes_address_and_location(self):
        warehouse = self.env["stock.warehouse"].search(
            [("company_id", "=", self.company.id)],
            limit=1,
        )
        self.assertTrue(warehouse)
        partner = warehouse.partner_id
        partner.write(
            {
                "street": partner.street or "Aurora Boreal 301",
                "city": partner.city or "Guadalupe",
                "zip": partner.zip or "67192",
                "country_id": partner.country_id.id or self.env.ref("base.mx").id,
            }
        )
        options = self.env["envia.billing.info.wizard"].get_origin_warehouse_options()
        match = next((entry for entry in options if entry["id"] == warehouse.id), None)
        self.assertTrue(match)
        self.assertEqual(match["location_id"], warehouse.lot_stock_id.id)
        self.assertIn("Aurora Boreal", match["address_label"])
        self.assertTrue(match["defaults"])

    @patch("odoo.addons.envia.wizards.envia_billing_info_wizard.EnviaClient")
    def test_save_billing_address_company_falls_back_to_name(self, mock_client_cls):
        self.company.envia_shop_id = "34107"
        self.company.envia_api_token = "shipping-token"
        warehouse = self.env["stock.warehouse"].search(
            [("company_id", "=", self.company.id)],
            limit=1,
        )
        self.assertTrue(warehouse)
        client = mock_client_cls.return_value
        client.create_user_address.return_value = {"data": {"id": 1}}
        client.set_shop_default_address.return_value = {"ok": True}
        mock_client_cls.extract_address_id.return_value = "1"

        self.env["envia.billing.info.wizard"].save_billing_address(
            {
                "name": "Malcom Prado",
                "company": "a",
                "country": "MX",
                "phone": "8121211454",
                "email": "a@b.co",
            },
            warehouse.id,
        )
        payload = client.create_user_address.call_args.args[0]
        self.assertEqual(payload["company"], "Malcom Prado")
        mock_client_cls.assert_called_once()
        self.assertEqual(mock_client_cls.call_args.args[1], "shipping-token")

    @patch("odoo.addons.envia.wizards.envia_billing_info_wizard.EnviaClient")
    def test_save_billing_address_creates_then_matches_shop(self, mock_client_cls):
        self.company.envia_shop_id = "34107"
        self.company.envia_api_token = "shipping-token"
        warehouse = self.env["stock.warehouse"].search(
            [("company_id", "=", self.company.id)],
            limit=1,
        )
        self.assertTrue(warehouse)
        client = mock_client_cls.return_value
        client.create_user_address.return_value = {"data": {"id": 3834910}}
        client.set_shop_default_address.return_value = {"ok": True}
        mock_client_cls.extract_address_id.return_value = "3834910"

        result = self.env["envia.billing.info.wizard"].save_billing_address(
            {
                "name": "Malcom Alejandro Prado Vazquez",
                "company": "Malcom Alejandro Prado Vazquez",
                "phone": "8121211454",
                "phone_code": "MX",
                "email": "malcom.prado@envia.com",
                "country": "MX",
                "district": "Bugambilias de la Sierra",
                "postal_code": "67192",
                "street": "Aurora boreal",
                "number": "201",
                "city": "Guadalupe",
                "state": "NL",
            },
            warehouse.id,
        )

        self.assertEqual(result["address_id"], "3834910")
        mock_client_cls.assert_called_once()
        create_payload = client.create_user_address.call_args.args[0]
        self.assertEqual(create_payload["shop_id"], 34107)
        self.assertEqual(create_payload["category_id"], 1)
        self.assertEqual(create_payload["type"], 1)
        self.assertEqual(create_payload["state"], "NL")
        self.assertEqual(create_payload["location_iden"], str(warehouse.lot_stock_id.id))
        client.set_shop_default_address.assert_called_once_with("34107", "3834910")

    @patch("odoo.addons.envia.wizards.envia_billing_info_wizard.EnviaClient")
    def test_save_billing_address_links_warehouse_origin(self, mock_client_cls):
        self.company.envia_shop_id = "34107"
        self.company.envia_api_token = "shipping-token"
        warehouse = self.env["stock.warehouse"].search(
            [("company_id", "=", self.company.id)],
            limit=1,
        )
        self.assertTrue(warehouse)
        client = mock_client_cls.return_value
        client.create_user_address.return_value = {"data": {"id": 7295564}}
        client.set_shop_default_address.return_value = {"ok": True}
        mock_client_cls.extract_address_id.return_value = "7295564"

        result = self.env["envia.billing.info.wizard"].save_billing_address(
            {
                "name": "ALMACEN PRUEBA",
                "company": "ALMACEN PRUEBA",
                "phone": "8121211454",
                "email": "wh@example.com",
                "country": "MX",
                "postal_code": "64000",
                "street": "Aurora boreal",
                "number": "201",
                "city": "Monterrey",
                "state": "NL",
            },
            warehouse.id,
        )
        self.assertEqual(result["address_id"], "7295564")
        create_payload = client.create_user_address.call_args.args[0]
        self.assertEqual(create_payload["location_iden"], str(warehouse.lot_stock_id.id))
        client.set_shop_default_address.assert_called_once_with("34107", "7295564")
        match = self.env["envia.warehouse.origin"].browse(result["warehouse_origin_id"])
        self.assertTrue(match)
        self.assertEqual(match.warehouse_id, warehouse)
        self.assertEqual(match.envia_address_id, "7295564")
        self.assertEqual(warehouse._envia_origin_address_id(), "7295564")

    def test_matching_shop_origin_compares_street_number_zip(self):
        Wizard = self.env["envia.billing.info.wizard"]
        payload = {
            "street": "Aurora boreal",
            "number": "201",
            "city": "Guadalupe",
            "postal_code": "67192",
            "country": "MX",
        }
        existing = {
            "id": "55",
            "label": "My Company · Aurora boreal 201 · Guadalupe · 67192",
            "street": "Aurora boreal 201",
            "city": "Guadalupe",
            "zip": "67192",
            "country_code": "MX",
        }
        self.assertEqual(
            Wizard._matching_shop_origin([existing], payload)["id"],
            "55",
        )
        self.assertFalse(
            Wizard._matching_shop_origin(
                [{**existing, "zip": "64000"}],
                payload,
            )
        )

    @patch("odoo.addons.envia.wizards.envia_billing_info_wizard.EnviaClient")
    def test_save_billing_address_reuses_existing_shop_origin(self, mock_client_cls):
        self.company.envia_shop_id = "34107"
        self.company.envia_api_token = "shipping-token"
        warehouse = self.env["stock.warehouse"].search(
            [("company_id", "=", self.company.id)],
            limit=1,
        )
        self.assertTrue(warehouse)
        client = mock_client_cls.return_value
        client.get_shop_default_addresses.return_value = [
            {
                "id": "55",
                "label": "My Company · Aurora boreal 201 · Guadalupe · 67192",
                "name": "My Company",
                "street": "Aurora boreal 201",
                "city": "Guadalupe",
                "zip": "67192",
                "country_code": "MX",
            }
        ]
        client.set_shop_default_address.return_value = {"ok": True}

        result = self.env["envia.billing.info.wizard"].save_billing_address(
            {
                "name": "My Company",
                "company": "My Company",
                "phone": "8121211454",
                "email": "a@b.co",
                "country": "MX",
                "postal_code": "67192",
                "street": "Aurora boreal",
                "number": "201",
                "city": "Guadalupe",
                "state": "NL",
            },
            warehouse.id,
        )

        self.assertEqual(result["address_id"], "55")
        self.assertTrue(result["reused"])
        client.create_user_address.assert_not_called()
        client.get_shop_default_addresses.assert_called_once_with("34107")
        client.set_shop_default_address.assert_called_once_with("34107", "55")
        self.assertEqual(warehouse._envia_origin_address_id(), "55")
