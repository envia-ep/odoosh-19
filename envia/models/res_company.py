from odoo import _, api, fields, models

from odoo.exceptions import UserError

from ..const import get_envia_dashboard_embed_url
from ..services.envia_config import (
    get_envia_api_base_url,
    get_envia_queries_base_url,
    is_envia_sandbox,
    resolve_envia_environment,
)
from ..services.envia_oauth_client import EnviaOauthClient
from ..services.envia_plugin_setup import (
    bind_integration_database,
    clear_pending_setup,
    generate_integration_credentials,
    get_envia_module_version,
    normalize_envia_plugin_version,
)


class ResCompany(models.Model):
    _inherit = "res.company"

    envia_environment = fields.Selection(
        [
            ("sandbox", "Sandbox"),
            ("production", "Production"),
        ],
        string="Envia Environment",
        default="production",
        help="Deprecated: URLs follow ENVIA_ENVIRONMENT on the server (production by default).",
    )
    envia_api_token = fields.Char(string="Envia API Token")
    envia_base_url = fields.Char(
        string="Envia Base URL",
        help="Leave empty to use the default URL for the selected environment.",
    )
    envia_default_carriers = fields.Char(
        string="Default Carrier Codes",
        default="dhl,fedex,estafeta",
        help="Comma-separated carrier codes used when quoting all carriers.",
    )
    envia_default_carrier_ids = fields.Many2many(
        "envia.carrier",
        string="Default Carriers",
        compute="_compute_envia_default_carrier_ids",
        inverse="_inverse_envia_default_carrier_ids",
        help="Carriers included when requesting rates.",
    )

    @api.depends("envia_default_carriers")
    def _compute_envia_default_carrier_ids(self) -> None:
        carrier_model = self.env["envia.carrier"]
        for company in self:
            codes = company._envia_parse_carrier_codes(company.envia_default_carriers)
            company.envia_default_carrier_ids = carrier_model.search([("code", "in", codes)])

    def _inverse_envia_default_carrier_ids(self) -> None:
        for company in self:
            company.envia_default_carriers = ",".join(company.envia_default_carrier_ids.mapped("code"))

    def _envia_parse_carrier_codes(self, carriers_value: str | bool | None) -> list[str]:
        if not carriers_value:
            return []
        return [code.strip() for code in str(carriers_value).split(",") if code.strip()]
    envia_default_origin_warehouse_id = fields.Many2one(
        "stock.warehouse",
        string="Default Origin Warehouse",
        check_company=True,
    )
    envia_default_origin_partner_id = fields.Many2one(
        "res.partner",
        string="Default Origin Contact",
        help="Legacy fallback when no origin warehouse is set.",
    )
    envia_warehouse_origin_ids = fields.One2many(
        "envia.warehouse.origin",
        "company_id",
        string="Warehouse Origin Addresses",
    )

    def _envia_get_default_origin_partner(self):
        self.ensure_one()
        # ponytail: temporarily ignore envia_default_origin_* settings; restore
        # warehouse/partner fallback when default origin config is re-enabled.
        return self.partner_id

    @api.model
    def _envia_format_address_preview(self, partner):
        if not partner:
            return False
        parts = [
            partner.street,
            partner.street2,
            " ".join(filter(None, [partner.zip, partner.city])),
            ", ".join(
                filter(
                    None,
                    [
                        partner.state_id.name if partner.state_id else False,
                        partner.country_id.name if partner.country_id else False,
                    ],
                )
            ),
        ]
        preview = "\n".join(filter(None, [partner.display_name, *parts]))
        return preview or _("No address saved on this contact yet.")
    envia_enable_branches = fields.Boolean(
        string="Enable branch pickup and delivery",
        default=True,
        help="Allow origin/destination branch (pickup and drop-off at carrier branches). When disabled, only home delivery routes are available.",
    )
    envia_checkout_enable_pickup = fields.Boolean(
        string="Enable pickup in website checkout",
        default=True,
        help="Show the Pickup option on the ecommerce delivery step.",
    )
    envia_checkout_show_map = fields.Boolean(
        string="Show pickup map in website checkout",
        default=True,
        help="Show the map of pickup locations when Pickup is selected.",
    )
    envia_checkout_pickup_map_only = fields.Boolean(
        string="Pickup map only (hide list)",
        default=False,
        help=(
            "When enabled, Pickup shows only the map (no branch list). "
            "Customers select a location from the map pins."
        ),
    )
    envia_checkout_ship_label = fields.Char(
        string="Checkout Ship label",
        default="",
        help="Leave empty to use the translated default. Label for the Ship tab on the ecommerce delivery step.",
    )
    envia_checkout_pickup_label = fields.Char(
        string="Checkout Pickup label",
        default="",
        help="Leave empty to use the translated default. Label for the Pickup tab on the ecommerce delivery step.",
    )
    envia_checkout_rates_per_carrier = fields.Integer(
        string="Max pickup branches per carrier",
        default=3,
        help=(
            "Maximum pickup branches shown per Envia carrier code in website "
            "checkout (e.g. paquetexpress, dhl; closest first). Use 0 for no "
            "limit. Does not affect Ship (home delivery) rates."
        ),
    )
    envia_default_carrier = fields.Boolean(
        string="Use Envia as default shipping method",
        default=False,
        help="Pre-select Envia.com in Add shipping on sale orders.",
    )
    envia_enable_labels = fields.Boolean(
        string="Enable Label Generation",
        default=True,
        help="Show Generate / Replace Envia Label on deliveries, quotes, and the quote wizard.",
    )
    envia_show_quote_archive = fields.Boolean(
        string="Show Quote Archive",
        default=False,
        help="Show the Quotes menu, quote smart buttons, and available rates on saved quotes.",
    )
    envia_label_format = fields.Selection(
        [
            ("PDF", "PDF"),
            ("ZPL", "ZPL"),
            ("PNG", "PNG"),
        ],
        string="Label Format",
        default="PDF",
    )
    envia_label_size = fields.Selection(
        [
            ("STOCK_4X6", "Stock 4x6"),
            ("PAPER_4X6", "Paper 4x6"),
        ],
        string="Label Size",
        default="STOCK_4X6",
    )
    envia_quote_onboarding_pending = fields.Boolean(
        string="Quote Onboarding Pending",
        default=True,
    )
    envia_oauth_connected = fields.Boolean(
        string="Envia OAuth Connected",
        default=False,
        readonly=True,
        copy=False,
    )
    envia_oauth_last_error = fields.Text(
        string="Envia OAuth Last Error",
        readonly=True,
        copy=False,
    )
    envia_oauth_access_token = fields.Char(
        string="Envia OAuth Access Token",
        readonly=True,
        copy=False,
        groups="base.group_system",
    )
    envia_integration_api_key = fields.Char(
        string="Envia Integration API Key",
        readonly=True,
        copy=False,
        groups="base.group_system",
        help="Plain-text Odoo API key used for the Envia.com integration.",
    )
    envia_plugin_version = fields.Char(
        string="Envia Plugin Version",
        readonly=True,
        copy=False,
    )
    envia_shop_id = fields.Char(
        string="Envia Shop ID",
        readonly=True,
        copy=False,
        help="Store identifier assigned by Envia.com during plugin integration.",
    )
    envia_company_id = fields.Char(
        string="Envia Company ID",
        readonly=True,
        copy=False,
        help="Company identifier assigned by Envia.com during plugin integration.",
    )
    envia_user_id = fields.Char(
        string="Envia User ID",
        readonly=True,
        copy=False,
        help="User identifier assigned by Envia.com during plugin integration.",
    )
    envia_plugin_version_display = fields.Char(
        string="Envia Plugin Version Display",
        compute="_compute_envia_plugin_version_display",
    )

    @api.depends(
        "envia_plugin_version",
        "envia_oauth_connected",
        "envia_oauth_access_token",
    )
    def _compute_envia_plugin_version_display(self) -> None:
        for company in self:
            if not company.envia_oauth_connected:
                company.envia_plugin_version_display = False
                continue

            version = normalize_envia_plugin_version(company.envia_plugin_version)
            if version:
                company.envia_plugin_version_display = version
            elif not company.envia_oauth_access_token:
                company.envia_plugin_version_display = _("Not synced — click Refresh token")
            else:
                company.envia_plugin_version_display = get_envia_module_version(company.env)

    def _initiate_envia_onboardings(self):
        onboardings = self.env["onboarding.onboarding"].sudo().search(
            [("route_name", "=", "envia_quotes")]
        )
        for company in self:
            onboardings.with_company(company)._search_or_create_progress()

    @api.model_create_multi
    def create(self, vals_list):
        companies = super().create(vals_list)
        companies._initiate_envia_onboardings()
        return companies

    def _envia_get_api_token(self) -> str:
        self.ensure_one()
        return (self.envia_api_token or "").strip()

    def _envia_get_shipping_api_token(self) -> str:
        """Return the Envia shipping API token stored on the company.

        Used for api.envia.com requests (quote, labels, tracking).
        Separate from the OAuth integration JWT (eshop/oauth endpoints).
        """
        self.ensure_one()
        return self._envia_get_api_token()

    def _envia_get_package_dimensions_token(self) -> str:
        """Bearer token for package/dimensions (same as shipping envia_api_token)."""
        self.ensure_one()
        return self._envia_get_shipping_api_token()

    def _envia_is_shipping_api_configured(self) -> bool:
        self.ensure_one()
        return bool((self.envia_api_token or "").strip())

    def _envia_try_sync_shipping_api_token_from_oauth(self) -> bool:
        self.ensure_one()
        if self._envia_is_shipping_api_configured():
            return True

        company = self.sudo()
        oauth_token = (company.envia_oauth_access_token or "").strip()
        if not oauth_token:
            return False

        try:
            store_access = EnviaOauthClient(self.env).fetch_store_access(oauth_token)
        except UserError:
            return False

        shipping_token = store_access.get("shipping_api_token")
        if not shipping_token:
            return False

        company.write({"envia_api_token": shipping_token})
        self.env.flush_all()
        return True

    def _envia_apply_integration_callback_success(
        self,
        *,
        hash_token: str,
        shop_id: str,
        envia_company_id: str | int | None = None,
        envia_user_id: str | int | None = None,
        api_key: str | None = None,
    ) -> dict:
        """Persist a successful Envia integration callback.

        The callback ``hash`` field is the Envia shipping API token.
        """
        self.ensure_one()
        company_vals = {
            "envia_oauth_connected": True,
            "envia_api_token": hash_token,
            "envia_oauth_last_error": False,
            "envia_shop_id": shop_id or False,
            "envia_company_id": str(envia_company_id).strip() if envia_company_id else False,
            "envia_user_id": str(envia_user_id).strip() if envia_user_id else False,
        }
        if api_key:
            company_vals["envia_integration_api_key"] = api_key
        self.write(company_vals)
        clear_pending_setup(self.env)
        return {
            "ok": True,
            "company": self.id,
            "shop": shop_id,
            "shipping_api_configured": self._envia_is_shipping_api_configured(),
        }

    def _envia_apply_integration_callback_failure(self, *, error_message: str) -> dict:
        self.ensure_one()
        self.write(
            {
                "envia_oauth_connected": False,
                "envia_api_token": False,
                "envia_shop_id": False,
                "envia_company_id": False,
                "envia_user_id": False,
                "envia_oauth_last_error": error_message,
            }
        )
        return {
            "ok": False,
            "company": self.id,
            "error": "integration_failed",
            "message": error_message,
        }

    def _envia_get_effective_environment(self) -> str:
        self.ensure_one()
        return resolve_envia_environment(self)

    def _envia_is_sandbox(self) -> bool:
        self.ensure_one()
        return is_envia_sandbox(self)

    def _envia_get_base_url(self) -> str:
        self.ensure_one()
        return get_envia_api_base_url(self)

    def _envia_get_queries_base_url(self) -> str:
        self.ensure_one()
        return get_envia_queries_base_url(self)

    def _envia_integration_api_key_is_valid(self, api_key: str | None = None) -> bool:
        from ..services.envia_integration_callback import validate_envia_store_credentials

        self.ensure_one()
        key = (api_key or self.envia_integration_api_key or "").strip()
        if not key:
            return False
        return bool(validate_envia_store_credentials(self.env.cr.dbname, key))

    def _envia_ensure_valid_integration_api_key(self, user=None) -> str:
        self.ensure_one()
        user = user or self.env.user
        api_key = (self.envia_integration_api_key or "").strip()
        if api_key and self._envia_integration_api_key_is_valid(api_key):
            bind_integration_database(self.env, api_key, user=user)
            return api_key
        credentials = generate_integration_credentials(self.env, self, user=user)
        self.sudo().write({"envia_integration_api_key": credentials["api_key"]})
        return credentials["api_key"]

    def _envia_default_branch_carrier(self) -> str:
        self.ensure_one()
        codes = self._envia_parse_carrier_codes(self.envia_default_carriers)
        return codes[0] if codes else "estafeta"

    def _envia_default_branch_carrier_id(self) -> int | bool:
        self.ensure_one()
        return self.env["envia.carrier"].search(
            [("code", "=", self._envia_default_branch_carrier())],
            limit=1,
        ).id

    @api.model
    def action_open_envia_dashboard(self):
        company = self.env.company
        if not company.envia_oauth_connected:
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": _("Envia.com"),
                    "message": _(
                        "Connect your store with Envia.com before opening the dashboard."
                    ),
                    "type": "warning",
                    "sticky": False,
                },
            }
        store_url = (self.env["ir.config_parameter"].sudo().get_param("web.base.url") or "").rstrip(
            "/"
        )
        action = self.env["ir.actions.actions"]._for_xml_id("envia.action_envia_dashboard_client")
        action["params"] = {
            "url": get_envia_dashboard_embed_url(
                store_url=store_url,
                company=company.envia_company_id,
                shop=company.envia_shop_id,
            ),
        }
        action["target"] = "current"
        return action
