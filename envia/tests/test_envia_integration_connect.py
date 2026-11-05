from odoo.tests import tagged
from odoo.tests.common import TransactionCase

from odoo.addons.envia.controllers.envia_integration import EnviaIntegrationController
from odoo.addons.envia.services.envia_integration_callback import (
    build_connect_url,
    resolve_connect_database,
)
from odoo.addons.envia.services.envia_plugin_setup import generate_integration_credentials


@tagged("post_install", "-at_install")
class TestEnviaIntegrationConnect(TransactionCase):
    def setUp(self):
        super().setUp()
        self.test_user = self.env.ref("base.user_admin")
        self.credentials = generate_integration_credentials(
            self.env,
            self.env.company,
            user=self.test_user,
        )

    def test_integration_connect_stores_envia_token_and_shop(self):
        payload = {
            "status": "active",
            "hash": "envia-shipping-api-token-xyz",
            "shop": "114865",
            "company": 8842,
            "user": 5430,
            "apiKey": self.credentials["api_key"],
        }
        self.env.company.sudo().write({"envia_integration_api_key": False})
        self.env["ir.config_parameter"].sudo().set_param(
            "envia.pending_plugin_setup_company_id",
            "",
        )
        result = EnviaIntegrationController._process_integration_connect(
            self.env.cr.dbname,
            payload,
            self.credentials["api_key"],
        )
        # Process commits in another cursor; assert response only (persistence: callback tests).
        self.assertTrue(result["ok"])
        self.assertEqual(result["shop"], "114865")
        self.assertNotEqual(5430, self.test_user.id)

    def test_resolve_connect_database_finds_database_from_api_key(self):
        database = resolve_connect_database(self.credentials["api_key"])
        self.assertEqual(database, self.env.cr.dbname)

    def test_resolve_connect_database_prefers_db_query_param(self):
        database = resolve_connect_database("unused-key", self.env.cr.dbname)
        self.assertEqual(database, self.env.cr.dbname)

    def test_build_connect_url_includes_database_query_param(self):
        self.env["ir.config_parameter"].sudo().set_param(
            "web.base.url",
            "https://odoo.example.com",
        )
        self.assertEqual(
            build_connect_url(self.env),
            f"https://odoo.example.com/envia/integration/connect?db={self.env.cr.dbname}",
        )
