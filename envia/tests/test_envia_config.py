from unittest.mock import patch

from odoo.tests import tagged
from odoo.tests.common import TransactionCase

from odoo.addons.envia.services.envia_config import (
    DEFAULT_CHECKOUT_PATH,
    DEFAULT_ECOMMERCE_PRIVATE_BASE_URL,
    DEFAULT_PACKAGE_DIMENSIONS_PATH,
    ENVIA_ENVIRONMENT_PARAM,
    get_envia_api_base_url,
    get_envia_checkout_path,
    get_envia_ecommerce_private_base_url,
    get_envia_environment_from_config,
    get_envia_package_dimensions_path,
    get_envia_queries_base_url,
    is_envia_sandbox,
    oauth_registration_sandbox,
    resolve_envia_environment,
)
from odoo.addons.envia.services.payload_mapper import get_envia_adapter


@tagged("post_install", "-at_install")
class TestEnviaConfig(TransactionCase):
    def test_envia_environment_from_config_reads_system_parameter(self):
        icp = self.env["ir.config_parameter"].sudo()
        icp.set_param(ENVIA_ENVIRONMENT_PARAM, "production")
        self.assertEqual(get_envia_environment_from_config(self.env), "production")
        icp.set_param(ENVIA_ENVIRONMENT_PARAM, "sandbox")
        self.assertEqual(get_envia_environment_from_config(self.env), "sandbox")

    def test_resolve_envia_environment_defaults_to_production(self):
        company = self.env.company
        company.envia_environment = "sandbox"
        with patch.dict("os.environ", {"ENVIA_ENVIRONMENT": ""}, clear=False):
            self.assertEqual(resolve_envia_environment(company), "production")
            self.assertFalse(is_envia_sandbox(company))

    def test_resolve_envia_environment_ignores_company_and_config(self):
        company = self.env.company
        self.env["ir.config_parameter"].sudo().set_param(ENVIA_ENVIRONMENT_PARAM, "sandbox")
        company.envia_environment = "sandbox"
        with patch.dict("os.environ", {"ENVIA_ENVIRONMENT": ""}, clear=False):
            self.assertEqual(resolve_envia_environment(company), "production")

    def test_new_company_default_envia_environment_from_config(self):
        self.env["ir.config_parameter"].sudo().set_param(ENVIA_ENVIRONMENT_PARAM, "production")
        company = self.env["res.company"].create({"name": "Envia Config Test Co"})
        self.assertEqual(company.envia_environment, "production")

    @patch.dict("os.environ", {"ENVIA_ENVIRONMENT": "production"}, clear=False)
    def test_envia_environment_env_overrides_company_field(self):
        company = self.env.company
        company.envia_environment = "sandbox"
        self.assertEqual(resolve_envia_environment(company), "production")
        self.assertFalse(is_envia_sandbox(company))

    @patch.dict(
        "os.environ",
        {"ENVIA_ENVIRONMENT": "sandbox", "ENVIA_API_BASE_URL": "", "ENVIA_QUERIES_BASE_URL": ""},
        clear=False,
    )
    def test_envia_environment_env_forces_sandbox_urls(self):
        company = self.env.company
        company.envia_environment = "production"
        self.assertEqual(get_envia_api_base_url(company), "https://api-test.envia.com/")
        self.assertEqual(get_envia_queries_base_url(company), "https://queries.test.envia.com/")

    @patch.dict(
        "os.environ",
        {"ENVIA_ENVIRONMENT": "", "ENVIA_API_BASE_URL": "", "ENVIA_QUERIES_BASE_URL": ""},
        clear=False,
    )
    def test_resolve_defaults_to_production_urls(self):
        company = self.env.company
        company.write({"envia_environment": "sandbox", "envia_base_url": False})
        self.assertEqual(get_envia_api_base_url(company), "https://api-clients.envia.com/")
        self.assertEqual(get_envia_queries_base_url(company), "https://queries.envia.com/")

    @patch.dict(
        "os.environ",
        {
            "ENVIA_API_BASE_URL": "https://api-custom.example.com",
            "ENVIA_QUERIES_BASE_URL": "https://queries-custom.example.com",
        },
        clear=False,
    )
    def test_api_urls_env_override_company_and_environment(self):
        company = self.env.company
        company.write(
            {
                "envia_environment": "sandbox",
                "envia_base_url": "https://company.example.com",
            }
        )
        self.assertEqual(get_envia_api_base_url(company), "https://api-custom.example.com/")
        self.assertEqual(
            get_envia_queries_base_url(company),
            "https://queries-custom.example.com/",
        )

    @patch.dict("os.environ", {"ENVIA_CHECKOUT_PATH": ""}, clear=False)
    def test_checkout_path_default(self):
        self.assertEqual(get_envia_checkout_path("34084"), DEFAULT_CHECKOUT_PATH.format(shop_id="34084"))

    @patch.dict("os.environ", {"ENVIA_CHECKOUT_PATH": "v3/checkout/odoo/{shop_id}"}, clear=False)
    def test_checkout_path_env_override(self):
        self.assertEqual(get_envia_checkout_path("34084"), "v3/checkout/odoo/34084")

    @patch.dict(
        "os.environ",
        {"ENVIA_ECOMMERCE_PRIVATE_BASE_URL": "", "ENVIA_PACKAGE_DIMENSIONS_PATH": ""},
        clear=False,
    )
    def test_ecommerce_private_base_url_defaults_without_env(self):
        self.assertEqual(
            get_envia_ecommerce_private_base_url(),
            DEFAULT_ECOMMERCE_PRIVATE_BASE_URL,
        )
        self.assertEqual(
            get_envia_package_dimensions_path("34084"),
            DEFAULT_PACKAGE_DIMENSIONS_PATH.format(shop_id="34084"),
        )

    @patch.dict(
        "os.environ",
        {
            "ENVIA_ECOMMERCE_PRIVATE_BASE_URL": "https://ecommerce-private-prod.example.com",
            "ENVIA_PACKAGE_DIMENSIONS_PATH": "package/dimensions/{shop_id}",
        },
        clear=False,
    )
    def test_package_dimensions_url_env_override(self):
        self.assertEqual(
            get_envia_ecommerce_private_base_url(),
            "https://ecommerce-private-prod.example.com/",
        )
        self.assertEqual(
            get_envia_package_dimensions_path("34084"),
            "package/dimensions/34084",
        )

    @patch.dict("os.environ", {"ENVIA_API_BASE_URL": "", "ENVIA_QUERIES_BASE_URL": ""}, clear=False)
    def test_company_base_url_override_takes_priority_over_environment(self):
        company = self.env.company
        company.write(
            {
                "envia_environment": "sandbox",
                "envia_base_url": "https://custom.example.com",
            }
        )
        self.assertEqual(get_envia_api_base_url(company), "https://custom.example.com/")

    @patch.dict(
        "os.environ",
        {"ENVIA_ENVIRONMENT": "production", "ENVIA_API_BASE_URL": "", "ENVIA_QUERIES_BASE_URL": ""},
        clear=False,
    )
    def test_get_envia_adapter_uses_production_api_when_env_is_set(self):
        company = self.env.company
        company.write(
            {
                "envia_environment": "sandbox",
                "envia_api_token": "envia-shipping-token-456",
                "envia_shop_id": "34084",
            }
        )
        adapter = get_envia_adapter(company)
        self.assertEqual(adapter.client.base_url, "https://api-clients.envia.com/")

    def test_oauth_registration_sandbox_is_always_false(self):
        self.assertFalse(oauth_registration_sandbox())

    def test_default_origin_partner_ignores_warehouse_setting(self):
        """Temporary: Envia default-origin settings are not used."""
        company = self.env.company
        warehouse = self.env["stock.warehouse"].search(
            [("company_id", "=", company.id)],
            limit=1,
        )
        self.assertTrue(warehouse)
        company.write(
            {
                "envia_default_origin_warehouse_id": warehouse.id,
                "envia_default_origin_partner_id": False,
            }
        )
        self.assertEqual(company._envia_get_default_origin_partner(), company.partner_id)

    def test_default_origin_partner_falls_back_to_company(self):
        company = self.env.company
        company.write(
            {
                "envia_default_origin_warehouse_id": False,
                "envia_default_origin_partner_id": False,
            }
        )
        self.assertEqual(company._envia_get_default_origin_partner(), company.partner_id)
