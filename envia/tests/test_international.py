from unittest.mock import MagicMock, patch

from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged

from odoo.addons.envia.services.envia_client import EnviaClient
from odoo.addons.envia.services.envia_official_adapter import EnviaOfficialAdapter
from odoo.addons.envia.services.international import (
    build_label_create_customs_payload,
    fallback_export_reasons,
    is_international,
    is_missing_product_code_error,
    missing_product_code_names,
    missing_product_code_user_message,
    normalize_export_reason,
    parse_export_reasons_body,
    validate_international_generate,
)


@tagged("post_install", "-at_install")
class TestEnviaInternational(TransactionCase):
    def test_is_international_truth_table(self):
        self.assertFalse(is_international(None, "US"))
        self.assertFalse(is_international("MX", ""))
        self.assertFalse(is_international("mx", "MX"))
        self.assertFalse(is_international(" MX ", "mx"))
        self.assertTrue(is_international("MX", "US"))
        self.assertTrue(is_international(" mx ", " us "))

    def test_missing_product_code_error_helpers(self):
        raw = (
            'International generate requires items.productCode '
            '(HS code). Missing for "Jean Jacket"'
        )
        self.assertTrue(is_missing_product_code_error(raw))
        self.assertEqual(missing_product_code_names(raw), ["Jean Jacket"])
        message = missing_product_code_user_message(["Jean Jacket"])
        self.assertIn("Jean Jacket", message)
        self.assertIn("International shipments need an HS code", message)
        self.assertNotIn("Envia raw error", message)
        self.assertFalse(is_missing_product_code_error("Not Enough money"))

    def test_normalize_export_reason(self):
        self.assertEqual(normalize_export_reason("gift"), "gift")
        self.assertEqual(normalize_export_reason(""), "sale")
        self.assertEqual(normalize_export_reason(None, is_return=True), "return")

    def test_validate_international_generate_blocks_missing_fields(self):
        with self.assertRaises(UserError) as error:
            validate_international_generate(
                origin_tax_id="",
                destination_tax_id="",
                export_reason="",
                customs_items=[],
                declared_value=0,
            )
        message = str(error.exception)
        self.assertIn("Origin tax ID", message)
        self.assertIn("Destination tax ID", message)
        self.assertIn("Export reason", message)

    def test_validate_customs_items_require_hs_and_coo(self):
        with self.assertRaises(UserError) as error:
            validate_international_generate(
                origin_tax_id="XAXX010101000",
                destination_tax_id="12-3456789",
                export_reason="sale",
                customs_items=[
                    {
                        "description": "Widget",
                        "quantity": 1,
                        "price": 10.0,
                        "productCode": "",
                        "countryOfManufacture": "",
                    }
                ],
                declared_value=10.0,
            )
        message = str(error.exception)
        self.assertIn("Widget", message)
        self.assertIn("HS code", message)
        self.assertIn("country of manufacture", message.casefold())

    def test_label_create_customs_payload_omits_empties(self):
        payload = build_label_create_customs_payload(
            origin_tax_id=" ORIG ",
            destination_tax_id="",
            export_reason="sale",
            duties_payment_entity="sender",
        )
        self.assertEqual(payload["identification_number_origin"], "ORIG")
        self.assertNotIn("identification_number_destination", payload)
        self.assertEqual(payload["customs_settings"], {"exportReason": "sale"})
        self.assertNotIn("dutiesPaymentEntity", payload["customs_settings"])

    def test_export_reasons_fallback_on_api_failure(self):
        with patch.object(
            EnviaClient,
            "_public_get_json",
            side_effect=UserError("boom"),
        ):
            reasons = EnviaClient.get_bol_export_reasons("https://queries.envia.com/")
        self.assertEqual(reasons, fallback_export_reasons())

    def test_parse_export_reasons_body(self):
        parsed = parse_export_reasons_body(
            [{"name": "sale"}, {"name": "gift", "label": "Gift"}]
        )
        self.assertEqual(parsed[0], {"name": "sale", "label": "Sale"})
        self.assertEqual(parsed[1]["name"], "gift")

    def test_label_create_national_omits_customs(self):
        adapter = EnviaOfficialAdapter(MagicMock(token="token"), shop_id="34084")
        with patch(
            "odoo.addons.envia.services.envia_official_adapter.EnviaClient._post",
            return_value={
                "status": True,
                "data": {
                    "labels": [
                        {
                            "trackingNumber": "1Z",
                            "label": "https://example.com/l.pdf",
                            "carrier": "fedex",
                            "shipmentId": "S1",
                        }
                    ]
                },
            },
        ) as mock_post:
            adapter.create_label_for_odoo_order(42, service_id=442)
            _path, payload = mock_post.call_args.args[:2]
            self.assertEqual(payload, {"id": "42", "service_id": 442})

    def test_label_create_international_includes_customs(self):
        adapter = EnviaOfficialAdapter(MagicMock(token="token"), shop_id="34084")
        extras = build_label_create_customs_payload(
            origin_tax_id="ORIGIN",
            destination_tax_id="DEST",
            export_reason="sale",
        )
        with patch(
            "odoo.addons.envia.services.envia_official_adapter.EnviaClient._post",
            return_value={
                "status": True,
                "data": {
                    "labels": [
                        {
                            "trackingNumber": "1Z",
                            "label": "https://example.com/l.pdf",
                            "carrier": "fedex",
                            "shipmentId": "S1",
                        }
                    ]
                },
            },
        ) as mock_post:
            adapter.create_label_for_odoo_order(
                42, service_id=442, customs_extras=extras
            )
            _path, payload = mock_post.call_args.args[:2]
            self.assertEqual(payload["identification_number_origin"], "ORIGIN")
            self.assertEqual(payload["identification_number_destination"], "DEST")
            self.assertEqual(payload["customs_settings"], {"exportReason": "sale"})

    def test_label_create_merges_origin_branch_code(self):
        adapter = EnviaOfficialAdapter(MagicMock(token="token"), shop_id="34084")
        extras = {"origin": {"branchCode": "SUL"}}
        with patch(
            "odoo.addons.envia.services.envia_official_adapter.EnviaClient._post",
            return_value={
                "status": True,
                "data": {
                    "labels": [
                        {
                            "trackingNumber": "1Z",
                            "label": "https://example.com/l.pdf",
                            "carrier": "fedex",
                            "shipmentId": "S1",
                        }
                    ]
                },
            },
        ) as mock_post:
            adapter.create_label_for_odoo_order(
                42, service_id=691, customs_extras=extras
            )
            _path, payload = mock_post.call_args.args[:2]
            self.assertEqual(
                payload,
                {
                    "id": "42",
                    "service_id": 691,
                    "origin": {"branchCode": "SUL"},
                },
            )

    def test_contact_to_label_create_address_contract(self):
        from odoo.addons.envia.services.dto import Contact

        contact = Contact(
            name="Ana García",
            company="Tienda Demo SA de CV",
            email="tienda@demo.mx",
            phone="8181234567",
            street="Av. Constitución",
            number="1500",
            interior_number="4",
            district="Centro",
            city="Monterrey",
            state="NL",
            postal_code="64000",
            country="MX",
            identification_number="XAXX010101000",
        )
        payload = EnviaOfficialAdapter.contact_to_label_create_address(contact)
        self.assertEqual(
            payload,
            {
                "name": "Ana García",
                "email": "tienda@demo.mx",
                "phone": "8181234567",
                "street": "Av. Constitución",
                "number": "1500",
                "city": "Monterrey",
                "state": "NL",
                "country": "MX",
                "postalCode": "64000",
                "company": "Tienda Demo SA de CV",
                "interior_number": "4",
                "district": "Centro",
                "identification_number": "XAXX010101000",
            },
        )
        empty_interior = Contact(
            name="Juan",
            street="Carrer",
            city="Barcelona",
            state="B",
            postal_code="08007",
            country="ES",
            phone="34600000000",
            email="juan@mail.com",
            interior_number="",
            district="",
            company="",
        )
        sparse = EnviaOfficialAdapter.contact_to_label_create_address(empty_interior)
        self.assertNotIn("interior_number", sparse)
        self.assertNotIn("district", sparse)
        self.assertNotIn("company", sparse)

    def test_envia_state_code_strips_country_prefix(self):
        self.assertEqual(EnviaOfficialAdapter.envia_state_code("MX", "MX-JAL"), "JAL")
        self.assertEqual(EnviaOfficialAdapter.envia_state_code("US", "US-CA"), "CA")

    def test_contact_address_includes_identification_number(self):
        from odoo.addons.envia.services.dto import Contact

        contact = Contact(
            name="Ada",
            street="1 Main",
            city="Austin",
            state="TX",
            postal_code="78701",
            country="US",
            phone="555",
            email="a@b.c",
            identification_number="12-3456789",
        )
        checkout = EnviaOfficialAdapter._contact_to_checkout_address(contact)
        official = EnviaOfficialAdapter._contact_to_official_address(contact)
        self.assertEqual(checkout["identificationNumber"], "12-3456789")
        self.assertEqual(official["identificationNumber"], "12-3456789")
