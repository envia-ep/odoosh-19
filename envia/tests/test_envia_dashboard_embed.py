import base64
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from odoo.tests import tagged
from odoo.tests.common import TransactionCase

from odoo.addons.envia.const import (
    ENVIA_ECOMMERCE_EMBED_URL_PRODUCTION,
    ENVIA_ECOMMERCE_EMBED_URL_SANDBOX,
    get_envia_dashboard_embed_url,
    get_envia_ecommerce_embed_base_url,
)


@tagged("post_install", "-at_install")
class TestEnviaDashboardEmbed(TransactionCase):
    def test_dashboard_action_requires_oauth_connection(self):
        company = self.env.company
        company.envia_oauth_connected = False
        action = self.env["res.company"].action_open_envia_dashboard()
        self.assertEqual(action["type"], "ir.actions.client")
        self.assertEqual(action["tag"], "display_notification")

    def test_dashboard_action_opens_ecommerce_iframe(self):
        company = self.env.company
        company.envia_oauth_connected = True
        company.envia_company_id = "70279"
        company.envia_shop_id = "shop-1"
        self.env["ir.config_parameter"].sudo().set_param(
            "web.base.url", "https://shop.example.com"
        )
        with patch(
            "odoo.addons.envia.services.envia_config.resolve_envia_environment",
            return_value="production",
        ):
            action = self.env["res.company"].action_open_envia_dashboard()
        self.assertEqual(action["type"], "ir.actions.client")
        self.assertEqual(action["tag"], "envia_dashboard")
        self.assertEqual(action["target"], "current")
        url = action["params"]["url"]
        self.assertTrue(url.startswith(f"{ENVIA_ECOMMERCE_EMBED_URL_PRODUCTION}?"))
        self.assertNotIn("accounts.envia.com", url)
        query = parse_qs(urlsplit(url).query)
        self.assertEqual(
            base64.b64decode(query["hash"][0]).decode(),
            "https://shop.example.com:70279:shop-1",
        )

    def test_dashboard_embed_host_follows_environment(self):
        self.assertEqual(
            get_envia_ecommerce_embed_base_url("production"),
            "https://shipping.envia.com/ecommerce",
        )
        self.assertEqual(
            get_envia_ecommerce_embed_base_url("sandbox"),
            "https://shipping-test.envia.com/ecommerce",
        )
        prod = get_envia_dashboard_embed_url(
            "https://shop.example.com", "1", "2", environment="production"
        )
        sandbox = get_envia_dashboard_embed_url(
            "https://shop.example.com", "1", "2", environment="sandbox"
        )
        self.assertTrue(prod.startswith(f"{ENVIA_ECOMMERCE_EMBED_URL_PRODUCTION}?"))
        self.assertTrue(sandbox.startswith(f"{ENVIA_ECOMMERCE_EMBED_URL_SANDBOX}?"))
