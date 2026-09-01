from unittest.mock import MagicMock, patch

from odoo.exceptions import UserError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase

from odoo.addons.envia.services.envia_client import EnviaClient
from odoo.addons.envia.services.envia_official_adapter import EnviaOfficialAdapter


@tagged("post_install", "-at_install")
class TestEnviaClient(TransactionCase):
    @patch("odoo.addons.envia.services.envia_client.requests.get")
    def test_test_connection_validates_carrier_list_response(self, mock_get):
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "data": [
                    {"name": "fedex", "description": "FedEx", "active": True},
                    {"name": "dhl", "description": "DHL", "active": True},
                ]
            },
            text='{"data": []}',
        )
        client = EnviaClient("https://api-test.envia.com/", "shipping-token")
        body = client.test_connection(
            queries_base_url="https://queries.test.envia.com/",
            country_code="MX",
        )
        self.assertEqual(len(body["data"]), 2)
        mock_get.assert_called_once()
        call_kwargs = mock_get.call_args.kwargs
        self.assertEqual(call_kwargs["params"], {"country_code": "MX"})
        self.assertIn("Bearer shipping-token", call_kwargs["headers"]["Authorization"])

    @patch("odoo.addons.envia.services.envia_client.requests.get")
    def test_test_connection_rejects_invalid_token(self, mock_get):
        mock_get.return_value = MagicMock(
            status_code=401,
            json=lambda: {"message": "Unauthorized"},
            text='{"message": "Unauthorized"}',
        )
        client = EnviaClient("https://api-test.envia.com/", "bad-token")
        with self.assertRaises(UserError):
            client.test_connection(
                queries_base_url="https://queries.test.envia.com/",
                country_code="MX",
            )

    @patch("odoo.addons.envia.services.envia_client.requests.get")
    def test_test_connection_rejects_unexpected_response_format(self, mock_get):
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"status": "ok"},
            text='{"status": "ok"}',
        )
        client = EnviaClient("https://api-test.envia.com/", "shipping-token")
        with self.assertRaises(UserError):
            client.test_connection(
                queries_base_url="https://queries.test.envia.com/",
                country_code="MX",
            )

    @patch("odoo.addons.envia.services.envia_client.requests.get")
    def test_get_branches_sends_type_and_zipcode(self, mock_get):
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "data": [
                    {
                        "branch_id": "LNT",
                        "reference": "Branch LNT",
                        "address": {
                            "street": "Av. Insurgentes Sur",
                            "city": "Alvaro Obregon",
                            "postalCode": "01000",
                            "state": "CX",
                            "country": "MX",
                        },
                    }
                ]
            },
            text='{"data": []}',
        )
        client = EnviaClient("https://api-test.envia.com/", "shipping-token")
        branches = client.get_branches(
            queries_base_url="https://queries.test.envia.com/",
            carrier="estafeta",
            country_code="MX",
            zipcode="64060",
            search_type=2,
        )
        self.assertEqual(len(branches), 1)
        self.assertEqual(
            mock_get.call_args.args[0],
            "https://queries.test.envia.com/branches/estafeta/MX",
        )
        self.assertEqual(
            mock_get.call_args.kwargs["params"],
            {"type": 2, "zipcode": "64060", "allBranch": False},
        )

    @patch("odoo.addons.envia.services.envia_client.requests.post")
    def test_post_accepts_optional_base_url(self, mock_post):
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {"success": True, "packages": []},
            text='{"success": true}',
        )
        client = EnviaClient("https://api-test.envia.com/", "shipping-token")
        body = client._post(
            "package/dimensions/test/34084",
            {"items": [], "currency": "MXN"},
            base_url="https://ecommerce-private.envia.com/",
        )
        self.assertTrue(body["success"])
        self.assertEqual(
            mock_post.call_args.args[0],
            "https://ecommerce-private.envia.com/package/dimensions/test/34084",
        )

    @patch("odoo.addons.envia.services.envia_client.requests.get")
    def test_get_generic_form_returns_field_list_without_auth(self, mock_get):
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: [
                {
                    "fieldId": "nombre",
                    "dataName": "name",
                    "fieldLabel": "Nombre",
                    "dataType": "text",
                    "visible": True,
                    "rules": {"required": True},
                },
                {
                    "fieldId": "rfc",
                    "dataName": "rfc",
                    "fieldLabel": "RFC",
                    "dataType": "text",
                    "visible": True,
                    "rules": {"required": True},
                },
            ],
            text="[]",
        )
        fields = EnviaClient.get_generic_form(
            "https://queries.test.envia.com/",
            "MX",
            form="address_info",
        )
        self.assertEqual(len(fields), 2)
        self.assertEqual(fields[0]["fieldId"], "nombre")
        self.assertEqual(
            mock_get.call_args.args[0],
            "https://queries.test.envia.com/generic-form",
        )
        self.assertEqual(
            mock_get.call_args.kwargs["params"],
            {"country_code": "MX", "form": "address_info"},
        )
        self.assertNotIn("Authorization", mock_get.call_args.kwargs["headers"])

    @patch("odoo.addons.envia.services.envia_client.requests.get")
    def test_get_generic_form_rejects_non_list_payload(self, mock_get):
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"fields": []},
            text='{"fields": []}',
        )
        with self.assertRaises(UserError):
            EnviaClient.get_generic_form("https://queries.test.envia.com/", "MX")

    @patch("odoo.addons.envia.services.envia_client.requests.get")
    def test_get_states_returns_data_list(self, mock_get):
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "data": [
                    {"name": "Antioquia", "code_2_digits": "AN", "country_code": "CO"},
                ]
            },
            text='{"data": []}',
        )
        states = EnviaClient.get_states("https://queries.test.envia.com/", "CO")
        self.assertEqual(len(states), 1)
        self.assertEqual(
            mock_get.call_args.args[0],
            "https://queries.test.envia.com/state",
        )
        self.assertEqual(mock_get.call_args.kwargs["params"], {"country_code": "CO"})

    @patch("odoo.addons.envia.services.envia_client.requests.get")
    def test_get_provinces_returns_data_list(self, mock_get):
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "data": [{"name": "ABEJORRAL", "state_code": "AN", "code": "05002000"}]
            },
            text='{"data": []}',
        )
        provinces = EnviaClient.get_provinces("https://queries.test.envia.com/", "AN")
        self.assertEqual(len(provinces), 1)
        self.assertEqual(
            mock_get.call_args.args[0],
            "https://queries.test.envia.com/provinces/AN",
        )

    def test_refine_branches_near_zip_prefers_exact_postal_code(self):
        branches = [
            {"branch_id": "A", "distance": 2, "address": {"postalCode": "67170"}},
            {"branch_id": "B", "distance": 1, "address": {"postalCode": "64000"}},
            {"branch_id": "C", "distance": 3, "address": {"postalCode": "67192"}},
        ]
        refined = EnviaClient.refine_branches_near_zip(branches, "67192")
        self.assertEqual([entry["branch_id"] for entry in refined], ["C"])

    def test_refine_branches_near_zip_falls_back_to_prefix(self):
        branches = [
            {"branch_id": "A", "distance": 2, "address": {"postalCode": "67170"}},
            {"branch_id": "B", "distance": 1, "address": {"postalCode": "64000"}},
        ]
        refined = EnviaClient.refine_branches_near_zip(branches, "67192")
        self.assertEqual([entry["branch_id"] for entry in refined], ["A"])

    @patch("odoo.addons.envia.services.envia_client.requests.post")
    def test_cancel_shipments_posts_to_queries_base(self, mock_post):
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {"success": True},
            text='{"success": true}',
        )
        client = EnviaClient("https://api-test.envia.com/", "shipping-token")
        body = client._post(
            "shipments/bulk/cancel",
            {"shipments": [40772217]},
            base_url="https://queries.test.envia.com/",
        )
        self.assertEqual(body, {"success": True})
        self.assertEqual(
            mock_post.call_args.args[0],
            "https://queries.test.envia.com/shipments/bulk/cancel",
        )
        self.assertEqual(
            mock_post.call_args.kwargs["json"],
            {"shipments": [40772217]},
        )

    @patch("odoo.addons.envia.services.envia_client.requests.delete")
    def test_unlink_order_shipment_deletes_on_queries_base(self, mock_delete):
        mock_delete.return_value = MagicMock(
            status_code=200,
            content=b'{"success": true}',
            json=lambda: {"success": True},
            text='{"success": true}',
        )
        client = EnviaClient("https://api-test.envia.com/", "shipping-token")
        body = client._delete(
            "orders/34165/110331/fulfillment/order-shipments",
            {"shipment_id": 179909},
            base_url="https://queries.test.envia.com/",
        )
        self.assertEqual(body, {"success": True})
        self.assertEqual(
            mock_delete.call_args.args[0],
            "https://queries.test.envia.com/orders/34165/110331/fulfillment/order-shipments",
        )
        self.assertEqual(
            mock_delete.call_args.kwargs["json"],
            {"shipment_id": 179909},
        )

    def test_label_create_feature_not_enabled_asks_to_enable_in_envia(self):
        with self.assertRaises(UserError) as error:
            EnviaOfficialAdapter._parse_label_create_response(
                {
                    "status": False,
                    "message": "Feature not enabled for this shop.",
                }
            )
        message = str(error.exception)
        self.assertIn("Label generation from the store", message)
        self.assertIn("Envia", message)
        self.assertNotIn("Feature not enabled", message)

    def test_label_create_feature_not_enabled_redirects_to_shipping_rules(self):
        from unittest.mock import MagicMock

        from odoo.addons.envia.services.envia_official_adapter import EnviaOfficialAdapter
        from odoo.exceptions import RedirectWarning

        client = MagicMock()
        client.token = "token"
        client._post.return_value = {
            "status": False,
            "message": "Feature not enabled for this shop.",
        }
        action = self.env.ref("envia.action_envia_open_shipping_rules_settings")
        adapter = EnviaOfficialAdapter(
            client,
            shop_id="125410",
            label_settings_action_id=action.id,
        )
        with self.assertRaises(RedirectWarning) as error:
            adapter.create_label_for_odoo_order(42, service_id=1)
        self.assertEqual(error.exception.args[1], action.id)
        self.assertIn("Label generation from the store", str(error.exception.args[0]))

    def test_label_create_feature_not_enabled_redirects_with_spanish_message(self):
        """UI language ES humanizes first; redirect must still fire."""
        from odoo.addons.envia.services.envia_official_adapter import EnviaOfficialAdapter
        from odoo.exceptions import RedirectWarning, UserError

        action = self.env.ref("envia.action_envia_open_shipping_rules_settings")
        adapter = EnviaOfficialAdapter(
            MagicMock(token="token"),
            shop_id="125410",
            label_settings_action_id=action.id,
        )
        spanish = UserError(
            "Habilite «Generación de etiquetas desde la tienda» en Envia "
            "para este shop e intente de nuevo."
        )
        with self.assertRaises(RedirectWarning) as error:
            adapter._reraise_label_settings_redirect(spanish)
            raise spanish
        self.assertEqual(error.exception.args[1], action.id)

    def test_label_create_requires_numeric_service_id(self):
        """label/create must not POST without Envia numeric service_id."""
        from unittest.mock import MagicMock, patch

        from odoo.addons.envia.services.envia_official_adapter import EnviaOfficialAdapter

        adapter = EnviaOfficialAdapter(MagicMock(token="token"), shop_id="34084")
        with patch(
            "odoo.addons.envia.services.envia_official_adapter.EnviaClient._post"
        ) as mock_post:
            with self.assertRaises(UserError) as error:
                adapter.create_label_for_odoo_order(42, service_id=False)
            self.assertIn("service_id", str(error.exception).casefold())
            mock_post.assert_not_called()

            mock_post.return_value = {
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
            }
            adapter.create_label_for_odoo_order(42, service_id=442)
            _path, payload = mock_post.call_args.args[:2]
            self.assertEqual(payload, {"id": "42", "service_id": 442})

    def test_label_create_not_enough_money_asks_to_add_funds(self):
        with self.assertRaises(UserError) as error:
            EnviaOfficialAdapter._parse_label_create_response(
                {
                    "status": False,
                    "message": "Not Enough money",
                }
            )
        message = str(error.exception)
        self.assertIn("enough balance", message.casefold())
        self.assertIn("Envia.com", message)
        self.assertNotIn("Not Enough money", message)
        self.assertNotIn("did not generate a label", message.casefold())

    def test_label_create_errors_array_already_fulfilled(self):
        """Business validation uses ``errors``, not ``message``."""
        with self.assertRaises(UserError) as error:
            EnviaOfficialAdapter._parse_label_create_response(
                {
                    "status": False,
                    "errors": ["Order already fulfilled"],
                }
            )
        message = str(error.exception)
        self.assertIn("already fulfilled", message.casefold())
        self.assertNotIn("did not generate a label", message.casefold())
        self.assertNotIn("Order already fulfilled", message)

    def test_label_create_known_api_messages_are_humanized(self):
        cases = (
            (
                "Shop not found.",
                ("shop", "reconnect"),
                ("Shop not found",),
            ),
            (
                "Feature not available for this ecommerce.",
                ("not available", "ecommerce"),
                ("Feature not available",),
            ),
            (
                "Duplicated Order.",
                ("already exists",),
                ("Duplicated Order",),
            ),
            (
                "Order ignored.",
                ("ignored", "shipping method"),
                ("Order ignored",),
            ),
            (
                "Invalid Order.",
                ("incomplete", "order"),
                ("Invalid Order",),
            ),
            (
                "Order not found.",
                ("could not find", "order"),
                ("Order not found.",),
            ),
            (
                "Order not found and could not be saved.",
                ("could not sync",),
                ("could not be saved",),
            ),
            (
                "Unexpected error during order creation.",
                ("could not create", "order"),
                ("Unexpected error during order creation",),
            ),
            (
                "No available packages with quoted service",
                ("packages", "quote"),
                ("No available packages",),
            ),
            (
                "Currency not found",
                ("currency",),
                ("Currency not found",),
            ),
            (
                "Carrier print options not found",
                ("print", "service"),
                ("Carrier print options not found",),
            ),
            (
                "Carrier print option not found",
                ("print", "service"),
                ("Carrier print option not found",),
            ),
            (
                "Label generation failed.",
                ("carrier", "label"),
                ("Label generation failed",),
            ),
            (
                "User token expired.",
                ("expired", "reconnect"),
                ("User token expired",),
            ),
            (
                "User token is invalid.",
                ("invalid", "token"),
                ("User token is invalid",),
            ),
            (
                "Invalid operation.",
                ("could not complete",),
                ("Invalid operation",),
            ),
            (
                "Invalid request payload/params input",
                ("request", "invalid"),
                ("Invalid request payload",),
            ),
            (
                "424 - The body data is invalid. Please check the docs",
                ("request", "invalid", "quote"),
                ("body data is invalid", "Please check the docs"),
            ),
            (
                "timeout of 60000ms exceeded",
                ("timed out", "try again"),
                ("timeout of 60000ms",),
            ),
            (
                "Request failed with status code 400",
                ("carrier", "rejected"),
                ("Request failed with status code",),
            ),
            (
                "Cannot read properties of undefined (reading 'name')",
                ("incomplete", "order"),
                ("Cannot read properties",),
            ),
        )
        for api_message, must_include, must_exclude in cases:
            with self.subTest(api_message=api_message):
                with self.assertRaises(UserError) as error:
                    EnviaOfficialAdapter._parse_label_create_response(
                        {"status": False, "message": api_message}
                    )
                text = str(error.exception)
                folded = text.casefold()
                for needle in must_include:
                    self.assertIn(needle.casefold(), folded, text)
                for needle in must_exclude:
                    self.assertNotIn(needle, text)
                self.assertNotIn("did not generate a label", folded)
