from __future__ import annotations

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from ..services.envia_config import oauth_registration_sandbox
from ..services.envia_integration_callback import build_callback_url
from ..services.envia_oauth_client import (
    EnviaOauthClient,
    build_integration_popup_url,
    get_oauth_use_sized_popup,
)
from ..services.envia_plugin_setup import (
    PENDING_SETUP_PARAM,
    bind_integration_database,
    clear_pending_setup,
    generate_integration_credentials,
    get_envia_module_version,
    get_pending_setup_company_id,
    normalize_envia_plugin_version,
    normalize_integration_store_url,
)


class EnviaPluginConnectWizard(models.TransientModel):
    _name = "envia.plugin.connect.wizard"
    _description = "Envia Plugin Connect Wizard"

    company_id = fields.Many2one(
        "res.company",
        required=True,
        default=lambda self: self.env.company,
    )
    state = fields.Selection(
        [
            ("ready", "Ready"),
            ("waiting_external", "Waiting for Envia.com"),
            ("integrating", "Integrating"),
            ("success", "Success"),
            ("error", "Error"),
        ],
        default="ready",
        required=True,
    )
    store_url = fields.Char(readonly=True)
    database_name = fields.Char(readonly=True)
    user_email = fields.Char(readonly=True)
    api_key = fields.Char(readonly=True)
    external_popup_url = fields.Char(string="External Popup URL", readonly=True)
    integration_use_sized_popup = fields.Boolean(compute="_compute_integration_use_sized_popup")
    sandbox_value = fields.Char(readonly=True, default="false")
    user_id = fields.Many2one("res.users", string="User", readonly=True)
    integration_message = fields.Text(string="Integration Message", readonly=True)
    plugin_version = fields.Char(string="Plugin Version", readonly=True)
    plugin_version_display = fields.Char(
        string="Plugin Version Display",
        compute="_compute_plugin_version_display",
        readonly=True,
    )
    display_name = fields.Char(compute="_compute_display_name")

    def _compute_integration_use_sized_popup(self) -> None:
        use_sized_popup = get_oauth_use_sized_popup(self.env)
        for wizard in self:
            wizard.integration_use_sized_popup = use_sized_popup

    @api.depends("state")
    def _compute_display_name(self) -> None:
        labels = {
            "ready": _("Welcome to Envia Shipping"),
            "waiting_external": _("Waiting for Envia.com"),
            "integrating": _("Connecting with Envia.com"),
            "success": _("Envia.com Connected"),
            "error": _("Envia.com Connection Failed"),
        }
        for wizard in self:
            wizard.display_name = labels.get(wizard.state, _("Envia Shipping Setup"))

    @api.depends(
        "plugin_version",
        "state",
        "company_id.envia_plugin_version",
        "company_id.envia_oauth_access_token",
        "company_id.envia_oauth_connected",
    )
    def _compute_plugin_version_display(self) -> None:
        for wizard in self:
            if wizard.state != "success":
                wizard.plugin_version_display = False
                continue

            version = normalize_envia_plugin_version(
                wizard.plugin_version or wizard.company_id.envia_plugin_version
            )
            if version:
                wizard.plugin_version_display = version
            elif not wizard.company_id.envia_oauth_access_token:
                wizard.plugin_version_display = _("Not synced — click Refresh token")
            else:
                wizard.plugin_version_display = get_envia_module_version(wizard.env)

    def _run_oauth_integration_flow(self) -> None:
        self.ensure_one()
        self.write({"state": "integrating", "integration_message": False, "plugin_version": False})
        try:
            self._commit_for_external_oauth_validation()
            access_token, register_body = self._execute_oauth_integration(api_key_value=self.api_key)
            self._mark_integration_success(
                access_token=access_token,
                register_body=register_body,
            )
        except (UserError, Exception) as error:
            message = error.args[0] if error.args else str(error)
            self._mark_integration_error(message)

    def _sync_integration_store_url(self) -> None:
        """Persist HTTPS store URL for tunnel dev setups (Envia callback requires it)."""
        self.ensure_one()
        store_url = normalize_integration_store_url(self.store_url or "")
        if not store_url:
            return
        icp = self.env["ir.config_parameter"].sudo()
        if normalize_integration_store_url(icp.get_param("web.base.url", "")) != store_url:
            icp.set_param("web.base.url", store_url)
        if self.store_url != store_url:
            self.store_url = store_url

    def _register_integration_with_envia(self) -> None:
        """Tell Envia the Odoo callback URL before opening the OAuth popup."""
        self.ensure_one()
        api_key = (self.api_key or "").strip()
        if not api_key:
            return
        try:
            EnviaOauthClient(self.env).register_odoo_integration(
                url=self.store_url,
                database=self.database_name,
                email=self.user_email,
                api_key=api_key,
                sandbox=oauth_registration_sandbox(),
                version=get_envia_module_version(self.env),
                callback_url=build_callback_url(self.env),
            )
        except UserError:
            pass

    def _try_sync_integration_token(self) -> bool:
        self.ensure_one()
        company = self.company_id
        if company._envia_is_shipping_api_configured():
            return True
        company._envia_try_sync_shipping_api_token_from_oauth()
        return company._envia_is_shipping_api_configured()

    def _try_finalize_integration_via_oauth(self) -> bool:
        """Fallback when Envia validated the store but skipped the Odoo callback."""
        self.ensure_one()
        if self._try_sync_integration_token():
            if self.state == "waiting_external":
                self._mark_integration_success_from_callback()
            return True
        try:
            access_token, register_body = self._execute_oauth_integration(api_key_value=self.api_key)
        except UserError:
            return False
        self._mark_integration_success(access_token=access_token, register_body=register_body)
        return self.state == "success"

    def action_run_integration(self):
        self.ensure_one()
        if not self._is_envia_connect_admin():
            return self._get_admin_required_notification_action()
        self._commit_for_external_oauth_validation()
        self._sync_integration_store_url()
        self._register_integration_with_envia()
        popup_url = self._build_integration_popup_url()
        self.write(
            {
                "state": "waiting_external",
                "external_popup_url": popup_url,
            }
        )
        return False

    def action_open_envia_integration(self):
        """Opened from JS on user click (tab or sized pop-up)."""
        self.ensure_one()
        return False

    def action_on_external_popup_closed(self):
        self.ensure_one()
        if self.state != "waiting_external":
            return False
        next_action = self.action_poll_integration_status()
        if next_action:
            return next_action
        if self._try_finalize_integration_via_oauth():
            return self._get_envia_settings_action()
        self.write({"state": "ready", "external_popup_url": False})
        return False

    def action_cancel_external_popup_wait(self):
        self.ensure_one()
        return self.action_on_external_popup_closed()

    def action_poll_integration_status(self):
        self.ensure_one()
        if self.state != "waiting_external":
            return False
        self.company_id.invalidate_recordset()
        if not self._try_sync_integration_token():
            return False
        self._mark_integration_success_from_callback()
        return self._get_envia_settings_action()

    def _mark_integration_success_from_callback(self) -> None:
        self.ensure_one()
        clear_pending_setup(self.env)
        self.company_id.envia_quote_onboarding_pending = False
        self.company_id.write(
            {
                "envia_oauth_connected": True,
                "envia_oauth_last_error": False,
            }
        )
        plugin_version = normalize_envia_plugin_version(self.company_id.envia_plugin_version) or False
        self.write(
            {
                "state": "success",
                "external_popup_url": False,
                "plugin_version": plugin_version,
                "integration_message": _(
                    "Your Odoo store was connected successfully with Envia.com."
                ),
                "api_key": self.company_id.envia_integration_api_key or self.api_key,
            }
        )

    @api.model
    def _is_envia_connect_admin(self) -> bool:
        return self.env.user.has_group("base.group_system")

    @api.model
    def _get_admin_required_notification_action(self):
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Administrator required"),
                "message": _(
                    "Only an Odoo administrator can connect Envia.com. "
                    "Please ask your administrator to complete the connection."
                ),
                "type": "warning",
                "sticky": False,
            },
        }

    @api.model
    def _get_open_action(self, wizard, target="new", name=None):
        return {
            "type": "ir.actions.act_window",
            "name": name or _("Connect with Envia.com"),
            "res_model": self._name,
            "res_id": wizard.id,
            "view_mode": "form",
            "views": [(False, "form")],
            "target": target,
        }

    @api.model
    def _get_connect_action(self, wizard, target="new", name=None):
        if isinstance(wizard, dict):
            return wizard
        return self._get_open_action(wizard, target=target, name=name)

    @api.model
    def _get_envia_settings_action(self):
        if not self._is_envia_connect_admin():
            return self._get_admin_required_notification_action()
        return self.env["ir.actions.act_window"]._for_xml_id("envia.action_envia_config_settings")

    def _build_integration_popup_url(self) -> str:
        self.ensure_one()
        api_key = (self.api_key or "").strip()
        if not api_key:
            raise UserError(_("Missing API key for Envia integration."))
        return build_integration_popup_url(
            self.env,
            url=self.store_url,
            database=self.database_name,
            email=self.user_email,
            api_key=api_key,
            company_id=self.company_id.id,
            user_id=self.user_id.id or self.env.user.id,
        )

    def get_integration_url(self) -> str:
        self.ensure_one()
        return (self.external_popup_url or "").strip() or self._build_integration_popup_url()

    def get_integration_url_action(self):
        """Open the Envia.com integration URL in a new browser tab."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_url",
            "url": self.get_integration_url(),
            "target": "new",
        }

    def action_finalize_integration(self):
        self.ensure_one()
        if not self._is_envia_connect_admin():
            return self._get_admin_required_notification_action()
        self._run_oauth_integration_flow()
        if self.state == "error":
            return self._get_open_action(self)
        return self._get_envia_settings_action()

    def action_return_to_connect_screen(self):
        self.ensure_one()
        self.write({"state": "ready"})
        return self._get_connect_action(
            self,
            target="current",
            name=_("Welcome to Envia Shipping"),
        )

    def action_go_to_plugin_settings(self):
        self.ensure_one()
        return self._get_envia_settings_action()

    def action_redirect_if_configured(self):
        self.ensure_one()
        company = self.company_id
        company._envia_try_sync_shipping_api_token_from_oauth()
        if not company._envia_is_shipping_api_configured():
            return False
        clear_pending_setup(self.env)
        company.envia_quote_onboarding_pending = False
        return self._get_envia_settings_action()

    def _commit_for_external_oauth_validation(self) -> None:
        """Persist the API key before Envia validates it against this Odoo instance."""
        self.ensure_one()
        if self.api_key:
            self.company_id.sudo().write({"envia_integration_api_key": self.api_key})
            bind_integration_database(self.env, self.api_key)
        self.env.flush_all()
        self.env.cr.commit()

    def _execute_oauth_integration(self, api_key_value=None) -> tuple[str, dict]:
        self.ensure_one()
        api_key = (api_key_value or self.api_key or "").strip()
        if not api_key:
            raise UserError(_("Missing API key for Envia integration."))

        client = EnviaOauthClient(self.env)
        register_body = client.register_odoo_integration(
            url=self.store_url,
            database=self.database_name,
            email=self.user_email,
            api_key=api_key,
            sandbox=oauth_registration_sandbox(),
        )
        if not client.verify_integration():
            raise UserError(_("Envia integration verification failed. The test endpoint did not return success."))
        return client.access_token, register_body

    def _resolve_plugin_version(
        self,
        *,
        access_token: str,
        register_body: dict | None = None,
    ) -> str | False:
        self.ensure_one()
        if register_body:
            version = EnviaOauthClient.extract_store_access_info(register_body).get("version")
            if version:
                return version

        store_access = EnviaOauthClient(self.env).fetch_store_access(access_token)
        version = store_access.get("version")
        if version:
            return version

        return normalize_envia_plugin_version(self.company_id.envia_plugin_version) or get_envia_module_version(
            self.env
        )

    def _sync_plugin_version_from_envia(
        self,
        access_token: str,
        register_body: dict | None = None,
    ) -> str | False:
        self.ensure_one()
        version = self._resolve_plugin_version(
            access_token=access_token,
            register_body=register_body,
        )
        if version:
            self.company_id.envia_plugin_version = version
        return version

    def _sync_shipping_api_token(
        self,
        *,
        register_body: dict | None = None,
        access_token: str | None = None,
    ) -> None:
        self.ensure_one()
        shipping_token = False
        if register_body:
            shipping_token = EnviaOauthClient.extract_store_access_info(register_body).get(
                "shipping_api_token"
            )
        token = access_token or self.company_id.envia_oauth_access_token
        if not shipping_token and token:
            try:
                store_access = EnviaOauthClient(self.env).fetch_store_access(token)
            except UserError:
                store_access = {}
            shipping_token = store_access.get("shipping_api_token")

        if shipping_token:
            self.company_id.envia_api_token = shipping_token
            return

        if self.company_id.envia_api_token == self.company_id.envia_oauth_access_token:
            self.company_id.envia_api_token = False

    def _mark_integration_success(self, access_token=None, register_body=None) -> None:
        self.ensure_one()
        company_vals = {
            "envia_oauth_connected": True,
            "envia_oauth_last_error": False,
        }
        if access_token:
            company_vals["envia_oauth_access_token"] = access_token
        if self.api_key:
            company_vals["envia_integration_api_key"] = self.api_key
        self.company_id.write(company_vals)
        self._sync_shipping_api_token(
            register_body=register_body,
            access_token=access_token,
        )
        clear_pending_setup(self.env)
        self.company_id.envia_quote_onboarding_pending = False

        plugin_version = False
        if access_token:
            try:
                plugin_version = self._sync_plugin_version_from_envia(
                    access_token,
                    register_body=register_body,
                )
            except UserError:
                plugin_version = (
                    normalize_envia_plugin_version(self.company_id.envia_plugin_version)
                    or get_envia_module_version(self.env)
                )

        integration_message = _("Your Odoo store was connected successfully with Envia.com.")
        if not self.company_id.envia_api_token:
            integration_message = _(
                "Your Odoo store was connected with Envia.com, but Envia did not return a "
                "shipping API token. Paste it manually in Settings > Envia Shipping > "
                "Envia Shipping API Token."
            )

        self.write(
            {
                "state": "success",
                "plugin_version": plugin_version or self.company_id.envia_plugin_version or False,
                "integration_message": integration_message,
                "api_key": self.company_id.envia_integration_api_key or self.api_key,
            }
        )

    def _mark_integration_error(self, error_message: str) -> None:
        self.ensure_one()
        self.company_id.write(
            {
                "envia_oauth_connected": False,
                "envia_oauth_access_token": False,
                "envia_integration_api_key": False,
                "envia_plugin_version": False,
                "envia_oauth_last_error": error_message,
            }
        )
        self.write(
            {
                "state": "error",
                "plugin_version": False,
                "integration_message": error_message,
            }
        )

    @api.model
    def _create_connected_wizard(self, company):
        base_url = normalize_integration_store_url(
            self.env["ir.config_parameter"].sudo().get_param("web.base.url", "")
        )
        plugin_version = normalize_envia_plugin_version(company.envia_plugin_version) or False
        if company.envia_oauth_access_token:
            try:
                store_access = EnviaOauthClient(self.env).fetch_store_access(
                    company.envia_oauth_access_token
                )
                plugin_version = store_access.get("version") or plugin_version
                if plugin_version:
                    company.envia_plugin_version = plugin_version
            except UserError:
                plugin_version = (
                    normalize_envia_plugin_version(company.envia_plugin_version)
                    or get_envia_module_version(self.env)
                    if company.envia_oauth_access_token
                    else False
                )

        integration_message = _("Your Odoo store is connected with Envia.com.")
        if company.envia_oauth_connected and not company.envia_oauth_access_token:
            integration_message = _(
                "Your Odoo store is connected with Envia.com. "
                "Click Refresh token to sync the latest connection details."
            )

        return self.create(
            {
                "company_id": company.id,
                "store_url": base_url,
                "database_name": self.env.cr.dbname,
                "user_email": self.env.user.login,
                "user_id": self.env.user.id,
                "api_key": company.envia_integration_api_key or False,
                "sandbox_value": "false",
                "state": "success",
                "plugin_version": plugin_version,
                "integration_message": integration_message,
            }
        )

    @api.model
    def _popup_url_for_credentials(self, credentials: dict) -> str:
        return build_integration_popup_url(
            self.env,
            url=credentials["store_url"],
            database=credentials["database_name"],
            email=credentials["user_email"],
            api_key=credentials["api_key"],
            company_id=credentials["company_id"],
            user_id=credentials["user_id"],
        )

    @api.model
    def create_wizard_for_company(self, company):
        if not self._is_envia_connect_admin():
            return self._get_admin_required_notification_action()

        credentials = generate_integration_credentials(self.env, company)
        return self.create(
            {
                **credentials,
                "external_popup_url": self._popup_url_for_credentials(credentials),
                "sandbox_value": "false",
                "state": "ready",
            }
        )

    def _get_stored_integration_credentials(self) -> dict:
        self.ensure_one()
        api_key = (self.company_id.envia_integration_api_key or "").strip()
        if not api_key:
            raise UserError(
                _("Generate the Odoo API key for Envia.com in Settings before refreshing the token.")
            )
        base_url = normalize_integration_store_url(
            self.env["ir.config_parameter"].sudo().get_param("web.base.url", "")
        )
        return {
            "store_url": base_url,
            "database_name": self.env.cr.dbname,
            "user_email": self.env.user.login,
            "api_key": api_key,
            "user_id": self.env.user.id,
        }

    def _open_external_integration_popup(self):
        self.ensure_one()
        credentials = self._get_stored_integration_credentials()
        self.write(
            {
                "store_url": credentials["store_url"],
                "database_name": credentials["database_name"],
                "user_email": credentials["user_email"],
                "api_key": credentials["api_key"],
                "user_id": credentials["user_id"],
                "sandbox_value": "false",
                "integration_message": False,
            }
        )
        self._commit_for_external_oauth_validation()
        self._sync_integration_store_url()
        self._register_integration_with_envia()
        popup_url = self._build_integration_popup_url()
        self.write(
            {
                "state": "waiting_external",
                "external_popup_url": popup_url,
            }
        )

    def action_refresh_token(self):
        self.ensure_one()
        if not self._is_envia_connect_admin():
            return self._get_admin_required_notification_action()
        self._open_external_integration_popup()
        return False

    @api.model
    def action_open_refresh_integration_wizard(self, company):
        if not self._is_envia_connect_admin():
            return self._get_admin_required_notification_action()
        wizard = self._create_connected_wizard(company)
        wizard._open_external_integration_popup()
        return self._get_open_action(
            wizard,
            target="new",
            name=_("Refresh Envia.com connection"),
        )

    def action_retry_integration(self):
        self.ensure_one()
        if not self._is_envia_connect_admin():
            return self._get_admin_required_notification_action()
        if not self.api_key:
            wizard = self.create_wizard_for_company(self.company_id)
            if isinstance(wizard, dict):
                return wizard
            return self._get_open_action(wizard)

        self._run_oauth_integration_flow()
        return self._get_open_action(self)

    @api.model
    def _get_pending_connect_action(self, company_id):
        company = self.env["res.company"].browse(company_id)
        company._envia_try_sync_shipping_api_token_from_oauth()
        if company._envia_is_shipping_api_configured():
            clear_pending_setup(self.env)
            company.envia_quote_onboarding_pending = False
            if self._is_envia_connect_admin():
                return self._get_envia_settings_action()
            return self._get_quotes_list_action()
        wizard = self.create_wizard_for_company(company)
        if isinstance(wizard, dict):
            return wizard
        return self._get_connect_action(
            wizard,
            target="current",
            name=_("Welcome to Envia Shipping"),
        )

    @api.model
    def _get_quotes_list_action(self):
        return self.env.ref("envia.action_envia_quote").read()[0]

    @api.model
    def action_envia_app_entry(self):
        company = self.env.company
        company._envia_try_sync_shipping_api_token_from_oauth()
        if company._envia_is_shipping_api_configured():
            clear_pending_setup(self.env)
            company.envia_quote_onboarding_pending = False
            if self._is_envia_connect_admin():
                return self._get_envia_settings_action()
            return self._get_quotes_list_action()

        if not self._is_envia_connect_admin():
            if get_pending_setup_company_id(self.env):
                return self._get_admin_required_notification_action()
            return self._get_quotes_list_action()

        company_id = get_pending_setup_company_id(self.env) or company.id
        return self._get_pending_connect_action(company_id)

    @api.model
    def action_open_connect_wizard(self):
        if not self._is_envia_connect_admin():
            return self._get_admin_required_notification_action()
        company = self.env.company
        company._envia_try_sync_shipping_api_token_from_oauth()
        if company._envia_is_shipping_api_configured() or company.envia_oauth_connected:
            clear_pending_setup(self.env)
            return self._get_envia_settings_action()
        wizard = self.create_wizard_for_company(company)
        if isinstance(wizard, dict):
            return wizard
        return self._get_connect_action(wizard)
