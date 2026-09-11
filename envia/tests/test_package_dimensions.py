from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase

from odoo.addons.envia.services.dto import ShipmentItem
from odoo.addons.envia.services.envia_client import EnviaClient
from odoo.addons.envia.services.envia_official_adapter import EnviaOfficialAdapter


@tagged("post_install", "-at_install")
class TestPackageDimensionsFetch(TransactionCase):
    @patch.dict(
        "os.environ",
        {"ENVIA_ECOMMERCE_PRIVATE_BASE_URL": "https://ecommerce-private.test/"},
        clear=False,
    )
    def test_fetch_package_dimensions_posts_to_ecommerce_private_base(self):
        shipping_client = EnviaClient("https://api-test.envia.com/", "shipping-token")
        adapter = EnviaOfficialAdapter(shipping_client, shop_id="34084")
        items = [
            ShipmentItem(
                description="Classic Leather Belt",
                quantity=1,
                price=100.0,
                currency="MXN",
                weight=0.5,
                product_id=82,
            )
        ]
        body = {
            "success": True,
            "packages": [
                {
                    "name": "Package",
                    "height": 17,
                    "width": 17,
                    "length": 17,
                    "weight": 1,
                    "content": "Classic Leather Belt",
                    "length_unit": "CM",
                    "weight_unit": "KG",
                }
            ],
            "message": "Package Default",
            "package_automatic": True,
        }
        with patch(
            "odoo.addons.envia.services.envia_official_adapter.EnviaClient"
        ) as mock_client_cls:
            mock_client_cls.return_value._post.return_value = body
            preview, hint = adapter.fetch_package_dimensions(
                items,
                "MXN",
                odoo_weight=1.0,
                auth_token="envia-api-token",
            )
        mock_client_cls.assert_called_once_with(
            "https://ecommerce-private.test/",
            "envia-api-token",
        )
        path, payload = mock_client_cls.return_value._post.call_args.args[:2]
        self.assertEqual(path, "package/dimensions/test/34084")
        self.assertEqual(payload["items"][0]["productId"], "82")
        self.assertIn("17x17x17 CM", preview)
        self.assertIn("Classic Leather Belt", preview)
        self.assertIn("0.5 KG (Odoo)", preview)
        # Item weight 0.5 vs Envia package weight 1 → sync hint.
        self.assertIn("sync package dimensions", hint.lower())

    @patch.dict(
        "os.environ",
        {"ENVIA_ECOMMERCE_PRIVATE_BASE_URL": "https://ecommerce-private.test/"},
        clear=False,
    )
    def test_fetch_package_dimensions_lists_odoo_items_instead_of_multiple_products(self):
        shipping_client = EnviaClient("https://api-test.envia.com/", "shipping-token")
        adapter = EnviaOfficialAdapter(shipping_client, shop_id="34084")
        items = [
            ShipmentItem(
                description="Classic Leather Belt",
                quantity=1,
                price=100.0,
                currency="MXN",
                weight=1.0,
                product_id=82,
            ),
            ShipmentItem(
                description="Australian healing clay",
                quantity=1,
                price=50.0,
                currency="MXN",
                weight=5.0,
                product_id=463,
            ),
        ]
        body = {
            "success": True,
            "packages": [
                {
                    "name": "Package",
                    "height": 29,
                    "width": 29,
                    "length": 29,
                    "weight": 5,
                    "content": "Multiple products",
                    "length_unit": "CM",
                    "weight_unit": "KG",
                }
            ],
        }
        with patch(
            "odoo.addons.envia.services.envia_official_adapter.EnviaClient"
        ) as mock_client_cls:
            mock_client_cls.return_value._post.return_value = body
            preview, hint = adapter.fetch_package_dimensions(
                items,
                "MXN",
                odoo_weight=6.0,
                auth_token="envia-api-token",
            )
        self.assertEqual(
            preview,
            "Package: 29x29x29 CM, 5 KG\n"
            "• Classic Leather Belt — 1 KG (Odoo)\n"
            "• Australian healing clay — 5 KG (Odoo)",
        )
        self.assertNotIn("Multiple products", preview)
        self.assertIn("differs from Odoo", hint)

    @patch.dict(
        "os.environ",
        {"ENVIA_ECOMMERCE_PRIVATE_BASE_URL": "https://ecommerce-private.test/"},
        clear=False,
    )
    def test_fetch_package_dimensions_uses_adapter_shipping_token_by_default(self):
        client = EnviaClient("https://api-test.envia.com/", "shipping-api-token")
        adapter = EnviaOfficialAdapter(client, shop_id="34084")
        with patch(
            "odoo.addons.envia.services.envia_official_adapter.EnviaClient"
        ) as mock_client_cls:
            mock_client_cls.return_value._post.return_value = {
                "packages": [],
                "package_automatic": False,
            }
            adapter.fetch_package_dimensions([], "MXN")
        mock_client_cls.assert_called_once_with(
            "https://ecommerce-private.test/",
            "shipping-api-token",
        )

    @patch.dict(
        "os.environ",
        {"ENVIA_ECOMMERCE_PRIVATE_BASE_URL": "https://ecommerce-private.test/"},
        clear=False,
    )
    def test_fetch_package_dimensions_soft_fails_on_api_error(self):
        client = EnviaClient("https://api-test.envia.com/", "shipping-token")
        adapter = EnviaOfficialAdapter(client, shop_id="34084")
        with patch(
            "odoo.addons.envia.services.envia_official_adapter.EnviaClient"
        ) as mock_client_cls:
            mock_client_cls.return_value._post.side_effect = UserError("boom")
            preview, hint = adapter.fetch_package_dimensions(
                [],
                "MXN",
                auth_token="envia-api-token",
            )
        self.assertEqual(preview, "")
        self.assertIn("Could not load Envia package dimensions preview", hint)

    def test_company_package_dimensions_token_uses_envia_api_token(self):
        company = self.env.company
        company.write(
            {
                "envia_api_token": "shipping-api-token",
                "envia_oauth_access_token": "oauth-access-token",
            }
        )
        self.assertEqual(
            company._envia_get_package_dimensions_token(),
            "shipping-api-token",
        )
