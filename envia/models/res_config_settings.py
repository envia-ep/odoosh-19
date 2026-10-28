from odoo import _, api, fields, models
from odoo.exceptions import UserError

from ..services.envia_client import EnviaClient
from ..services.envia_config import resolve_envia_environment
from ..services.envia_integration_callback import build_callback_url, get_integration_database_name
from ..services.envia_oauth_client import EnviaOauthClient
from ..services.envia_plugin_setup import (
    clear_pending_setup,
    generate_integration_credentials,
    get_envia_module_version,
    normalize_envia_plugin_version,
)


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    envia_environment = fields.Selection(related="company_id.envia_environment", readonly=True)
    envia_api_token = fields.Char(related="company_id.envia_api_token", readonly=True)
    envia_base_url = fields.Char(related="company_id.envia_base_url", readonly=False)
    envia_default_carriers = fields.Char(related="company_id.envia_default_carriers", readonly=True)
    envia_default_carrier_ids = fields.Many2many(related="company_id.envia_default_carrier_ids", readonly=False)
    envia_default_origin_warehouse_id = fields.Many2one(
        related="company_id.envia_default_origin_warehouse_id",
        readonly=False,
    )
    envia_default_origin_partner_display = fields.Char(
        string="Linked contact",
        compute="_compute_envia_default_origin_display",
        readonly=True,
    )
    envia_default_origin_address_preview = fields.Char(
        string="Ship-from address",
        compute="_compute_envia_default_origin_display",
        readonly=True,
    )
    envia_warehouse_origin_ids = fields.One2many(
        related="company_id.envia_warehouse_origin_ids",
        readonly=False,
    )
    envia_enable_branches = fields.Boolean(
        related="company_id.envia_enable_branches",
        readonly=False,
    )
    envia_checkout_enable_pickup = fields.Boolean(
        related="company_id.envia_checkout_enable_pickup",
        readonly=False,
    )
    envia_checkout_show_map = fields.Boolean(
        related="company_id.envia_checkout_show_map",
        readonly=False,
    )
    envia_checkout_pickup_map_only = fields.Boolean(
        related="company_id.envia_checkout_pickup_map_only",
        readonly=False,
    )
    envia_checkout_ship_label = fields.Char(
        related="company_id.envia_checkout_ship_label",
        readonly=False,
    )
    envia_checkout_pickup_label = fields.Char(
        related="company_id.envia_checkout_pickup_label",
        readonly=False,
    )
    envia_checkout_rates_per_carrier = fields.Integer(
        related="company_id.envia_checkout_rates_per_carrier",
        readonly=False,
    )
    envia_default_carrier = fields.Boolean(
        related="company_id.envia_default_carrier",
        readonly=False,
    )
    envia_enable_labels = fields.Boolean(
        related="company_id.envia_enable_labels",
        readonly=False,
    )
    envia_show_quote_archive = fields.Boolean(
        related="company_id.envia_show_quote_archive",
        readonly=False,
    )
    envia_label_format = fields.Selection(related="company_id.envia_label_format", readonly=False)
    envia_label_size = fields.Selection(related="company_id.envia_label_size", readonly=False)
    envia_effective_base_url = fields.Char(
        string="Active endpoint",
        compute="_compute_envia_settings_display",
    )
    envia_is_sandbox = fields.Boolean(compute="_compute_envia_settings_display")
    envia_is_production = fields.Boolean(compute="_compute_envia_settings_display")
    envia_has_api_token = fields.Boolean(compute="_compute_envia_settings_display")
    envia_oauth_connected = fields.Boolean(related="company_id.envia_oauth_connected", readonly=True)
    envia_plugin_version = fields.Char(related="company_id.envia_plugin_version", readonly=True)
    envia_plugin_version_display = fields.Char(
        string="Envia Plugin Version Display",
        compute="_compute_envia_plugin_version_display",
        readonly=True,
    )
    envia_module_version_display = fields.Char(
        string="Envia Module Version",
        compute="_compute_envia_module_version_display",
        readonly=True,
    )
    envia_oauth_status_connected = fields.Char(
        compute="_compute_envia_oauth_status_labels",
        readonly=True,
    )
    envia_oauth_status_disconnected = fields.Char(
        compute="_compute_envia_oauth_status_labels",
        readonly=True,
    )
    # ponytail: inline es/en — view .po terms for this settings block stay English in DB.
    envia_ui_connection_title = fields.Char(compute="_compute_envia_connection_ui_labels")
    envia_ui_connection_help = fields.Char(compute="_compute_envia_connection_ui_labels")
    envia_ui_connect_help = fields.Text(compute="_compute_envia_connection_ui_labels")
    envia_ui_shop_id_label = fields.Char(compute="_compute_envia_connection_ui_labels")
    envia_ui_company_id_label = fields.Char(compute="_compute_envia_connection_ui_labels")
    envia_ui_user_id_label = fields.Char(compute="_compute_envia_connection_ui_labels")
    envia_ui_module_label = fields.Char(compute="_compute_envia_connection_ui_labels")
    envia_ui_api_key_title = fields.Char(compute="_compute_envia_connection_ui_labels")
    envia_ui_api_key_help = fields.Char(compute="_compute_envia_connection_ui_labels")
    envia_ui_shipping_token_title = fields.Char(compute="_compute_envia_connection_ui_labels")
    envia_ui_shipping_token_help = fields.Char(compute="_compute_envia_connection_ui_labels")
    envia_integration_api_key = fields.Char(
        string="Envia Integration API Key",
        compute="_compute_envia_integration_api_key",
        readonly=True,
    )
    envia_api_token_display = fields.Char(
        string="Envia Shipping API Token (stored)",
        compute="_compute_envia_api_token_display",
        readonly=True,
    )
    envia_oauth_integration_url = fields.Char(
        string="OAuth Integration URL",
        config_parameter="envia.oauth_integration_url",
    )
    envia_oauth_popup_url = fields.Char(
        string="OAuth Popup URL",
        config_parameter="envia.oauth_popup_url",
    )
    envia_oauth_use_sized_popup = fields.Boolean(
        string="Open Envia.com in a sized pop-up window",
        config_parameter="envia.oauth_use_sized_popup",
        help=(
            "When disabled (default), Envia.com opens in a new browser tab. "
            "When enabled, opens a smaller pop-up window that may require "
            "allowing pop-ups in the browser."
        ),
    )
    envia_eshop_test_url = fields.Char(
        string="Eshop Test URL",
        config_parameter="envia.eshop_test_url",
    )
    envia_eshop_accesses_me_url = fields.Char(
        string="Eshop Accesses Me URL",
        config_parameter="envia.eshop_accesses_me_url",
    )
    envia_integration_callback_url = fields.Char(
        string="Integration Callback URL",
        compute="_compute_envia_integration_callback_url",
        readonly=True,
    )
    envia_integration_database_name = fields.Char(
        string="Odoo Database Name",
        compute="_compute_envia_integration_database_name",
        readonly=True,
    )
    envia_shop_id_display = fields.Char(
        string="Shop ID",
        compute="_compute_envia_integration_identity_display",
        readonly=True,
    )
    envia_company_id_display = fields.Char(
        string="Company ID",
        compute="_compute_envia_integration_identity_display",
        readonly=True,
    )
    envia_user_id_display = fields.Char(
        string="User ID",
        compute="_compute_envia_integration_identity_display",
        readonly=True,
    )
    envia_odoo_version_display = fields.Char(
        string="Odoo Version",
        compute="_compute_envia_integration_identity_display",
        readonly=True,
    )

    @api.depends(
        "envia_default_origin_warehouse_id",
        "envia_default_origin_warehouse_id.partner_id",
        "company_id.envia_default_origin_partner_id",
        "company_id.partner_id",
    )
    def _compute_envia_default_origin_display(self) -> None:
        company_model = self.env["res.company"]
        for record in self:
            partner = record.company_id._envia_get_default_origin_partner()
            record.envia_default_origin_partner_display = partner.display_name if partner else False
            record.envia_default_origin_address_preview = company_model._envia_format_address_preview(
                partner
            )

    def _compute_envia_module_version_display(self) -> None:
        module_version = get_envia_module_version(self.env)
        for record in self:
            record.envia_module_version_display = module_version

    @api.depends("envia_oauth_connected")
    @api.depends_context("lang")
    def _compute_envia_oauth_status_labels(self) -> None:
        # ponytail: inline es/en labels; code .po terms do not import reliably from the 6MB catalog
        spanish = (self.env.lang or "en_US").startswith("es")
        connected = "Conectado" if spanish else "Connected"
        disconnected = "No conectado" if spanish else "Not connected"
        for record in self:
            record.envia_oauth_status_connected = connected
            record.envia_oauth_status_disconnected = disconnected

    @api.depends_context("lang")
    def _compute_envia_connection_ui_labels(self) -> None:
        spanish = (self.env.lang or "en_US").startswith("es")

        def t(en: str, es: str) -> str:
            return es if spanish else en

        title = t("Connection", "Conexión")
        connection_help = t(
            "Link your Odoo store with Envia.com and manage integration credentials.",
            "Vincula tu tienda Odoo con Envia.com y gestiona las credenciales de integración.",
        )
        connect_help = t(
            "This card shows your Envia.com link status (Connected / Not connected), "
            "shop identity (Shop ID, Company ID, User ID), and Odoo/module versions. "
            "Use Refresh token if credentials drift after reconnecting. "
            "Only administrators can refresh the integration token.",
            "Esta tarjeta muestra el estado del vínculo con Envia.com (Conectado / No conectado), "
            "la identidad de la tienda (ID de tienda, ID de empresa, ID de usuario) y las versiones "
            "de Odoo y del módulo. Usa Actualizar token si las credenciales se desincronizan "
            "tras reconectar. Solo los administradores pueden actualizar el token de integración.",
        )
        shop = t("Shop ID", "ID de tienda")
        company = t("Company ID", "ID de empresa")
        user = t("User ID", "ID de usuario")
        module = t("Module", "Módulo")
        api_title = t("Odoo API key", "Clave API de Odoo")
        api_help = t(
            "API key Envia.com uses to call back into Odoo. Generate it here and copy it into your Envia.com integration setup.",
            "Clave API que Envia.com usa para llamar a Odoo. Genérala aquí y cópiala en la configuración de integración de Envia.com.",
        )
        token_title = t("Envia shipping token", "Token de envío Envia")
        token_help = t(
            "Bearer token for api.envia.com (quoting, labels, tracking). Saved automatically by Envia.com during integration.",
            "Token Bearer para api.envia.com (cotización, etiquetas, rastreo). Se guarda automáticamente durante la integración con Envia.com.",
        )
        for record in self:
            record.envia_ui_connection_title = title
            record.envia_ui_connection_help = connection_help
            record.envia_ui_connect_help = connect_help
            record.envia_ui_shop_id_label = shop
            record.envia_ui_company_id_label = company
            record.envia_ui_user_id_label = user
            record.envia_ui_module_label = module
            record.envia_ui_api_key_title = api_title
            record.envia_ui_api_key_help = api_help
            record.envia_ui_shipping_token_title = token_title
            record.envia_ui_shipping_token_help = token_help

    @api.depends_context("uid")
    def _compute_envia_integration_callback_url(self) -> None:
        for record in self:
            record.envia_integration_callback_url = build_callback_url(record.env)

    @api.depends_context("uid")
    def _compute_envia_integration_database_name(self) -> None:
        for record in self:
            record.envia_integration_database_name = get_integration_database_name(record.env)

    @api.depends(
        "company_id",
        "company_id.envia_shop_id",
        "company_id.envia_company_id",
        "company_id.envia_user_id",
    )
    def _compute_envia_integration_identity_display(self) -> None:
        import odoo.release as release

        odoo_version = release.version
        for record in self:
            company = record.company_id
            record.envia_company_id_display = (company.envia_company_id or "").strip() or "—"
            record.envia_shop_id_display = (company.envia_shop_id or "").strip() or "—"
            record.envia_user_id_display = (company.envia_user_id or "").strip() or "—"
            record.envia_odoo_version_display = odoo_version

    @api.depends(
        "envia_environment",
        "envia_api_token",
        "envia_base_url",
        "company_id",
    )
    def _compute_envia_settings_display(self) -> None:
        for record in self:
            company = record.company_id
            record.envia_effective_base_url = company._envia_get_base_url() if company else ""
            record.envia_is_sandbox = resolve_envia_environment(company) == "sandbox"
            record.envia_is_production = not record.envia_is_sandbox
            record.envia_has_api_token = record.company_id._envia_is_shipping_api_configured()

    @api.depends(
        "envia_oauth_connected",
        "envia_plugin_version",
        "company_id.envia_oauth_access_token",
    )
    def _compute_envia_plugin_version_display(self) -> None:
        for record in self:
            if not record.envia_oauth_connected:
                record.envia_plugin_version_display = False
                continue

            version = normalize_envia_plugin_version(record.envia_plugin_version)
            if version:
                record.envia_plugin_version_display = version
            elif not record.company_id.envia_oauth_access_token:
                record.envia_plugin_version_display = _("Not synced — click Refresh token")
            else:
                record.envia_plugin_version_display = get_envia_module_version(record.env)

    @api.depends("company_id.envia_integration_api_key")
    def _compute_envia_integration_api_key(self) -> None:
        for record in self:
            if record.env.user.has_group("base.group_system"):
                record.envia_integration_api_key = record.company_id.envia_integration_api_key
            else:
                record.envia_integration_api_key = False

    @api.depends("company_id.envia_api_token")
    def _compute_envia_api_token_display(self) -> None:
        for record in self:
            if record.env.user.has_group("base.group_system"):
                record.envia_api_token_display = record.company_id.envia_api_token
            else:
                record.envia_api_token_display = False

    def action_generate_envia_integration_api_key(self):
        self.ensure_one()
        if not self.env.user.has_group("base.group_system"):
            raise UserError(_("Only administrators can generate the Envia integration API key."))
        credentials = generate_integration_credentials(self.env, self.company_id)
        self.company_id.envia_integration_api_key = credentials["api_key"]
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("API key generated"),
                "message": _(
                    "Copy the Odoo API key below and share it with Envia.com for the "
                    "integration callback."
                ),
                "type": "success",
                "sticky": False,
            },
        }

    def set_values(self):
        super().set_values()
        for settings in self:
            if settings.company_id._envia_is_shipping_api_configured():
                clear_pending_setup(self.env)
            quotes_menu = self.env.ref("envia.menu_envia_quotes", raise_if_not_found=False)
            if quotes_menu:
                quotes_menu.active = settings.envia_show_quote_archive

    @api.model
    def get_envia_adapter(self):
        company = self.env.company
        return self.env["envia.shipment"]._get_envia_adapter(company)

    def action_test_envia_connection(self):
        self.ensure_one()
        company = self.company_id
        token = company._envia_get_shipping_api_token()
        if not token:
            raise UserError(
                _(
                    "Paste your Envia shipping API token in Settings > Envia Shipping > "
                    "API Connection. Sandbox tokens come from "
                    "https://shipping-test.envia.com/settings/developers"
                )
            )
        country_code = company.country_id.code or "MX"
        body = EnviaClient(
            company._envia_get_base_url(),
            token,
        ).test_connection(
            queries_base_url=company._envia_get_queries_base_url(),
            country_code=country_code,
        )
        carrier_count = len(body.get("data") or [])
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Connection successful"),
                "message": _(
                    "Envia accepted the token for %(country)s and returned "
                    "%(count)s active carrier(s)."
                )
                % {"country": country_code, "count": carrier_count},
                "type": "success",
                "sticky": False,
            },
        }

    def action_open_envia_plugin_connect_wizard(self):
        self.ensure_one()
        return self.env["envia.plugin.connect.wizard"].action_open_connect_wizard()

    def action_open_envia_billing_info_wizard(self):
        self.ensure_one()
        return self.env["envia.billing.info.wizard"].action_open_billing_info_wizard()

    def action_envia_refresh_integration_token(self):
        self.ensure_one()
        if not self.env.user.has_group("base.group_system"):
            raise UserError(_("Only administrators can refresh the Envia integration token."))
        company = self.company_id
        wizard_model = self.env["envia.plugin.connect.wizard"]
        if company.envia_oauth_connected:
            return wizard_model.action_open_refresh_integration_wizard(company)
        return wizard_model.action_open_connect_wizard()
