from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase

from odoo.addons.envia.services.payload_mapper import get_envia_adapter


@tagged("post_install", "-at_install")
class TestEnviaApiTokenResolution(TransactionCase):
    @patch.dict(
        "os.environ",
        {"ENVIA_ENVIRONMENT": "", "ENVIA_API_BASE_URL": "", "ENVIA_QUERIES_BASE_URL": ""},
        clear=False,
    )
    def test_get_envia_adapter_uses_official_api_with_shipping_token(self):
        company = self.env.company
        company.write(
            {
                "envia_oauth_connected": True,
                "envia_oauth_access_token": "oauth-access-token-123",
                "envia_api_token": "envia-shipping-token-456",
                "envia_shop_id": "34084",
                "envia_base_url": False,
            }
        )
        adapter = get_envia_adapter(company)
        self.assertEqual(adapter.client.base_url, "https://api-clients.envia.com/")
        self.assertEqual(adapter.client.token, "envia-shipping-token-456")
        self.assertEqual(adapter.shop_id, "34084")
        self.assertTrue(adapter.client.use_bearer_auth)

    def test_get_envia_adapter_raises_when_shop_id_is_missing(self):
        company = self.env.company
        company.write(
            {
                "envia_api_token": "envia-shipping-token-456",
                "envia_shop_id": False,
            }
        )
        with self.assertRaises(UserError):
            get_envia_adapter(company)

    def test_get_envia_adapter_uses_only_envia_api_token_not_oauth_jwt(self):
        company = self.env.company
        company.write(
            {
                "envia_oauth_connected": True,
                "envia_oauth_access_token": "oauth-access-token-123",
                "envia_api_token": False,
            }
        )
        with self.assertRaises(UserError):
            get_envia_adapter(company)

    def test_get_envia_adapter_raises_when_no_credentials_are_available(self):
        company = self.env.company
        company.write(
            {
                "envia_oauth_connected": False,
                "envia_oauth_access_token": False,
                "envia_api_token": False,
            }
        )
        with self.assertRaises(UserError):
            get_envia_adapter(company)
