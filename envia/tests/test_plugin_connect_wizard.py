from unittest.mock import MagicMock, patch

from odoo.exceptions import UserError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase

from odoo.addons.envia.services.envia_plugin_setup import (
    DEFAULT_ENVIA_API_KEY_NAME,
    PENDING_SETUP_PARAM,
    generate_integration_credentials,
    queue_pending_setup,
)


def _mock_oauth_success(mock_get, mock_post, plugin_version="19.0.1.32.0"):
    mock_post.return_value = MagicMock(
        status_code=200,
        json=lambda: {
            "status": "ok",
            "access_token": "envia-access-token-test",
            "store": {
                "access_token": "envia-access-token-test",
                "access": {
                    "access_token": "envia-access-token-test",
                    "api_token": "envia-shipping-token-test",
                },
            },
        },
        text='{"status": "ok", "access_token": "envia-access-token-test"}',
    )

    def get_side_effect(url, **kwargs):
        if "accesses/me" in url:
            return MagicMock(
                status_code=200,
                json=lambda: {
                    "success": True,
                    "store": {
                        "access": {
                            "version": plugin_version,
                        }
                    },
                },
                text='{"success": true}',
            )
        return MagicMock(
            status_code=200,
            json=lambda: {"success": True},
            text='{"success": true}',
        )

    mock_get.side_effect = get_side_effect


@tagged("post_install", "-at_install")
class TestEnviaPluginConnectWizard(TransactionCase):
    def _configure_shipping_api(self, token="envia-shipping-token-test"):
        self.env.company.write({"envia_api_token": token})
        self.env.flush_all()

    def _reset_shipping_config(self):
        self.env.company.write(
            {
                "envia_api_token": False,
                "envia_oauth_connected": False,
                "envia_oauth_access_token": False,
            }
        )
        self.env.flush_all()

    def setUp(self):
        super().setUp()
        self._oauth_commit_patcher = patch(
            "odoo.addons.envia.wizards.envia_plugin_connect_wizard"
            ".EnviaPluginConnectWizard._commit_for_external_oauth_validation",
            lambda self: self.env.flush_all(),
        )
        self._oauth_commit_patcher.start()
        self.addCleanup(self._oauth_commit_patcher.stop)

    def test_create_wizard_includes_integration_popup_url(self):
        wizard = self.env["envia.plugin.connect.wizard"].create_wizard_for_company(self.env.company)
        self.assertTrue(wizard.external_popup_url)
        self.assertIn("apiKey=", wizard.external_popup_url)
        self.assertFalse(wizard.integration_use_sized_popup)

    def test_commits_before_oauth_registration(self):
        wizard = self.env["envia.plugin.connect.wizard"].create_wizard_for_company(self.env.company)
        with patch.object(
            type(wizard),
            "_commit_for_external_oauth_validation",
            autospec=True,
        ) as commit_mock:
            action = wizard.action_run_integration()
            commit_mock.assert_called_once()
            wizard.invalidate_recordset()
            self.assertEqual(wizard.state, "waiting_external")
            self.assertTrue(wizard.external_popup_url)
            self.assertIn("apiKey=", wizard.external_popup_url)
            self.assertFalse(action)

    def test_get_integration_url(self):
        wizard = self.env["envia.plugin.connect.wizard"].create_wizard_for_company(self.env.company)
        wizard.write({"external_popup_url": "https://oauth.example.com/connect?apiKey=test"})
        self.assertIn("apiKey=test", wizard.get_integration_url())

    def test_get_integration_url_action_opens_new_tab(self):
        wizard = self.env["envia.plugin.connect.wizard"].create_wizard_for_company(self.env.company)
        wizard.write({"external_popup_url": "https://oauth.example.com/connect?apiKey=test"})
        action = wizard.get_integration_url_action()
        self.assertEqual(action["type"], "ir.actions.act_url")
        self.assertEqual(action["target"], "new")
        self.assertIn("apiKey=test", action["url"])

    def test_sized_popup_setting_exposed_on_wizard(self):
        self.env["ir.config_parameter"].sudo().set_param("envia.oauth_use_sized_popup", "True")
        wizard = self.env["envia.plugin.connect.wizard"].create_wizard_for_company(self.env.company)
        self.assertTrue(wizard.integration_use_sized_popup)

    def test_open_envia_integration_returns_false(self):
        wizard = self.env["envia.plugin.connect.wizard"].create_wizard_for_company(self.env.company)
        wizard.write({"state": "waiting_external"})
        self.assertFalse(wizard.action_open_envia_integration())

    def test_external_popup_closed_resets_wizard_to_ready(self):
        self._reset_shipping_config()
        wizard = self.env["envia.plugin.connect.wizard"].create_wizard_for_company(self.env.company)
        wizard.write(
            {
                "state": "waiting_external",
                "external_popup_url": "https://oauth.example.com/popup",
            }
        )
        with patch.object(
            type(wizard),
            "_execute_oauth_integration",
            side_effect=UserError("OAuth skipped in test"),
        ):
            wizard.action_on_external_popup_closed()
        wizard.invalidate_recordset()
        self.assertEqual(wizard.state, "ready")
        self.assertFalse(wizard.external_popup_url)

    def test_poll_integration_status_marks_success_when_token_saved(self):
        wizard = self.env["envia.plugin.connect.wizard"].create_wizard_for_company(self.env.company)
        wizard.write({"state": "waiting_external"})
        self.env.company.write(
            {
                "envia_api_token": "saved-by-envia-callback",
                "envia_oauth_connected": True,
            }
        )
        action = wizard.action_poll_integration_status()
        wizard.invalidate_recordset()
        self.assertEqual(wizard.state, "success")
        self.assertEqual(action["res_model"], "res.config.settings")

    def test_return_to_connect_screen_keeps_ready_state(self):
        wizard = self.env["envia.plugin.connect.wizard"].create_wizard_for_company(self.env.company)
        action = wizard.action_return_to_connect_screen()
        self.assertEqual(wizard.state, "ready")
        self.assertEqual(action["res_model"], "envia.plugin.connect.wizard")
        self.assertEqual(action["res_id"], wizard.id)
        self.assertEqual(action["target"], "current")

    def test_go_to_plugin_settings_opens_envia_app(self):
        wizard = self.env["envia.plugin.connect.wizard"].create_wizard_for_company(self.env.company)
        action = wizard.action_go_to_plugin_settings()
        self.assertEqual(action["res_model"], "res.config.settings")
        context = action["context"]
        if isinstance(context, str):
            self.assertIn("envia", context)
        else:
            self.assertEqual(context.get("module"), "envia")

    @patch("odoo.addons.envia.services.envia_oauth_client.requests.get")
    @patch("odoo.addons.envia.services.envia_oauth_client.requests.post")
    def test_create_wizard_for_current_admin(self, mock_post, mock_get):
        _mock_oauth_success(mock_get, mock_post)
        company = self.env.company
        wizard = self.env["envia.plugin.connect.wizard"].create_wizard_for_company(company)
        self.assertEqual(wizard.state, "ready")

        wizard.action_run_integration()
        wizard.invalidate_recordset()
        self.assertEqual(wizard.state, "waiting_external")
        wizard.write({"state": "ready"})
        wizard.action_finalize_integration()
        wizard.invalidate_recordset()

        self.assertEqual(wizard.state, "success")
        self.assertTrue(wizard.api_key)
        self.assertEqual(len(wizard.api_key), 40)
        self.assertEqual(wizard.user_id, self.env.user)
        self.assertEqual(wizard.user_email, self.env.user.login)
        self.assertTrue(company.envia_oauth_connected)
        self.assertEqual(wizard.plugin_version, "19.0.1.32.0")
        self.assertTrue(company.envia_oauth_access_token)
        self.assertEqual(company.envia_plugin_version, "19.0.1.32.0")
        self.assertEqual(company.envia_integration_api_key, wizard.api_key)
        self.assertEqual(company.envia_api_token, "envia-shipping-token-test")

        api_key_record = self.env["res.users.apikeys"].search(
            [
                ("user_id", "=", self.env.user.id),
                ("name", "=", DEFAULT_ENVIA_API_KEY_NAME),
            ],
            limit=1,
        )
        self.assertTrue(api_key_record)
        self.assertFalse(api_key_record.expiration_date)

    @patch("odoo.addons.envia.services.envia_oauth_client.requests.get")
    @patch("odoo.addons.envia.services.envia_oauth_client.requests.post")
    def test_regenerating_replaces_existing_envia_api_key(self, mock_post, mock_get):
        _mock_oauth_success(mock_get, mock_post)
        company = self.env.company
        first_wizard = self.env["envia.plugin.connect.wizard"].create_wizard_for_company(company)
        first_wizard.action_run_integration()
        first_wizard.action_finalize_integration()
        second_wizard = self.env["envia.plugin.connect.wizard"].create_wizard_for_company(company)
        second_wizard.action_run_integration()
        second_wizard.action_finalize_integration()

        api_key_records = self.env["res.users.apikeys"].search(
            [
                ("user_id", "=", self.env.user.id),
                ("name", "=", DEFAULT_ENVIA_API_KEY_NAME),
            ]
        )
        self.assertEqual(len(api_key_records), 1)
        self.assertNotEqual(first_wizard.api_key, second_wizard.api_key)

    def test_can_generate_expiring_key_when_requested(self):
        credentials = generate_integration_credentials(
            self.env,
            self.env.company,
            expiration_days=30,
        )
        api_key_record = self.env["res.users.apikeys"].search(
            [
                ("user_id", "=", self.env.user.id),
                ("name", "=", DEFAULT_ENVIA_API_KEY_NAME),
            ],
            order="id desc",
            limit=1,
        )
        self.assertTrue(credentials["api_key"])
        self.assertTrue(api_key_record.expiration_date)

    @patch("odoo.addons.envia.services.envia_oauth_client.requests.get")
    @patch("odoo.addons.envia.services.envia_oauth_client.requests.post")
    def test_pending_setup_generates_for_admin(self, mock_post, mock_get):
        _mock_oauth_success(mock_get, mock_post)
        self._reset_shipping_config()
        company = self.env.company
        queue_pending_setup(self.env, company)

        action = self.env["envia.plugin.connect.wizard"].action_envia_app_entry()
        self.assertTrue(action)
        self.assertEqual(action["type"], "ir.actions.act_window")
        self.assertEqual(action["res_model"], "envia.plugin.connect.wizard")
        self.assertEqual(action["target"], "current")

        wizard = self.env["envia.plugin.connect.wizard"].browse(action["res_id"])
        self.assertEqual(wizard.state, "ready")
        self.assertEqual(
            self.env["ir.config_parameter"].sudo().get_param(PENDING_SETUP_PARAM),
            str(company.id),
        )
        wizard.action_finalize_integration()
        self.assertEqual(wizard.state, "success")
        self.assertEqual(wizard.user_id, self.env.user)
        self.assertFalse(
            self.env["ir.config_parameter"].sudo().get_param(PENDING_SETUP_PARAM)
        )

    @patch("odoo.addons.envia.services.envia_oauth_client.requests.get")
    @patch("odoo.addons.envia.services.envia_oauth_client.requests.post")
    def test_refresh_token_reuses_stored_api_key_and_reconnects(self, mock_post, mock_get):
        _mock_oauth_success(mock_get, mock_post)
        wizard = self.env["envia.plugin.connect.wizard"].create_wizard_for_company(self.env.company)
        wizard.action_run_integration()
        wizard.action_finalize_integration()
        self.assertEqual(wizard.state, "success")
        previous_api_key = wizard.api_key
        self.assertEqual(self.env.company.envia_integration_api_key, previous_api_key)

        wizard.action_refresh_token()
        self.assertEqual(wizard.state, "waiting_external")
        self.assertTrue(wizard.external_popup_url)
        self.assertEqual(wizard.api_key, previous_api_key)

    def test_refresh_token_generates_api_key_when_missing(self):
        self.env.company.write(
            {
                "envia_oauth_connected": True,
                "envia_integration_api_key": False,
            }
        )
        wizard = self.env["envia.plugin.connect.wizard"]._create_connected_wizard(self.env.company)
        wizard.action_refresh_token()
        self.assertEqual(wizard.state, "waiting_external")
        self.assertTrue(self.env.company.envia_integration_api_key)
        self.assertTrue(wizard.api_key)

    @patch("odoo.addons.envia.services.envia_oauth_client.requests.get")
    @patch("odoo.addons.envia.services.envia_oauth_client.requests.post")
    def test_refresh_token_regenerates_revoked_api_key(self, mock_post, mock_get):
        _mock_oauth_success(mock_get, mock_post)
        wizard = self.env["envia.plugin.connect.wizard"].create_wizard_for_company(self.env.company)
        wizard.action_run_integration()
        wizard.action_finalize_integration()
        previous_api_key = self.env.company.envia_integration_api_key
        self.env.company.write({"envia_integration_api_key": "revoked-stale-key-12345678901234567890"})

        wizard.action_refresh_token()

        self.env.company.invalidate_recordset()
        self.assertEqual(wizard.state, "waiting_external")
        self.assertNotEqual(self.env.company.envia_integration_api_key, "revoked-stale-key-12345678901234567890")
        self.assertTrue(self.env.company._envia_integration_api_key_is_valid())

    def test_open_connect_wizard_opens_settings_when_already_linked(self):
        self.env.company.write(
            {
                "envia_oauth_connected": True,
                "envia_oauth_access_token": False,
            }
        )
        action = self.env["envia.plugin.connect.wizard"].action_open_connect_wizard()
        self.assertEqual(action["res_model"], "res.config.settings")

    @patch("odoo.addons.envia.services.envia_oauth_client.requests.get")
    def test_create_connected_wizard_shows_version_sync_hint_when_token_missing(self, mock_get):
        self.env.company.write(
            {
                "envia_oauth_connected": True,
                "envia_oauth_access_token": False,
                "envia_plugin_version": False,
            }
        )
        wizard = self.env["envia.plugin.connect.wizard"]._create_connected_wizard(self.env.company)
        self.assertEqual(wizard.state, "success")
        self.assertEqual(
            wizard.plugin_version_display,
            "Not synced — click Refresh token",
        )

    @patch("odoo.addons.envia.services.envia_oauth_client.requests.get")
    @patch("odoo.addons.envia.services.envia_oauth_client.requests.post")
    def test_create_connected_wizard_fetches_plugin_version(self, mock_post, mock_get):
        _mock_oauth_success(mock_get, mock_post, plugin_version="2.5.0")
        self.env.company.write(
            {
                "envia_oauth_connected": True,
                "envia_oauth_access_token": "stored-access-token",
                "envia_integration_api_key": False,
            }
        )
        wizard = self.env["envia.plugin.connect.wizard"]._create_connected_wizard(self.env.company)
        self.assertEqual(wizard.state, "success")
        self.assertEqual(wizard.plugin_version, "2.5.0")
        self.assertEqual(self.env.company.envia_plugin_version, "2.5.0")

    @patch("odoo.addons.envia.services.envia_oauth_client.requests.get")
    @patch("odoo.addons.envia.services.envia_oauth_client.requests.post")
    def test_success_wizard_keeps_api_key_after_connect(self, mock_post, mock_get):
        _mock_oauth_success(mock_get, mock_post)
        wizard = self.env["envia.plugin.connect.wizard"].create_wizard_for_company(self.env.company)
        generated_api_key = wizard.api_key
        wizard.action_run_integration()
        wizard.action_finalize_integration()
        self.assertEqual(wizard.state, "success")
        self.assertEqual(wizard.api_key, generated_api_key)
        self.assertEqual(len(wizard.api_key), 40)

    def test_non_admin_gets_notification_on_connect(self):
        user = self.env["res.users"].create(
            {
                "name": "Envia User Only",
                "login": "envia.user.only@test.local",
                "email": "envia.user.only@test.local",
                "password": "test-password-123",
                "group_ids": [(6, 0, [self.env.ref("envia.group_envia_user").id])],
            }
        )
        action = (
            self.env["envia.plugin.connect.wizard"]
            .with_user(user)
            .action_open_connect_wizard()
        )
        self.assertEqual(action["type"], "ir.actions.client")
        self.assertEqual(action["tag"], "display_notification")
        self.assertEqual(action["params"]["type"], "warning")

    def test_non_admin_pending_setup_shows_notification_and_keeps_flag(self):
        self._reset_shipping_config()
        user = self.env["res.users"].create(
            {
                "name": "Envia User Only Pending",
                "login": "envia.user.pending@test.local",
                "email": "envia.user.pending@test.local",
                "password": "test-password-123",
                "group_ids": [(6, 0, [self.env.ref("envia.group_envia_user").id])],
            }
        )
        queue_pending_setup(self.env, self.env.company)
        action = (
            self.env["envia.plugin.connect.wizard"]
            .with_user(user)
            .action_envia_app_entry()
        )
        self.assertEqual(action["tag"], "display_notification")
        self.assertEqual(
            self.env["ir.config_parameter"].sudo().get_param(PENDING_SETUP_PARAM),
            str(self.env.company.id),
        )

    @patch("odoo.addons.envia.services.envia_oauth_client.requests.get")
    @patch("odoo.addons.envia.services.envia_oauth_client.requests.post")
    def test_oauth_verification_failure_marks_error_state(self, mock_post, mock_get):
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {"access_token": "envia-access-token-fail"},
            text='{"access_token": "envia-access-token-fail"}',
        )
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"success": False},
            text='{"success": false}',
        )
        wizard = self.env["envia.plugin.connect.wizard"].create_wizard_for_company(self.env.company)
        wizard.action_run_integration()
        wizard.action_finalize_integration()
        self.assertEqual(wizard.state, "error")
        self.assertFalse(self.env.company.envia_oauth_connected)

    def test_portal_user_cannot_generate_key(self):
        portal_group = self.env.ref("base.group_portal")
        user = self.env["res.users"].create(
            {
                "name": "Portal User",
                "login": "portal.user@test.local",
                "email": "portal.user@test.local",
                "password": "test-password-123",
                "group_ids": [(6, 0, [portal_group.id])],
            }
        )
        with self.assertRaises(UserError):
            generate_integration_credentials(self.env, self.env.company, user=user)

    @patch("odoo.addons.envia.services.envia_oauth_client.requests.get")
    @patch("odoo.addons.envia.services.envia_oauth_client.requests.post")
    def test_app_entry_opens_pending_setup_for_admin(self, mock_post, mock_get):
        _mock_oauth_success(mock_get, mock_post)
        self._reset_shipping_config()
        queue_pending_setup(self.env, self.env.company)
        action = self.env["envia.plugin.connect.wizard"].action_envia_app_entry()
        self.assertEqual(action["type"], "ir.actions.act_window")
        self.assertEqual(action["res_model"], "envia.plugin.connect.wizard")
        self.assertEqual(action["target"], "current")

    def test_wizard_redirects_to_settings_when_api_token_is_configured(self):
        wizard = self.env["envia.plugin.connect.wizard"].create_wizard_for_company(self.env.company)
        self._configure_shipping_api()
        action = wizard.action_redirect_if_configured()
        self.assertEqual(action["res_model"], "res.config.settings")

    def test_wizard_stays_on_welcome_when_api_token_is_missing(self):
        self._reset_shipping_config()
        wizard = self.env["envia.plugin.connect.wizard"].create_wizard_for_company(self.env.company)
        self.env.company.write({"envia_api_token": False})
        action = wizard.action_redirect_if_configured()
        self.assertFalse(action)

    def test_settings_preserves_api_token_when_password_field_is_blank(self):
        stored_token = "envia-shipping-token-persisted"
        self.env.company.write({"envia_api_token": stored_token})
        settings = self.env["res.config.settings"].create(
            {
                "company_id": self.env.company.id,
                "envia_environment": "sandbox",
            }
        )
        settings.set_values()
        self.assertEqual(self.env.company.envia_api_token, stored_token)

    @patch("odoo.addons.envia.services.envia_oauth_client.requests.get")
    def test_app_entry_syncs_shipping_token_from_oauth_before_showing_welcome(self, mock_get):
        self._reset_shipping_config()
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "success": True,
                "store": {
                    "access": {
                        "api_token": "envia-shipping-token-from-oauth",
                    }
                },
            },
            text='{"success": true}',
        )
        self.env.company.write(
            {
                "envia_oauth_connected": True,
                "envia_oauth_access_token": "oauth-access-token-123",
                "envia_api_token": False,
            }
        )
        action = self.env["envia.plugin.connect.wizard"].action_envia_app_entry()
        self.assertEqual(action["res_model"], "res.config.settings")
        self.assertEqual(self.env.company.envia_api_token, "envia-shipping-token-from-oauth")

    def test_app_entry_falls_back_to_settings(self):
        self._configure_shipping_api()
        self.env.company.envia_quote_onboarding_pending = False
        action = self.env["envia.plugin.connect.wizard"].action_envia_app_entry()
        self.assertEqual(action["res_model"], "res.config.settings")

    def test_app_entry_skips_welcome_when_api_token_is_configured(self):
        self.env["ir.config_parameter"].sudo().set_param(PENDING_SETUP_PARAM, str(self.env.company.id))
        self._configure_shipping_api()
        self.env.company.envia_quote_onboarding_pending = False
        action = self.env["envia.plugin.connect.wizard"].action_envia_app_entry()
        self.assertEqual(action["res_model"], "res.config.settings")
        self.assertFalse(
            self.env["ir.config_parameter"].sudo().get_param(PENDING_SETUP_PARAM)
        )

    def test_app_entry_shows_welcome_when_api_token_is_missing(self):
        self._reset_shipping_config()
        self.env["ir.config_parameter"].sudo().set_param(PENDING_SETUP_PARAM, "")
        self.env.company.write(
            {
                "envia_oauth_connected": True,
                "envia_api_token": False,
            }
        )
        action = self.env["envia.plugin.connect.wizard"].action_envia_app_entry()
        self.assertEqual(action["type"], "ir.actions.act_window")
        self.assertEqual(action["res_model"], "envia.plugin.connect.wizard")
        self.assertEqual(action["target"], "current")

    def test_app_entry_shows_welcome_when_not_connected_without_pending_flag(self):
        self._reset_shipping_config()
        self.env["ir.config_parameter"].sudo().set_param(PENDING_SETUP_PARAM, "")
        self.env.company.write(
            {
                "envia_oauth_connected": False,
                "envia_api_token": False,
            }
        )
        action = self.env["envia.plugin.connect.wizard"].action_envia_app_entry()
        self.assertEqual(action["type"], "ir.actions.act_window")
        self.assertEqual(action["res_model"], "envia.plugin.connect.wizard")
        self.assertEqual(action["target"], "current")

    def test_settings_connect_opens_modal_wizard(self):
        self._reset_shipping_config()
        action = self.env["envia.plugin.connect.wizard"].action_open_connect_wizard()
        self.assertEqual(action["type"], "ir.actions.act_window")
        self.assertEqual(action["target"], "new")

    def test_go_to_quotes_action(self):
        action = self.env["envia.plugin.connect.wizard"].create_wizard_for_company(
            self.env.company
        ).action_go_to_plugin_settings()
        self.assertEqual(action["res_model"], "res.config.settings")

    def test_settings_refresh_token_requires_connection(self):
        self.env.company.write({"envia_oauth_connected": False, "envia_api_token": False})
        settings = self.env["res.config.settings"].create({})
        action = settings.action_envia_refresh_integration_token()
        self.assertEqual(action["type"], "ir.actions.act_window")
        self.assertEqual(action["res_model"], "envia.plugin.connect.wizard")

    @patch("odoo.addons.envia.services.envia_oauth_client.requests.get")
    @patch("odoo.addons.envia.services.envia_oauth_client.requests.post")
    def test_settings_refresh_token_opens_envia_integration_wizard(self, mock_post, mock_get):
        _mock_oauth_success(mock_get, mock_post, plugin_version="2.5.0")
        self.env.company.write(
            {
                "envia_oauth_connected": True,
                "envia_integration_api_key": "stored-integration-api-key-12345678901234567890",
            }
        )
        settings = self.env["res.config.settings"].create({})
        action = settings.action_envia_refresh_integration_token()
        self.assertEqual(action["type"], "ir.actions.act_window")
        self.assertEqual(action["res_model"], "envia.plugin.connect.wizard")
        self.assertEqual(action["target"], "new")
        wizard = self.env["envia.plugin.connect.wizard"].browse(action["res_id"])
        self.assertEqual(wizard.state, "waiting_external")
        self.assertTrue(wizard.external_popup_url)

    def test_post_init_hook_queues_pending_setup(self):
        from odoo.addons.envia.hooks import post_init_hook

        self.env["ir.config_parameter"].sudo().set_param(PENDING_SETUP_PARAM, "")
        self.env.company.write({"envia_api_token": False})
        self.env.flush_all()
        post_init_hook(self.env)
        self.assertEqual(
            self.env["ir.config_parameter"].sudo().get_param(PENDING_SETUP_PARAM),
            str(self.env.company.id),
        )
