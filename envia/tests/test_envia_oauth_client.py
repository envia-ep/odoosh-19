from unittest.mock import MagicMock, patch

from odoo.exceptions import UserError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase

from odoo.addons.envia.services.envia_oauth_client import (
    EnviaOauthClient,
    build_integration_popup_url,
    get_eshop_accesses_me_url,
    get_eshop_test_url,
    get_oauth_integration_url,
    get_oauth_popup_url,
)
from urllib.parse import parse_qs


@tagged("post_install", "-at_install")
class TestEnviaOauthClient(TransactionCase):
    def test_default_urls(self):
        self.assertTrue(get_oauth_integration_url(self.env).startswith("https://"))
        self.assertTrue(get_eshop_test_url(self.env).startswith("https://"))
        self.assertIn("oauth", get_oauth_integration_url(self.env))
        self.assertIn("test", get_eshop_test_url(self.env))

    @patch.dict(
        "os.environ",
        {
            "ENVIA_OAUTH_INTEGRATION_URL": "https://oauth.example.com/integration/odoo",
            "ENVIA_OAUTH_POPUP_URL": "https://oauth.example.com/popup?ecommerce=odoo",
            "ENVIA_ESHOP_TEST_URL": "https://eshop.example.com/api/v2/test",
        },
        clear=False,
    )
    def test_environment_variables_override_config(self):
        self.assertEqual(
            get_oauth_integration_url(self.env),
            "https://oauth.example.com/integration/odoo",
        )
        self.assertEqual(
            get_oauth_popup_url(self.env),
            "https://oauth.example.com/popup?ecommerce=odoo",
        )
        self.assertEqual(
            get_eshop_test_url(self.env),
            "https://eshop.example.com/api/v2/test",
        )

    def test_build_integration_popup_url(self):
        self.env["ir.config_parameter"].sudo().set_param(
            "web.base.url",
            "https://mitienda.odoo.com",
        )
        self.env["ir.config_parameter"].sudo().set_param(
            "envia.oauth_popup_url",
            "https://oauth-deve.herokuapp.com/j4CVuDzGDiA2sxu0YYOYndiE4XkonsFb?ecommerce=odoo",
        )
        popup_url = build_integration_popup_url(
            self.env,
            url="https://mitienda.odoo.com",
            database="mydb",
            email="user@correo.com",
            api_key="xxxxx",
        )
        self.assertIn("j4CVuDzGDiA2sxu0YYOYndiE4XkonsFb", popup_url)
        self.assertIn("ecommerce=odoo", popup_url)
        self.assertIn("state=fromPlugin", popup_url)
        self.assertIn("origin=envia_odoo", popup_url)
        self.assertIn("url=https%3A%2F%2Fmitienda.odoo.com", popup_url)
        self.assertIn("database=mydb", popup_url)
        self.assertIn("email=user%40correo.com", popup_url)
        self.assertIn("apiKey=xxxxx", popup_url)
        self.assertNotIn("callbackUrl=", popup_url)

    def test_build_integration_form_body_matches_postman_payload(self):
        form_body = EnviaOauthClient.build_integration_form_body(
            url="http://huge-part-benefits-trans.trycloudflare.com",
            database="odoo_dev",
            email="admin",
            api_key="1cf4a99f637851038313aa71675860efd3c101b",
            sandbox=False,
        )
        parsed = parse_qs(form_body)
        self.assertEqual(
            parsed,
            {
                "url": ["http://huge-part-benefits-trans.trycloudflare.com"],
                "database": ["odoo_dev"],
                "email": ["admin"],
                "apiKey": ["1cf4a99f637851038313aa71675860efd3c101b"],
                "sandbox": ["false"],
            },
        )

    def test_extract_access_token_from_store_response(self):
        token = "eyJhbGciOiJIUzUxMiIsInR5cCI6IkpXVCJ9.test.signature"
        body = {
            "success": True,
            "store": {
                "_id": "6a39b1c873a63db6d38c9b1f",
                "access_token": token,
                "access": {
                    "ecommerce": "Odoo",
                    "access_token": token,
                },
            },
        }
        self.assertEqual(EnviaOauthClient._extract_access_token(body), token)

    def test_extract_store_access_info_reads_version_from_store_access(self):
        body = {
            "success": True,
            "store": {
                "_id": "6a39b1c873a63db6d38c9b1f",
                "access": {
                    "version": "19.0.1.32.0",
                    "url": "http://odoo.example.com",
                    "database": "odoo_dev",
                    "email": "admin",
                },
            },
        }
        info = EnviaOauthClient.extract_store_access_info(body)
        self.assertEqual(info["version"], "19.0.1.32.0")
        self.assertEqual(info["database"], "odoo_dev")

    def test_extract_store_access_info_ignores_na_version(self):
        body = {
            "success": True,
            "store": {
                "access": {
                    "version": "N/A",
                }
            },
        }
        info = EnviaOauthClient.extract_store_access_info(body)
        self.assertFalse(info["version"])

    def test_extract_shipping_api_token_from_store_access(self):
        body = {
            "success": True,
            "store": {
                "access_token": "oauth-jwt-token",
                "access": {
                    "access_token": "oauth-jwt-token",
                    "api_token": "envia-shipping-token-123",
                },
            },
        }
        self.assertEqual(
            EnviaOauthClient.extract_shipping_api_token(body),
            "envia-shipping-token-123",
        )
        info = EnviaOauthClient.extract_store_access_info(body)
        self.assertEqual(info["shipping_api_token"], "envia-shipping-token-123")

    @patch("odoo.addons.envia.services.envia_oauth_client.requests.get")
    def test_fetch_store_access(self, mock_get):
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "success": True,
                "store": {
                    "access": {
                        "version": "N/A",
                    }
                },
            },
            text='{"success": true}',
        )
        client = EnviaOauthClient(self.env)
        info = client.fetch_store_access("envia-access-token-789")
        self.assertFalse(info["version"])
        mock_get.assert_called_once_with(
            get_eshop_accesses_me_url(self.env),
            headers={"Authorization": "envia-access-token-789"},
            timeout=60,
        )

    @patch("odoo.addons.envia.services.envia_oauth_client.requests.post")
    @patch("odoo.addons.envia.services.envia_oauth_client.requests.get")
    def test_register_and_verify_integration_with_store_response(self, mock_get, mock_post):
        token = "envia-jwt-access-token"
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "success": True,
                "store": {
                    "access_token": token,
                    "access": {"access_token": token},
                },
            },
            text='{"success": true}',
        )
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"success": True},
            text='{"success": true}',
        )

        client = EnviaOauthClient(self.env)
        response = client.register_odoo_integration(
            url="http://huge-part-benefits-trans.trycloudflare.com",
            database="odoo_dev",
            email="admin",
            api_key="abc123",
            sandbox=False,
        )
        self.assertTrue(response["success"])
        self.assertTrue(client.verify_integration())
        mock_get.assert_called_once_with(
            get_eshop_test_url(self.env),
            headers={"Authorization": token},
            timeout=60,
        )

    @patch("odoo.addons.envia.services.envia_oauth_client.requests.post")
    @patch("odoo.addons.envia.services.envia_oauth_client.requests.get")
    def test_register_and_verify_integration(self, mock_get, mock_post):
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {"status": "ok", "access_token": "envia-access-token-123"},
            text='{"status": "ok", "access_token": "envia-access-token-123"}',
        )
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"success": True},
            text='{"success": true}',
        )

        client = EnviaOauthClient(self.env)
        self.env["ir.config_parameter"].sudo().set_param("web.base.url", "https://odoo.example.com")
        response = client.register_odoo_integration(
            url="https://odoo.example.com",
            database="odoo_dev",
            email="admin@example.com",
            api_key="abc123",
            sandbox=False,
        )
        self.assertEqual(response["status"], "ok")
        expected_body = EnviaOauthClient.build_integration_form_body(
            url="https://odoo.example.com",
            database="odoo_dev",
            email="admin@example.com",
            api_key="abc123",
            sandbox=False,
        )
        mock_post.assert_called_once_with(
            get_oauth_integration_url(self.env),
            data=expected_body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=60,
        )
        self.assertTrue(client.verify_integration())
        mock_get.assert_called_once_with(
            get_eshop_test_url(self.env),
            headers={"Authorization": "envia-access-token-123"},
            timeout=60,
        )

    @patch("odoo.addons.envia.services.envia_oauth_client.requests.get")
    @patch("odoo.addons.envia.services.envia_oauth_client.requests.post")
    def test_verify_integration_fails_when_success_is_false(self, mock_post, mock_get):
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {"access_token": "envia-access-token-456"},
            text='{"access_token": "envia-access-token-456"}',
        )
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"success": False},
            text='{"success": false}',
        )

        client = EnviaOauthClient(self.env)
        client.register_odoo_integration(
            url="https://odoo.example.com",
            database="odoo_dev",
            email="admin@example.com",
            api_key="abc123",
            sandbox=False,
        )
        self.assertFalse(client.verify_integration())

    @patch("odoo.addons.envia.services.envia_oauth_client.requests.post")
    def test_register_integration_raises_when_access_token_is_missing(self, mock_post):
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {"status": "ok"},
            text='{"status": "ok"}',
        )
        client = EnviaOauthClient(self.env)
        with self.assertRaises(UserError):
            client.register_odoo_integration(
                url="https://odoo.example.com",
                database="odoo_dev",
                email="admin@example.com",
                api_key="abc123",
                sandbox=False,
            )

    def test_extract_response_message_from_html_error_page(self):
        response = MagicMock(
            status_code=401,
            text='<!DOCTYPE html><p class="err-msg">Credenciales inválidas en Odoo</p>',
        )
        message = EnviaOauthClient._extract_response_message(response, response.text)
        self.assertEqual(message, "Credenciales inválidas en Odoo")

    @patch("odoo.addons.envia.services.envia_oauth_client.requests.post")
    def test_register_integration_raises_on_http_error(self, mock_post):
        mock_post.return_value = MagicMock(
            status_code=400,
            json=lambda: {"message": "Invalid payload"},
            text="Invalid payload",
        )
        client = EnviaOauthClient(self.env)
        with self.assertRaises(UserError):
            client.register_odoo_integration(
                url="https://odoo.example.com",
                database="odoo_dev",
                email="admin@example.com",
                api_key="abc123",
                sandbox=False,
            )
