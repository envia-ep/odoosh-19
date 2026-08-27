from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import format_amount

from ..const import ENVIA_LOCATION_TYPE_SELECTION
from ..services.dto import Contact
from ..services.envia_client import EnviaClient
from ..services.envia_geocodes_client import EnviaGeocodesClient
from ..services.envia_official_adapter import EnviaOfficialAdapter
from ..services.payload_mapper import PayloadMapper, get_envia_adapter


def _format_delivery_eta(days):
    if days == 1:
        return "Next day"
    if days and days > 1:
        return f"1-{days} days"
    if days:
        return "1 day"
    return ""


class EnviaQuoteWizardBranch(models.TransientModel):
    _name = "envia.quote.wizard.branch"
    _description = "Envia Quote Wizard Branch Option"
    _order = "distance asc, name asc"

    wizard_id = fields.Many2one("envia.quote.wizard", required=True, ondelete="cascade")
    side = fields.Selection(
        [("origin", "Origin"), ("destination", "Destination")],
        required=True,
    )
    external_id = fields.Char()
    branch_code = fields.Char()
    number = fields.Char(string="Street Number")
    name = fields.Char(required=True)
    street = fields.Char()
    city = fields.Char()
    zip = fields.Char(string="Postal Code")
    distance = fields.Float(string="Distance (km)", digits=(16, 1))
    state_code = fields.Char()
    country_code = fields.Char()
    carrier = fields.Char()
    phone = fields.Char()
    email = fields.Char()
    is_selected = fields.Boolean()
    price = fields.Float()
    currency_name = fields.Char()
    estimated_delivery_days = fields.Integer()
    service_id = fields.Char()
    service_name = fields.Char()
    option_label = fields.Char(compute="_compute_option_labels")
    address_label = fields.Char(compute="_compute_option_labels")
    price_label = fields.Char(compute="_compute_option_labels")

    @api.depends(
        "carrier",
        "name",
        "service_name",
        "price",
        "currency_name",
        "estimated_delivery_days",
        "wizard_id.currency_id",
    )
    def _compute_option_labels(self):
        carrier_model = self.env["envia.carrier"]
        for line in self:
            carrier = carrier_model.search([("code", "=", line.carrier)], limit=1)
            carrier_name = carrier.display_name if carrier else (line.carrier or "").title()
            title = (
                _("%(carrier)s - %(branch)s", carrier=carrier_name, branch=line.name)
                if carrier_name
                else line.name
            )
            eta = _format_delivery_eta(line.estimated_delivery_days)
            if eta:
                title = f"{title} ({eta})"
            if line.service_name:
                title = f"{title} - {line.service_name}"
            line.option_label = title
            parts = [
                part
                for part in (
                    line.street,
                    line.city,
                    line.state_code,
                    line.zip,
                    line.country_code,
                )
                if part
            ]
            line.address_label = ", ".join(parts)
            currency = (
                self.env["res.currency"].search(
                    [("name", "=", line.currency_name)], limit=1
                )
                if line.currency_name
                else line.wizard_id.currency_id
            )
            line.price_label = (
                str(format_amount(line.env, line.price, currency))
                if line.price and currency
                else ""
            )

    def _envia_branch_code(self):
        """Best branch identifier for Envia (prefer human codes like MTY01)."""
        self.ensure_one()
        code = (self.branch_code or "").strip()
        if code and not code.isdigit():
            return code
        external = (self.external_id or "").strip()
        if external and not external.isdigit():
            return external
        # Some carriers only return a numeric branch id; Envia still accepts it.
        return code or external or False

    def action_select_branch(self):
        self.ensure_one()
        if not self.exists():
            return False
        siblings = getattr(self.wizard_id, f"{self.side}_branch_line_ids")
        siblings.write({"is_selected": False})
        self.is_selected = True
        wizard = self.wizard_id
        wizard.with_context(envia_apply_branch=True)._apply_branch_to_side(self)
        # Rate-first: after the destination ocurre branch is chosen, persist it on
        # the quote; origin stays "any branch" without a concrete code.
        if self.side == "destination" and wizard.quote_id:
            wizard.quote_id.write(wizard._quote_location_values())
            selected = wizard.service_line_ids.filtered("is_selected")[:1]
            if selected:
                service = wizard.quote_id.service_ids.filtered(
                    lambda line: line.service_id == selected.service_id
                )[:1]
                if service:
                    service.action_select_service()
        return False


class EnviaQuoteWizardAddress(models.TransientModel):
    _name = "envia.quote.wizard.address"
    _description = "Envia Quote Wizard Saved Address Option"
    _order = "partner_id"

    wizard_id = fields.Many2one("envia.quote.wizard", required=True, ondelete="cascade")
    side = fields.Selection(
        [("origin", "Origin"), ("destination", "Destination")],
        required=True,
    )
    warehouse_id = fields.Many2one("stock.warehouse", ondelete="cascade")
    partner_id = fields.Many2one("res.partner", required=True, ondelete="cascade")
    is_selected = fields.Boolean()
    option_label = fields.Char(compute="_compute_option_labels")
    address_label = fields.Char(compute="_compute_option_labels")

    @api.depends("warehouse_id", "partner_id", "partner_id.street", "partner_id.city", "partner_id.zip")
    def _compute_option_labels(self):
        for line in self:
            if line.warehouse_id:
                line.option_label = line.warehouse_id.display_name or ""
            else:
                line.option_label = line.partner_id.display_name or ""
            line.address_label = EnviaQuoteWizard._format_address_preview(line.partner_id) or ""

    def action_select_address(self):
        self.ensure_one()
        siblings = getattr(self.wizard_id, f"{self.side}_address_line_ids")
        siblings.write({"is_selected": False})
        self.is_selected = True
        wizard = self.wizard_id
        fallback = wizard.env.company.country_id if self.side == "origin" else None
        partner = self.partner_id
        values = {}
        if self.side == "origin" and self.warehouse_id:
            values["origin_warehouse_id"] = self.warehouse_id.id
            partner = self.warehouse_id.partner_id or partner
        values[f"{self.side}_partner_id"] = partner.id
        values.update(wizard._build_address_defaults(partner, self.side, fallback))
        wizard.with_context(envia_skip_address_sync=True).write(values)
        if wizard._is_ready_for_auto_quote():
            return wizard.action_get_quote()
        return wizard._wizard_action()


class EnviaQuoteWizardService(models.TransientModel):
    _name = "envia.quote.wizard.service"
    _description = "Envia Quote Wizard Service Line"
    _order = "price asc"

    wizard_id = fields.Many2one("envia.quote.wizard", required=True, ondelete="cascade")
    service_id = fields.Char(required=True)
    envia_service_id = fields.Integer()
    carrier = fields.Char()
    carrier_name = fields.Char()
    service_name = fields.Char()
    price = fields.Float()
    currency_name = fields.Char()
    estimated_delivery_days = fields.Integer()
    # Envia dropOff: 0/None door-door, 1 origin branch, 2 dest branch, 3 both.
    drop_off = fields.Integer()
    is_selected = fields.Boolean()
    option_label = fields.Char(compute="_compute_option_labels")
    price_label = fields.Char(compute="_compute_option_labels")
    route_type_label = fields.Char(compute="_compute_option_labels")

    def _envia_location_type_labels(self):
        """Reuse selection labels (Domicilio/Sucursal) already translated in Odoo 19."""
        return {
            "address": self.env._("Domicile"),
            "branch": self.env._("Branch"),
        }

    def _route_type_label_for(self, drop_off):
        labels = self._envia_location_type_labels()
        origin = labels["branch" if drop_off in (1, 3) else "address"]
        destination = labels["branch" if drop_off in (2, 3) else "address"]
        return f"{origin} - {destination}"

    @api.depends(
        "carrier_name",
        "carrier",
        "service_name",
        "estimated_delivery_days",
        "price",
        "currency_name",
        "drop_off",
        "wizard_id.currency_id",
    )
    def _compute_option_labels(self):
        for line in self:
            carrier = line.carrier_name or line.carrier or ""
            service = line.service_name or ""
            title = f"{carrier} {service}".strip() if carrier else service
            eta = _format_delivery_eta(line.estimated_delivery_days)
            line.option_label = f"{title} ( {eta} )" if eta else title
            line.route_type_label = line._route_type_label_for(line.drop_off)
            currency = (
                self.env["res.currency"].search(
                    [("name", "=", line.currency_name)], limit=1
                )
                if line.currency_name
                else line.wizard_id.currency_id
            )
            line.price_label = str(
                format_amount(line.env, line.price, currency)
                if currency
                else f"{line.price:.2f}"
            )

    def action_choose_service(self):
        self.ensure_one()
        return self.wizard_id.with_context(service_id=self.service_id).action_select_service_rate()


class EnviaQuoteWizard(models.TransientModel):
    _name = "envia.quote.wizard"
    _description = "Envia Quote Wizard"

    step = fields.Selection(
        [
            ("address", "Shipment Details"),
            ("rates", "Select Rate"),
        ],
        default="address",
        required=True,
    )
    # Set after Update seeds from the order quote; blocks re-restore on Get rate save.
    is_seeded_from_order = fields.Boolean(default=False)
    sale_order_id = fields.Many2one("sale.order", readonly=True)
    picking_id = fields.Many2one("stock.picking", readonly=True)
    origin_warehouse_id = fields.Many2one(
        "stock.warehouse",
        string="Ship From Warehouse",
        check_company=True,
    )
    origin_partner_id = fields.Many2one("res.partner", string="Ship From")
    destination_partner_id = fields.Many2one("res.partner", string="Ship To")
    allowed_origin_warehouse_ids = fields.Many2many(
        "stock.warehouse",
        compute="_compute_allowed_origin_warehouses",
    )
    origin_linked_contact_display = fields.Char(
        string="Linked contact",
        compute="_compute_origin_linked_contact_display",
    )
    allowed_destination_partner_ids = fields.Many2many(
        "res.partner",
        compute="_compute_allowed_address_partners",
    )
    origin_location_type = fields.Selection(
        ENVIA_LOCATION_TYPE_SELECTION,
        string="Origin Type",
        default="address",
        required=True,
    )
    destination_location_type = fields.Selection(
        ENVIA_LOCATION_TYPE_SELECTION,
        string="Destination Type",
        default="address",
        required=True,
    )
    origin_branch_carrier_id = fields.Many2one(
        "envia.carrier",
        string="Origin Carrier",
        domain="[('active', '=', True)]",
    )
    destination_branch_carrier_id = fields.Many2one(
        "envia.carrier",
        string="Destination Carrier",
        domain="[('active', '=', True)]",
    )
    origin_branch_line_ids = fields.One2many(
        "envia.quote.wizard.branch",
        "wizard_id",
        domain=[("side", "=", "origin")],
    )
    destination_branch_line_ids = fields.One2many(
        "envia.quote.wizard.branch",
        "wizard_id",
        domain=[("side", "=", "destination")],
    )
    origin_address_line_ids = fields.One2many(
        "envia.quote.wizard.address",
        "wizard_id",
        domain=[("side", "=", "origin")],
    )
    destination_address_line_ids = fields.One2many(
        "envia.quote.wizard.address",
        "wizard_id",
        domain=[("side", "=", "destination")],
    )
    origin_postal_code = fields.Char(string="Origin Postal Code")
    origin_street = fields.Char(string="Origin Street")
    origin_street_number = fields.Char(string="Origin Street Number")
    origin_district = fields.Char(string="Origin District")
    origin_city = fields.Char(string="Origin City")
    origin_country_id = fields.Many2one("res.country", string="Origin Country")
    origin_state_id = fields.Many2one(
        "res.country.state",
        string="Origin State",
        domain="[('country_id', '=', origin_country_id)]",
    )
    destination_postal_code = fields.Char(string="Destination Postal Code")
    destination_street = fields.Char(string="Destination Street")
    destination_street_number = fields.Char(string="Destination Street Number")
    destination_district = fields.Char(string="Destination District")
    destination_city = fields.Char(string="Destination City")
    destination_country_id = fields.Many2one("res.country", string="Destination Country")
    destination_state_id = fields.Many2one(
        "res.country.state",
        string="Destination State",
        domain="[('country_id', '=', destination_country_id)]",
    )
    weight = fields.Float(string="Weight (kg)", required=True, default=1.0)
    content = fields.Char(required=True, default="General merchandise")
    declared_value = fields.Float(string="Declared Value")
    currency_id = fields.Many2one("res.currency", default=lambda self: self.env.company.currency_id)
    quote_id = fields.Many2one("envia.quote", readonly=True)
    service_line_ids = fields.One2many("envia.quote.wizard.service", "wizard_id")
    route_summary = fields.Char(compute="_compute_route_summary")
    origin_address_warning = fields.Char(compute="_compute_address_warnings")
    destination_address_warning = fields.Char(compute="_compute_address_warnings")
    origin_envia_sync_warning = fields.Char(compute="_compute_origin_envia_sync_warning")
    weight_warning = fields.Char(compute="_compute_weight_warning")
    envia_package_preview = fields.Text(readonly=True)
    envia_package_sync_hint = fields.Char(readonly=True)
    origin_address_preview = fields.Char(compute="_compute_address_previews")
    destination_address_preview = fields.Char(compute="_compute_address_previews")
    origin_contact_complete = fields.Boolean(compute="_compute_contact_status")
    destination_contact_complete = fields.Boolean(compute="_compute_contact_status")
    selected_service_label = fields.Char(compute="_compute_selected_service_label")
    service_count = fields.Integer(compute="_compute_service_count")
    can_get_rates = fields.Boolean(compute="_compute_form_state")
    validation_summary = fields.Text(compute="_compute_form_state")
    blocking_message = fields.Char(compute="_compute_form_state")
    is_international_route = fields.Boolean(compute="_compute_route_flags")
    is_domestic_route = fields.Boolean(compute="_compute_route_flags")
    is_sandbox = fields.Boolean(compute="_compute_route_flags")
    is_standalone = fields.Boolean(compute="_compute_is_standalone")
    envia_enable_labels = fields.Boolean(compute="_compute_envia_company_flags")
    envia_show_quote_archive = fields.Boolean(compute="_compute_envia_company_flags")
    envia_branches_enabled = fields.Boolean(compute="_compute_envia_company_flags")
    cheapest_rate_label = fields.Char(compute="_compute_rate_highlights")
    fastest_delivery_label = fields.Char(compute="_compute_rate_highlights")
    origin_branch_count = fields.Integer(compute="_compute_branch_ui_state")
    destination_branch_count = fields.Integer(compute="_compute_branch_ui_state")
    origin_address_count = fields.Integer(compute="_compute_address_ui_state")
    destination_address_count = fields.Integer(compute="_compute_address_ui_state")
    origin_selected_branch_label = fields.Char(compute="_compute_branch_ui_state")
    destination_selected_branch_label = fields.Char(compute="_compute_branch_ui_state")
    origin_branch_load_error = fields.Char(readonly=True)
    # Carriers that returned no rates for the current Pickup route (comma-separated).
    failed_branch_carriers = fields.Char()
    # Carriers that support the current Ship/Pickup route (probed via Envia rates).
    available_branch_carriers = fields.Char()
    branch_carriers_probed = fields.Boolean(default=False)
    rates_feedback = fields.Char(readonly=True)
    destination_branch_load_error = fields.Char(readonly=True)
    show_service_rates = fields.Boolean(compute="_compute_show_service_rates")
    selected_carrier_code = fields.Char(compute="_compute_branch_ui_state")
    show_origin_branch_picker = fields.Boolean(compute="_compute_branch_ui_state")
    show_destination_branch_picker = fields.Boolean(compute="_compute_branch_ui_state")
    can_generate_label = fields.Boolean(compute="_compute_form_state")
    sale_order_state = fields.Selection(related="sale_order_id.state", readonly=True)
    origin_readonly = fields.Boolean(compute="_compute_origin_readonly")
    destination_partner_readonly = fields.Boolean(
        compute="_compute_destination_partner_readonly",
    )

    @api.depends("service_line_ids")
    def _compute_show_service_rates(self):
        for wizard in self:
            wizard.show_service_rates = bool(wizard.service_line_ids)

    @api.depends(
        "origin_location_type",
        "destination_location_type",
        "service_line_ids",
        "service_line_ids.is_selected",
        "service_line_ids.carrier",
        "origin_branch_line_ids",
        "origin_branch_line_ids.is_selected",
        "origin_branch_line_ids.name",
        "destination_branch_line_ids",
        "destination_branch_line_ids.is_selected",
        "destination_branch_line_ids.name",
        "sale_order_id.company_id.envia_enable_branches",
    )
    def _compute_branch_ui_state(self):
        for wizard in self:
            if not wizard.envia_branches_enabled:
                wizard.selected_carrier_code = False
                wizard.show_origin_branch_picker = False
                wizard.show_destination_branch_picker = False
                wizard.origin_branch_count = 0
                wizard.destination_branch_count = 0
                wizard.origin_selected_branch_label = False
                wizard.destination_selected_branch_label = False
                continue
            selected_rate = wizard.service_line_ids.filtered("is_selected")[:1]
            wizard.selected_carrier_code = selected_rate.carrier if selected_rate else False
            # Origin=branch: drop at any branch of the selected carrier — no picker.
            # Destination=branch: pick ocurre after the rate/service is selected.
            wizard.show_origin_branch_picker = False
            wizard.show_destination_branch_picker = bool(
                wizard.destination_location_type == "branch" and selected_rate
            )
            wizard.origin_branch_count = len(wizard.origin_branch_line_ids)
            wizard.destination_branch_count = len(wizard.destination_branch_line_ids)
            origin_selected = wizard.origin_branch_line_ids.filtered("is_selected")[:1]
            destination_selected = wizard.destination_branch_line_ids.filtered("is_selected")[:1]
            wizard.origin_selected_branch_label = origin_selected.name if origin_selected else False
            wizard.destination_selected_branch_label = (
                destination_selected.name if destination_selected else False
            )

    @api.depends("origin_address_line_ids", "destination_address_line_ids")
    def _compute_address_ui_state(self):
        for wizard in self:
            wizard.origin_address_count = len(wizard.origin_address_line_ids)
            wizard.destination_address_count = len(wizard.destination_address_line_ids)

    def _branch_lines(self, side):
        self.ensure_one()
        return (
            self.origin_branch_line_ids
            if side == "origin"
            else self.destination_branch_line_ids
        )

    @staticmethod
    def _partner_address_tree(root_partner):
        if not root_partner:
            return root_partner.browse()
        commercial = root_partner.commercial_partner_id
        return root_partner.search(
            [
                "|",
                ("id", "=", commercial.id),
                ("parent_id", "child_of", commercial.id),
            ]
        )

    @api.depends("sale_order_id", "sale_order_id.state")
    def _compute_origin_readonly(self):
        for wizard in self:
            # Match sale_stock: warehouse on the SO is locked after confirmation.
            wizard.origin_readonly = wizard.sale_order_id.state in ("sale", "done", "cancel")

    @api.depends("sale_order_id")
    def _compute_destination_partner_readonly(self):
        for wizard in self:
            # Sales: destination contact is always the SO Delivery Address.
            wizard.destination_partner_readonly = bool(wizard.sale_order_id)

    @staticmethod
    def _sale_order_destination_partner(sale_order):
        return sale_order.partner_shipping_id or sale_order.partner_id

    def _apply_sale_order_destination(self):
        """Force destination contact (and address when Ship) from SO Delivery Address."""
        for wizard in self:
            order = wizard.sale_order_id
            if not order:
                continue
            destination = wizard._sale_order_destination_partner(order)
            if not destination:
                continue
            values = {"destination_partner_id": destination.id}
            # Ocurre keeps branch address; door delivery mirrors the SO partner.
            if wizard.destination_location_type != "branch":
                values.update(wizard._build_address_defaults(destination, "destination"))
            changed = any(
                wizard._normalize_quote_compare_value(values[field])
                != wizard._normalize_quote_compare_value(getattr(wizard, field))
                for field in values
                if field in wizard._fields
            )
            if not changed:
                continue
            ctx = {
                "envia_skip_auto_quote": True,
                "envia_skip_branch_autoload": True,
            }
            # Partner unchanged → skip address-option reshuffle (UI MissingError).
            if wizard.destination_partner_id == destination:
                ctx["envia_skip_address_sync"] = True
            wizard.with_context(**ctx).write(values)

    @api.depends("sale_order_id", "sale_order_id.company_id", "picking_id", "picking_id.company_id")
    def _compute_allowed_origin_warehouses(self):
        Warehouse = self.env["stock.warehouse"]
        for wizard in self:
            company = (
                wizard.sale_order_id.company_id
                or wizard.picking_id.company_id
                or wizard.env.company
            )
            wizard.allowed_origin_warehouse_ids = Warehouse.search(
                [("company_id", "=", company.id)]
            )

    @api.depends("origin_warehouse_id", "origin_warehouse_id.partner_id", "origin_partner_id")
    def _compute_origin_linked_contact_display(self):
        for wizard in self:
            partner = wizard.origin_warehouse_id.partner_id if wizard.origin_warehouse_id else wizard.origin_partner_id
            wizard.origin_linked_contact_display = partner.display_name if partner else False

    @api.depends("sale_order_id", "picking_id")
    def _compute_allowed_address_partners(self):
        Partner = self.env["res.partner"]
        for wizard in self:
            if wizard.sale_order_id:
                shipping = (
                    wizard.sale_order_id.partner_shipping_id
                    or wizard.sale_order_id.partner_id
                )
                wizard.allowed_destination_partner_ids = wizard._partner_address_tree(
                    shipping
                )
                continue
            if wizard.picking_id:
                wizard.allowed_destination_partner_ids = wizard._partner_address_tree(
                    wizard.picking_id.partner_id
                )
                continue
            wizard.allowed_destination_partner_ids = Partner.search(
                [
                    ("street", "!=", False),
                    ("zip", "!=", False),
                    ("type", "in", ["contact", "delivery", "other", "invoice"]),
                ],
                limit=200,
            )

    _ADDRESS_SYNC_FIELDS = frozenset(
        {
            "origin_warehouse_id",
            "origin_partner_id",
            "destination_partner_id",
            "origin_location_type",
            "destination_location_type",
            "sale_order_id",
            "picking_id",
        }
    )

    def _sync_address_lines(self, side):
        self.ensure_one()
        if side == "origin":
            self._sync_origin_warehouse_lines()
            return
        lines = getattr(self, f"{side}_address_line_ids")
        if getattr(self, f"{side}_location_type") != "address":
            lines.unlink()
            return
        allowed = getattr(self, f"allowed_{side}_partner_ids")
        current_partner = getattr(self, f"{side}_partner_id")
        if current_partner and current_partner.id not in set(allowed.ids):
            allowed = allowed | current_partner
        allowed_ids = set(allowed.ids)
        lines.filtered(lambda line: line.partner_id.id not in allowed_ids).unlink()
        # Re-browse after unlink; stale ids raise MissingError on write.
        lines = getattr(self, f"{side}_address_line_ids")
        existing_partner_ids = set(lines.mapped("partner_id").ids)
        to_create = [
            {
                "wizard_id": self.id,
                "side": side,
                "partner_id": partner.id,
                "is_selected": bool(current_partner and partner.id == current_partner.id),
            }
            for partner in allowed
            if partner.id not in existing_partner_ids
        ]
        if to_create:
            self.env["envia.quote.wizard.address"].create(to_create)
        lines = getattr(self, f"{side}_address_line_ids")
        if current_partner:
            match = lines.filtered(lambda line: line.partner_id.id == current_partner.id)
            if match:
                (lines - match).write({"is_selected": False})
                match.is_selected = True
            elif lines:
                lines[1:].write({"is_selected": False})
                lines[0].is_selected = True
        elif lines:
            lines.write({"is_selected": False})

    def _sync_origin_warehouse_lines(self):
        self.ensure_one()
        lines = self.origin_address_line_ids
        if self.origin_location_type != "address":
            lines.unlink()
            return
        allowed = self.allowed_origin_warehouse_ids
        current_warehouse = self.origin_warehouse_id
        if current_warehouse and current_warehouse.id not in set(allowed.ids):
            allowed = allowed | current_warehouse
        allowed_ids = set(allowed.ids)
        lines.filtered(lambda line: line.warehouse_id.id not in allowed_ids).unlink()
        lines = self.origin_address_line_ids
        existing_warehouse_ids = set(lines.mapped("warehouse_id").ids)
        to_create = []
        for warehouse in allowed:
            if warehouse.id in existing_warehouse_ids or not warehouse.partner_id:
                continue
            to_create.append(
                {
                    "wizard_id": self.id,
                    "side": "origin",
                    "warehouse_id": warehouse.id,
                    "partner_id": warehouse.partner_id.id,
                    "is_selected": bool(
                        current_warehouse and warehouse.id == current_warehouse.id
                    ),
                }
            )
        if to_create:
            self.env["envia.quote.wizard.address"].create(to_create)
        lines = self.origin_address_line_ids
        if current_warehouse:
            match = lines.filtered(
                lambda line: line.warehouse_id.id == current_warehouse.id
            )
            if match:
                (lines - match).write({"is_selected": False})
                match.is_selected = True
            elif lines:
                lines[1:].write({"is_selected": False})
                lines[0].is_selected = True
        elif lines:
            lines.write({"is_selected": False})

    def _sync_all_address_lines(self):
        for wizard in self:
            wizard._sync_address_lines("origin")
            wizard._sync_address_lines("destination")

    @api.depends(
        "origin_postal_code",
        "origin_country_id",
        "origin_state_id",
        "destination_postal_code",
        "destination_country_id",
        "destination_state_id",
    )
    def _compute_route_summary(self):
        for wizard in self:
            origin = wizard._format_route_point(
                wizard.origin_postal_code,
                wizard.origin_state_id,
                wizard.origin_country_id,
            )
            destination = wizard._format_route_point(
                wizard.destination_postal_code,
                wizard.destination_state_id,
                wizard.destination_country_id,
            )
            wizard.route_summary = f"{origin} → {destination}" if origin and destination else ""

    @api.depends(
        "origin_partner_id",
        "destination_partner_id",
        "origin_location_type",
        "destination_location_type",
        "origin_street",
        "origin_street_number",
        "origin_district",
        "destination_street",
        "destination_street_number",
        "destination_district",
        "origin_city",
        "destination_city",
        "origin_postal_code",
        "destination_postal_code",
        "origin_country_id",
        "destination_country_id",
        "origin_state_id",
        "destination_state_id",
    )
    def _compute_address_warnings(self):
        for wizard in self:
            wizard.origin_address_warning = wizard._side_address_missing_message("origin")
            wizard.destination_address_warning = wizard._side_address_missing_message(
                "destination"
            )

    @api.depends(
        "origin_warehouse_id",
        "origin_warehouse_id.envia_origin_id",
        "origin_warehouse_id.envia_origin_id.envia_address_id",
        "origin_location_type",
    )
    def _compute_origin_envia_sync_warning(self):
        for wizard in self:
            wizard.origin_envia_sync_warning = False
            if wizard.origin_location_type == "branch":
                continue
            warehouse = wizard.origin_warehouse_id
            if not warehouse:
                continue
            if warehouse._envia_origin_address_id():
                continue
            wizard.origin_envia_sync_warning = _(
                "This warehouse does not have an Envia origin address yet. "
                "Create or link one on the warehouse to improve quoting. "
                "You can still get rates with the address shown here."
            )

    @api.depends(
        "sale_order_id",
        "sale_order_id.order_line.product_id.weight",
        "picking_id",
        "picking_id.move_ids.product_id.weight",
        "picking_id.move_ids.state",
    )
    def _compute_weight_warning(self):
        for wizard in self:
            products = PayloadMapper.quote_context_products(
                sale_order=wizard.sale_order_id,
                picking=wizard.picking_id,
            )
            wizard.weight_warning = PayloadMapper.missing_weight_warning(products)

    @api.depends("origin_partner_id", "destination_partner_id")
    def _compute_address_previews(self):
        for wizard in self:
            wizard.origin_address_preview = wizard._format_address_preview(wizard.origin_partner_id)
            wizard.destination_address_preview = wizard._format_address_preview(
                wizard.destination_partner_id
            )

    @api.depends("origin_partner_id", "destination_partner_id")
    def _compute_contact_status(self):
        for wizard in self:
            wizard.origin_contact_complete = not bool(
                wizard._partner_missing_message(wizard.origin_partner_id)
            )
            wizard.destination_contact_complete = not bool(
                wizard._partner_missing_message(wizard.destination_partner_id)
            )

    @api.depends(
        "origin_address_warning",
        "destination_address_warning",
        "origin_location_type",
        "destination_location_type",
        "origin_branch_line_ids.is_selected",
        "destination_branch_line_ids.is_selected",
        "origin_branch_load_error",
        "destination_branch_load_error",
        "origin_postal_code",
        "destination_postal_code",
        "origin_street",
        "origin_street_number",
        "origin_district",
        "destination_street",
        "destination_street_number",
        "destination_district",
        "origin_city",
        "destination_city",
        "origin_country_id",
        "destination_country_id",
        "origin_state_id",
        "destination_state_id",
        "weight",
        "selected_service_label",
        "sale_order_id.company_id.envia_enable_labels",
    )
    def _compute_form_state(self):
        for wizard in self:
            # Get rate first (addresses + ZIP); destination ocurre is chosen after
            # the service card, then branch_code comes from that destination branch.
            errors = wizard._collect_validation_errors(require_branches=False)
            if wizard.destination_location_type == "branch" and wizard.destination_branch_load_error:
                errors = [wizard.destination_branch_load_error] + errors
            elif wizard.origin_location_type == "branch" and wizard.origin_branch_load_error:
                errors = [wizard.origin_branch_load_error] + errors
            wizard.can_get_rates = not errors
            label_errors = wizard._collect_validation_errors(
                require_branches=wizard.destination_location_type == "branch"
            )
            wizard.can_generate_label = bool(
                wizard.envia_enable_labels
                and wizard.selected_service_label
                and not label_errors
            )
            wizard.validation_summary = "\n".join(f"• {error}" for error in errors) if errors else False
            wizard.blocking_message = errors[0] if errors else False

    @api.depends("origin_country_id", "destination_country_id")
    def _compute_route_flags(self):
        company = self.env.company
        for wizard in self:
            wizard.is_international_route = bool(
                wizard.origin_country_id
                and wizard.destination_country_id
                and wizard.origin_country_id != wizard.destination_country_id
            )
            wizard.is_domestic_route = bool(
                wizard.origin_country_id
                and wizard.destination_country_id
                and wizard.origin_country_id == wizard.destination_country_id
            )
            wizard.is_sandbox = company._envia_is_sandbox()

    @api.depends("sale_order_id", "picking_id")
    def _compute_is_standalone(self):
        for wizard in self:
            wizard.is_standalone = not wizard.sale_order_id and not wizard.picking_id

    @api.depends("service_line_ids", "service_line_ids.price", "service_line_ids.estimated_delivery_days")
    def _compute_rate_highlights(self):
        for wizard in self:
            lines = wizard.service_line_ids
            if not lines:
                wizard.cheapest_rate_label = False
                wizard.fastest_delivery_label = False
                continue
            cheapest = min(lines, key=lambda line: line.price or 0.0)
            wizard.cheapest_rate_label = _(
                "Best price: %(carrier)s · %(price).2f %(currency)s",
                carrier=cheapest.carrier_name or cheapest.carrier,
                price=cheapest.price,
                currency=cheapest.currency_name or wizard.currency_id.name,
            )
            with_eta = lines.filtered(lambda line: line.estimated_delivery_days)
            if with_eta:
                fastest = min(with_eta, key=lambda line: line.estimated_delivery_days)
                wizard.fastest_delivery_label = _(
                    "Fastest: %(carrier)s · %(days)s day(s)",
                    carrier=fastest.carrier_name or fastest.carrier,
                    days=fastest.estimated_delivery_days,
                )
            else:
                wizard.fastest_delivery_label = False

    @api.depends(
        "sale_order_id",
        "sale_order_id.company_id",
        "sale_order_id.company_id.envia_enable_labels",
        "picking_id",
        "picking_id.company_id",
        "picking_id.company_id.envia_enable_labels",
    )
    def _compute_envia_company_flags(self):
        for wizard in self:
            company = (
                wizard.sale_order_id.company_id
                or wizard.picking_id.company_id
                or wizard.env.company
            )
            wizard.envia_enable_labels = company.envia_enable_labels
            wizard.envia_show_quote_archive = company.envia_show_quote_archive
            wizard.envia_branches_enabled = company.envia_enable_branches

    @api.depends("service_line_ids", "service_line_ids.is_selected")
    def _compute_selected_service_label(self):
        for wizard in self:
            selected = wizard.service_line_ids.filtered("is_selected")[:1]
            if not selected:
                wizard.selected_service_label = False
                continue
            wizard.selected_service_label = _(
                "%(carrier)s · %(service)s · %(price).2f %(currency)s",
                carrier=selected.carrier_name or selected.carrier,
                service=selected.service_name,
                price=selected.price,
                currency=selected.currency_name or wizard.currency_id.name,
            )

    @api.depends("service_line_ids")
    def _compute_service_count(self):
        for wizard in self:
            wizard.service_count = len(wizard.service_line_ids)

    @api.model
    def default_get(self, fields_list):
        defaults = super().default_get(fields_list)
        company = self.env.company
        sale_order_id = self.env.context.get("default_sale_order_id")
        sale_order = self.env["sale.order"].browse(sale_order_id) if sale_order_id else False
        # Sales: origin is the Delivery warehouse on the order.
        # ponytail: skip company.envia_default_origin_warehouse_id until re-enabled.
        warehouse = sale_order.warehouse_id if sale_order else False
        if not warehouse:
            warehouse = self.env["stock.warehouse"].search(
                [("company_id", "=", company.id)],
                limit=1,
            )
        if warehouse:
            defaults["origin_warehouse_id"] = warehouse.id
            origin_partner = warehouse.partner_id or company._envia_get_default_origin_partner()
        else:
            origin_partner = company._envia_get_default_origin_partner()
        defaults["origin_partner_id"] = origin_partner.id
        if origin_partner:
            defaults.update(
                self._build_address_defaults(origin_partner, "origin", company.country_id)
            )
        destination_partner_id = self.env.context.get("default_destination_partner_id")
        if sale_order:
            destination = self._sale_order_destination_partner(sale_order)
            if destination:
                destination_partner_id = destination.id
        if destination_partner_id:
            defaults["destination_partner_id"] = destination_partner_id
            destination_partner = self.env["res.partner"].browse(destination_partner_id)
            defaults.update(self._build_address_defaults(destination_partner, "destination"))
        if sale_order:
            defaults["sale_order_id"] = sale_order.id
            defaults["content"] = PayloadMapper.sale_order_package_content(sale_order)
            defaults["declared_value"] = PayloadMapper.sale_order_declared_value(sale_order)
            defaults["weight"] = PayloadMapper.sale_order_package_weight(sale_order)
        if not defaults.get("origin_country_id") and company.country_id:
            defaults["origin_country_id"] = company.country_id.id
        if not company.envia_enable_branches:
            defaults["origin_location_type"] = "address"
            defaults["destination_location_type"] = "address"
        return defaults

    def _envia_branches_enabled(self):
        self.ensure_one()
        company = self.sale_order_id.company_id if self.sale_order_id else self.env.company
        return company.envia_enable_branches

    def _enforce_address_only_route(self):
        skip = {"envia_skip_auto_quote": True, "envia_skip_route_carrier_refresh": True}
        for wizard in self:
            if wizard._envia_branches_enabled():
                continue
            if (
                wizard.origin_location_type != "address"
                or wizard.destination_location_type != "address"
            ):
                wizard.with_context(**skip).write(
                    {
                        "origin_location_type": "address",
                        "destination_location_type": "address",
                    }
                )
            if wizard.origin_branch_line_ids or wizard.destination_branch_line_ids:
                wizard._clear_branch_lines()
            if (
                not wizard.is_seeded_from_order
                and wizard.service_line_ids
                and any(
                    not wizard._rate_drop_off_matches_route(line.drop_off)
                    for line in wizard.service_line_ids
                )
            ):
                wizard._clear_stale_rates_for_route()

    def _expected_route_drop_off(self):
        """Envia dropOff for the wizard Ship/Pickup selection (None = Ship-Ship)."""
        self.ensure_one()
        if not self._envia_branches_enabled():
            return None
        origin_branch = self.origin_location_type == "branch"
        destination_branch = self.destination_location_type == "branch"
        if origin_branch and destination_branch:
            return 3
        if origin_branch:
            return 1
        if destination_branch:
            return 2
        return None

    def _rate_drop_off_matches_route(self, drop_off):
        expected = self._expected_route_drop_off()
        return (drop_off or 0) == (expected or 0)

    def _clear_stale_rates_for_route(self):
        """Drop cached/quoted rates when Ship/Pickup no longer matches."""
        self.ensure_one()
        self.rates_feedback = False
        if self.id:
            self.with_context(envia_skip_auto_quote=True).write({"quote_id": False})
            self.service_line_ids.unlink()
        else:
            self.service_line_ids = [(5, 0, 0)]

    def _clear_stale_rates_if_route_mismatch(self):
        self.ensure_one()
        if not self.service_line_ids:
            return
        if all(self._rate_drop_off_matches_route(line.drop_off) for line in self.service_line_ids):
            return
        self._clear_stale_rates_for_route()

    def _get_branch_carrier_codes(self, country):
        carriers = self.env["envia.carrier"].search([("active", "=", True)])
        if country:
            country_code = country.code
            carriers = carriers.filtered(
                lambda carrier: not carrier.country_codes
                or country_code in [code.strip() for code in carrier.country_codes.split(",")]
            )
        codes = carriers.mapped("code")
        # Hide carriers that already failed Get rate for this Pickup route.
        failed = {
            code.strip()
            for code in (self.failed_branch_carriers or "").split(",")
            if code.strip()
        }
        codes = [code for code in codes if code not in failed]
        # After probing, only carriers that returned rates for this route type.
        if self.branch_carriers_probed:
            available = {
                code.strip()
                for code in (self.available_branch_carriers or "").split(",")
                if code.strip()
            }
            codes = [code for code in codes if code in available]
        return codes

    def _can_probe_branch_route(self):
        self.ensure_one()
        return bool(
            self._uses_branch_route()
            and self.origin_postal_code
            and self.destination_postal_code
            and self.origin_country_id
            and self.destination_country_id
        )

    def _probe_contact_for_side(self, side):
        """Contact used to ask Envia which carriers support this Ship/Pickup route."""
        self.ensure_one()
        country = getattr(self, f"{side}_country_id")
        state = getattr(self, f"{side}_state_id")
        postal = getattr(self, f"{side}_postal_code") or ""
        city = getattr(self, f"{side}_city") or "City"
        company_partner = self.env.company.partner_id
        is_branch = getattr(self, f"{side}_location_type") == "branch"
        return Contact(
            name=company_partner.name or "Shipper",
            street=city,
            city=city,
            district=PayloadMapper._resolve_district(state=state) or None,
            state=EnviaOfficialAdapter.envia_state_code(
                country.code if country else "",
                state.code if state else "",
            ),
            postal_code=postal,
            country=country.code if country else "",
            phone=company_partner.phone or "5555555555",
            email=company_partner.email or "shipping@company.com",
            branch_code="PROBE" if is_branch else None,
        )

    def _probe_branch_route_carriers(self, expected_drop_off):
        """Carriers that return at least one rate for the selected Pickup route."""
        self.ensure_one()
        mapper = PayloadMapper()
        origin_contact = self._probe_contact_for_side("origin")
        destination_contact = self._probe_contact_for_side("destination")
        request = mapper.build_quote_request_from_values(
            {
                "origin_postal_code": self.origin_postal_code,
                "origin_country": self.origin_country_id.code,
                "origin_state": self._side_envia_state("origin", origin_contact),
                "destination_postal_code": self.destination_postal_code,
                "destination_country": self.destination_country_id.code,
                "destination_state": self._side_envia_state("destination", destination_contact),
                "weight": PayloadMapper.normalize_package_weight(self.weight),
                "weight_unit": PayloadMapper.envia_weight_unit(self.env),
                "content": self.content or "General merchandise",
                "declared_value": self.declared_value or 0,
                "currency": self.currency_id.name,
                "carriers": "all",
                "origin_contact": origin_contact,
                "destination_contact": destination_contact,
                "items": mapper.quote_items_for_context(
                    self.sale_order_id,
                    self.picking_id,
                ),
            }
        )
        response = get_envia_adapter(self.env.company).quote(request)
        return sorted(
            {
                service.carrier
                for service in response.services
                if service.carrier and service.drop_off == expected_drop_off
            }
        )

    def _refresh_available_branch_carriers(self):
        """Probe Envia and keep only carriers that support the current route type."""
        self.ensure_one()
        skip = {
            "envia_skip_auto_quote": True,
            "envia_skip_branch_autoload": True,
            "envia_skip_route_carrier_refresh": True,
        }
        expected = self._expected_route_drop_off()
        if expected is None or not self._can_probe_branch_route():
            self.with_context(**skip).write(
                {
                    "branch_carriers_probed": False,
                    "available_branch_carriers": False,
                }
            )
            return
        try:
            carriers = self._probe_branch_route_carriers(expected)
        except UserError:
            # Probe failed (network/token): do not restrict the branch list.
            self.with_context(**skip).write(
                {
                    "branch_carriers_probed": False,
                    "available_branch_carriers": False,
                    "rates_feedback": False,
                }
            )
            return
        feedback = False
        if not carriers:
            feedback = _(
                "No carriers offer %(route)s rates for this route. "
                "Try Ship or another postal code."
            ) % {"route": self._route_type_label_for_wizard()}
        self.with_context(**skip).write(
            {
                "branch_carriers_probed": True,
                "available_branch_carriers": ",".join(carriers),
                "failed_branch_carriers": False,
                "rates_feedback": feedback,
            }
        )

    def _on_route_type_changed(self):
        """Clear stale rates when Ship/Pickup changes; route type drives dropOff at quote time."""
        self.ensure_one()
        self.with_context(
            envia_skip_auto_quote=True,
            envia_skip_branch_autoload=True,
            envia_skip_route_carrier_refresh=True,
        ).write(
            {
                "failed_branch_carriers": False,
                "rates_feedback": False,
                "branch_carriers_probed": False,
                "available_branch_carriers": False,
            }
        )
        self._clear_branch_lines()
        self._clear_stale_rates_for_route()
        if not self.env.context.get("envia_skip_branch_autoload"):
            self._auto_reload_branch_options()

    @api.model_create_multi
    def create(self, vals_list):
        company = self.env.company
        for vals in vals_list:
            sale_order_id = vals.get("sale_order_id")
            if sale_order_id:
                sale_order = self.env["sale.order"].browse(sale_order_id)
                if sale_order.warehouse_id:
                    vals["origin_warehouse_id"] = sale_order.warehouse_id.id
                destination = self._sale_order_destination_partner(sale_order)
                if destination:
                    vals["destination_partner_id"] = destination.id
            origin_warehouse_id = vals.get("origin_warehouse_id")
            if origin_warehouse_id:
                warehouse = self.env["stock.warehouse"].browse(origin_warehouse_id)
                partner = warehouse.partner_id
                if partner:
                    vals["origin_partner_id"] = partner.id
                    self._merge_address_defaults(
                        vals,
                        self._build_address_defaults(partner, "origin", company.country_id),
                    )
            else:
                origin_partner_id = vals.get("origin_partner_id")
                if origin_partner_id:
                    partner = self.env["res.partner"].browse(origin_partner_id)
                    self._merge_address_defaults(
                        vals,
                        self._build_address_defaults(partner, "origin", company.country_id),
                    )
                elif not vals.get("origin_country_id") and company.country_id:
                    vals["origin_country_id"] = company.country_id.id

            destination_partner_id = vals.get("destination_partner_id")
            if destination_partner_id:
                partner = self.env["res.partner"].browse(destination_partner_id)
                self._merge_address_defaults(
                    vals,
                    self._build_address_defaults(partner, "destination"),
                )
        records = super().create(vals_list)
        for wizard in records:
            wizard._sync_partner_address_fields()
            wizard._sync_all_address_lines()
            wizard._enforce_address_only_route()
        return records

    def _sync_partner_address_fields(self):
        self.ensure_one()
        for side in ("origin", "destination"):
            partner = getattr(self, f"{side}_partner_id")
            if not partner:
                continue
            fallback = self.env.company.country_id if side == "origin" else None
            values = self._build_address_defaults(partner, side, fallback)
            self.with_context(
                envia_skip_auto_quote=True,
                envia_skip_branch_autoload=True,
            ).write({k: v for k, v in values.items() if v and not getattr(self, k)})
            if not getattr(self, f"{side}_state_id") and getattr(self, f"{side}_postal_code"):
                geo = self._geocode_side_values(side)
                if geo:
                    self.with_context(
                        envia_skip_auto_quote=True,
                        envia_skip_branch_autoload=True,
                    ).write(geo)

    _BRANCH_RELOAD_FIELDS = frozenset(
        {
            "origin_postal_code",
            "destination_postal_code",
            "origin_location_type",
            "destination_location_type",
            "origin_country_id",
            "destination_country_id",
            "weight",
            "content",
            "origin_partner_id",
            "destination_partner_id",
        }
    )

    _AUTO_QUOTE_FIELDS = frozenset(
        {
            "origin_postal_code",
            "destination_postal_code",
            "origin_partner_id",
            "destination_partner_id",
            "origin_location_type",
            "destination_location_type",
            "origin_state_id",
            "destination_state_id",
            "origin_city",
            "destination_city",
            "origin_street",
            "origin_street_number",
            "origin_district",
            "destination_street",
            "destination_street_number",
            "destination_district",
            "origin_country_id",
            "destination_country_id",
            "weight",
            "content",
        }
    )
    # Ship↔Pickup keeps the same ZIP; keep rates so the user can pick a branch.
    _RATE_INVALIDATING_FIELDS = _AUTO_QUOTE_FIELDS - {
        "origin_location_type",
        "destination_location_type",
    }

    def _is_ready_for_auto_quote(self):
        self.ensure_one()
        return (
            self.can_get_rates
            and self.origin_location_type == "address"
            and self.destination_location_type == "address"
        )

    def _clear_quote_results(self):
        self.ensure_one()
        self.service_line_ids.unlink()
        self.write({"step": "address", "quote_id": False})

    @staticmethod
    def _normalize_quote_compare_value(value):
        if isinstance(value, models.BaseModel):
            return value.id or False
        if value in (None, False, ""):
            return False
        if isinstance(value, float):
            return round(value, 6)
        if isinstance(value, str):
            return value.strip()
        return value

    def _quote_route_values_changed(self, vals, fields=None):
        self.ensure_one()
        fields = fields or self._AUTO_QUOTE_FIELDS
        for field in fields:
            if field not in vals:
                continue
            if self._normalize_quote_compare_value(vals[field]) != self._normalize_quote_compare_value(
                getattr(self, field)
            ):
                return True
        return False

    def write(self, vals):
        vals = dict(vals)
        if not self.env.context.get("envia_allow_branch_location_type"):
            for wizard in self:
                if wizard._envia_branches_enabled():
                    continue
                if vals.get("origin_location_type") == "branch":
                    vals["origin_location_type"] = "address"
                if vals.get("destination_location_type") == "branch":
                    vals["destination_location_type"] = "address"
        # ponytail: web save re-sends Ship/Pickup on the parent modal; only react to real changes.
        if "service_line_ids" in vals and not self.env.context.get(
            "envia_allow_service_line_write"
        ):
            vals.pop("service_line_ids")
        route_type_fields = {"origin_location_type", "destination_location_type"}
        route_type_wizards = self.filtered(
            lambda wizard: wizard._quote_route_values_changed(vals, route_type_fields)
        )
        for wizard in self:
            for side in ("origin", "destination"):
                if vals.get(f"{side}_location_type") != "branch":
                    continue
                postal_field = f"{side}_postal_code"
                if vals.get(postal_field) or getattr(wizard, postal_field):
                    continue
                partner = getattr(wizard, f"{side}_partner_id")
                if partner and partner.zip:
                    country = (
                        getattr(wizard, f"{side}_country_id")
                        or partner.country_id
                        or self.env.company.country_id
                    )
                    vals[postal_field] = wizard._normalize_postal_code(
                        country.code if country else None,
                        partner.zip,
                    )
            for side in ("origin", "destination"):
                if side == "origin" and vals.get("origin_warehouse_id"):
                    warehouse = self.env["stock.warehouse"].browse(vals["origin_warehouse_id"])
                    partner = warehouse.partner_id
                    if partner:
                        vals["origin_partner_id"] = partner.id
                        wizard._merge_address_defaults(
                            vals,
                            wizard._build_address_defaults(
                                partner, "origin", self.env.company.country_id
                            ),
                        )
                    continue
                partner_field = f"{side}_partner_id"
                if partner_field not in vals or vals.get(f"{side}_location_type", getattr(wizard, f"{side}_location_type")) != "address":
                    continue
                partner = self.env["res.partner"].browse(vals[partner_field]) if vals[partner_field] else False
                if not partner:
                    continue
                fallback = self.env.company.country_id if side == "origin" else None
                wizard._merge_address_defaults(
                    vals,
                    wizard._build_address_defaults(partner, side, fallback),
                )
        if not self.env.context.get("envia_skip_auto_quote"):
            for wizard in self:
                if not wizard._AUTO_QUOTE_FIELDS.intersection(vals):
                    continue
                if not self.env.context.get("envia_apply_branch"):
                    if "origin_postal_code" in vals and wizard.origin_location_type == "branch":
                        wizard.origin_branch_line_ids.unlink()
                    if "destination_postal_code" in vals and wizard.destination_location_type == "branch":
                        wizard.destination_branch_line_ids.unlink()
                if (
                    not self.env.context.get("envia_apply_branch")
                    and wizard.service_line_ids
                    and wizard._quote_route_values_changed(vals, self._RATE_INVALIDATING_FIELDS)
                ):
                    wizard.with_context(envia_skip_auto_quote=True)._clear_quote_results()
        res = super().write(vals)
        if (
            not self.env.context.get("envia_skip_address_sync")
            and self._ADDRESS_SYNC_FIELDS.intersection(vals)
        ):
            self._sync_all_address_lines()
        if (
            route_type_wizards
            and not self.env.context.get("envia_skip_route_carrier_refresh")
        ):
            for wizard in route_type_wizards:
                wizard._on_route_type_changed()
        self._enforce_address_only_route()
        return res

    @staticmethod
    def _merge_address_defaults(vals, address_defaults):
        for field_name, value in address_defaults.items():
            if field_name not in vals or not vals.get(field_name):
                vals[field_name] = value

    @staticmethod
    def _normalize_postal_code(country_code, zipcode):
        if zipcode in (None, False, ""):
            return zipcode
        normalized = str(zipcode).strip()
        if country_code == "MX" and normalized.isdigit() and len(normalized) < 5:
            return normalized.zfill(5)
        return normalized

    @staticmethod
    def _postal_ready_for_branch_search(country, zipcode):
        if not country or not zipcode or not str(zipcode).strip():
            return False
        normalized = EnviaQuoteWizard._normalize_postal_code(country.code, zipcode)
        return len(normalized) >= 4

    def _ensure_branch_side_country(self, side):
        self.ensure_one()
        country = getattr(self, f"{side}_country_id")
        if country:
            return country
        country = self.env.company.country_id
        if country:
            self.with_context(envia_skip_branch_autoload=True).write(
                {f"{side}_country_id": country.id}
            )
        return country

    def _auto_reload_branch_options(self, sides=None):
        self.ensure_one()
        sides = sides or [
            side
            for side in ("origin", "destination")
            if getattr(self, f"{side}_location_type") == "branch"
        ]
        for side in sides:
            if not self._branch_side_stale(side):
                self._refresh_branch_carrier_rates()
                continue
            self._try_load_branch_side(side)

    def _try_load_branch_side(self, side, carrier_code=None):
        self.ensure_one()
        error_field = f"{side}_branch_load_error"
        if not self._side_needs_branch_selection(side):
            self._set_branch_load_error(error_field, False)
            self._branch_lines(side).unlink()
            return False
        if getattr(self, f"{side}_location_type") != "branch":
            self._set_branch_load_error(error_field, False)
            return False
        country = self._ensure_branch_side_country(side)
        zipcode = getattr(self, f"{side}_postal_code")
        if not self._postal_ready_for_branch_search(country, zipcode):
            self._set_branch_load_error(error_field, False)
            return False
        if (
            self._uses_branch_route()
            and self.branch_carriers_probed
            and not self._get_branch_carrier_codes(country)
            and not carrier_code
        ):
            self._set_branch_load_error(
                error_field,
                _(
                    "No carriers offer %(route)s rates for this route. "
                    "Try Ship or another postal code."
                )
                % {"route": self._route_type_label_for_wizard()},
            )
            return False
        try:
            self.with_context(
                envia_skip_auto_quote=True,
                envia_skip_branch_autoload=True,
            )._load_branches(side, carrier_codes=carrier_code)
        except UserError as error:
            self._set_branch_load_error(error_field, str(error))
            return False
        self._set_branch_load_error(error_field, False)
        self._refresh_branch_carrier_rates()
        return True

    def _load_branches_for_selected_carrier(self):
        self.ensure_one()
        selected = self.service_line_ids.filtered("is_selected")[:1]
        if not selected or not selected.carrier:
            return
        # Rate-first Branch/B2B: after service, load only destination ocurre branches.
        if self._side_needs_branch_selection("destination") and not self._branch_lines(
            "destination"
        ):
            self._try_load_branch_side("destination", carrier_code=selected.carrier)

    def _clear_branch_lines(self, sides=None):
        self.ensure_one()
        for side in sides or ("origin", "destination"):
            self._branch_lines(side).unlink()
            self._set_branch_load_error(f"{side}_branch_load_error", False)

    def _set_branch_load_error(self, field_name, message):
        if self.env.context.get("envia_in_onchange"):
            setattr(self, field_name, message or False)
            return
        self.with_context(envia_skip_branch_autoload=True).write({field_name: message or False})

    def _refresh_branch_carrier_rates(self):
        self.ensure_one()
        for side in ("origin", "destination"):
            if getattr(self, f"{side}_location_type") == "branch" and self._branch_lines(side):
                self._attach_branch_carrier_rates(side)

    def _can_preview_branch_rates(self, branch_side):
        self.ensure_one()
        if self.weight <= 0:
            return False
        if not self.env.company._envia_get_shipping_api_token():
            return False
        other = "destination" if branch_side == "origin" else "origin"
        if getattr(self, f"{other}_location_type") == "address":
            if getattr(self, f"{other}_address_warning") or not getattr(
                self, f"{other}_partner_id"
            ):
                return False
        elif not self._get_selected_branch(other):
            return False
        country = getattr(self, f"{branch_side}_country_id") or self.env.company.country_id
        return bool(country and getattr(self, f"{branch_side}_postal_code"))

    def _build_quote_request_for_branch(self, branch):
        self.ensure_one()
        origin_override = branch if branch.side == "origin" else None
        destination_override = branch if branch.side == "destination" else None
        origin_contact = self._build_contact_for_side(
            "origin", for_quote=True, branch_override=origin_override
        )
        destination_contact = self._build_contact_for_side(
            "destination", for_quote=True, branch_override=destination_override
        )
        mapper = PayloadMapper()
        return mapper.build_quote_request_from_values(
            {
                "origin_postal_code": self.origin_postal_code,
                "origin_country": self.origin_country_id.code,
                "origin_state": self._side_envia_state("origin", origin_contact),
                "destination_postal_code": self.destination_postal_code,
                "destination_country": self.destination_country_id.code,
                "destination_state": self._side_envia_state("destination", destination_contact),
                "weight": self.weight,
                "weight_unit": PayloadMapper.envia_weight_unit(self.env),
                "content": self.content,
                "declared_value": self.declared_value,
                "currency": self.currency_id.name,
                "carriers": branch.carrier,
                "origin_contact": origin_contact,
                "destination_contact": destination_contact,
                "items": mapper.quote_items_for_context(
                    self.sale_order_id,
                    self.picking_id,
                ),
            }
        )

    def _attach_branch_carrier_rates(self, side):
        self.ensure_one()
        branch_lines = self._branch_lines(side)
        if not branch_lines or not self._can_preview_branch_rates(side):
            branch_lines.write(
                {
                    "price": 0.0,
                    "currency_name": False,
                    "estimated_delivery_days": 0,
                    "service_id": False,
                    "service_name": False,
                }
            )
            return
        adapter = get_envia_adapter(self.env.company)
        proxy_by_carrier = {}
        for branch in branch_lines:
            if branch.carrier and branch.carrier not in proxy_by_carrier:
                proxy_by_carrier[branch.carrier] = branch
        rates_by_carrier = {}
        failed_carriers = set()
        for carrier, proxy_branch in proxy_by_carrier.items():
            try:
                request = self._build_quote_request_for_branch(proxy_branch)
                response = adapter.quote(request)
            except UserError:
                # Network/token error: keep the branch; do not treat as unrateable.
                continue
            if not response.services:
                failed_carriers.add(carrier)
                continue
            expected_drop_off = EnviaOfficialAdapter._expected_drop_off(
                request.origin_contact,
                request.destination_contact,
            )
            service = EnviaOfficialAdapter.pick_cheapest_service(
                response.services,
                expected_drop_off,
            )
            if service:
                rates_by_carrier[carrier] = service
            else:
                failed_carriers.add(carrier)
        # Other pickup already chosen: drop carriers that cannot quote this pair.
        if failed_carriers:
            branch_lines.filtered(lambda line: line.carrier in failed_carriers).unlink()
            branch_lines = self._branch_lines(side)
        for branch in branch_lines:
            service = rates_by_carrier.get(branch.carrier)
            if not service:
                branch.write(
                    {
                        "price": 0.0,
                        "currency_name": False,
                        "estimated_delivery_days": 0,
                        "service_id": False,
                        "service_name": False,
                    }
                )
                continue
            branch.write(
                {
                    "price": service.price,
                    "currency_name": service.currency,
                    "estimated_delivery_days": service.estimated_delivery_days,
                    "service_id": service.service_id,
                    "envia_service_id": service.envia_service_id,
                    "service_name": service.service_name,
                }
            )

    def _build_address_defaults(self, partner, prefix, fallback_country=None):
        if not partner:
            return {}
        country = partner.country_id or fallback_country
        state = partner.state_id
        if state and country and state.country_id != country:
            state = self.env["res.country.state"]
        number, district, _interior = PayloadMapper._partner_address_extras(partner)
        return {
            f"{prefix}_street": partner.street or "",
            f"{prefix}_street_number": number or "",
            f"{prefix}_district": district or (state.name if state else ""),
            f"{prefix}_postal_code": self._normalize_postal_code(
                country.code if country else None,
                partner.zip or "",
            ),
            f"{prefix}_city": partner.city or "",
            f"{prefix}_country_id": country.id if country else False,
            f"{prefix}_state_id": state.id if state else False,
        }

    @staticmethod
    def _format_address_preview(partner):
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

    @staticmethod
    def _partner_missing_message(partner):
        if not partner:
            return _("Select a contact to load the address automatically.")
        missing = []
        if not partner.street:
            missing.append(_("street"))
        if not partner.city:
            missing.append(_("city"))
        if not partner.zip:
            missing.append(_("postal code"))
        if not partner.country_id:
            missing.append(_("country"))
        if partner.country_id and partner.state_id and partner.state_id.country_id != partner.country_id:
            missing.append(_("state matching country"))
        if not partner.phone and not getattr(partner, "mobile", False):
            missing.append(_("phone"))
        if not partner.email:
            missing.append(_("email"))
        if not missing:
            return False
        return _("Missing on contact: %s") % ", ".join(missing)

    def _side_address_missing_message(self, side):
        self.ensure_one()
        partner = getattr(self, f"{side}_partner_id")
        if not partner:
            return _("Select a contact to load the address automatically.")
        missing = []
        if not getattr(self, f"{side}_street"):
            missing.append(_("street"))
        if not getattr(self, f"{side}_city"):
            missing.append(_("city"))
        if not getattr(self, f"{side}_postal_code"):
            missing.append(_("postal code"))
        if not getattr(self, f"{side}_country_id"):
            missing.append(_("country"))
        state = getattr(self, f"{side}_state_id")
        country = getattr(self, f"{side}_country_id")
        if country and country.state_ids and not state:
            missing.append(_("state"))
        elif state and country and state.country_id != country:
            missing.append(_("state matching country"))
        if not missing:
            return False
        return _("Missing address fields: %s") % ", ".join(missing)

    @staticmethod
    def _partner_hard_missing_message(partner):
        if not partner:
            return _("Select a contact to load the address automatically.")
        missing = []
        if not partner.street:
            missing.append(_("street"))
        if not partner.city:
            missing.append(_("city"))
        if not partner.zip:
            missing.append(_("postal code"))
        if not partner.country_id:
            missing.append(_("country"))
        if partner.country_id and partner.state_id and partner.state_id.country_id != partner.country_id:
            missing.append(_("state matching country"))
        if not missing:
            return False
        return _("Missing on contact: %s") % ", ".join(missing)

    @staticmethod
    def _format_route_point(postal_code, state, country):
        if not postal_code or not country:
            return ""
        state_code = state.code if state else "?"
        return f"{postal_code} {state_code}, {country.code}"

    def _apply_warehouse_origin(self, warehouse):
        if not warehouse:
            return
        partner = warehouse.partner_id
        if not partner:
            return
        self.origin_warehouse_id = warehouse
        self.origin_partner_id = partner
        self._apply_partner_address(partner, "origin")

    def _apply_partner_address(self, partner, prefix):
        if not partner:
            return
        fallback_country = self.env.company.country_id if prefix == "origin" else None
        values = self._build_address_defaults(partner, prefix, fallback_country)
        for field_name, value in values.items():
            setattr(self, field_name, value)

    @api.onchange("origin_warehouse_id")
    def _onchange_origin_warehouse_id(self):
        self._apply_warehouse_origin(self.origin_warehouse_id)

    @api.onchange("origin_partner_id")
    def _onchange_origin_partner_id(self):
        self._apply_partner_address(self.origin_partner_id, "origin")

    @api.onchange("destination_partner_id")
    def _onchange_destination_partner_id(self):
        self._apply_partner_address(self.destination_partner_id, "destination")

    @api.onchange("origin_country_id")
    def _onchange_origin_country_id(self):
        if self.origin_state_id and self.origin_state_id.country_id != self.origin_country_id:
            self.origin_state_id = False

    @api.onchange("destination_country_id")
    def _onchange_destination_country_id(self):
        if self.destination_state_id and self.destination_state_id.country_id != self.destination_country_id:
            self.destination_state_id = False

    @api.onchange("origin_postal_code", "origin_country_id", "origin_location_type")
    def _onchange_origin_postal_code(self):
        if self.origin_location_type == "branch":
            self._onchange_prepare_branch_side("origin")
            return
        self._apply_geocode("origin")

    @api.onchange("destination_postal_code", "destination_country_id", "destination_location_type")
    def _onchange_destination_postal_code(self):
        if self.destination_location_type == "branch":
            self._onchange_prepare_branch_side("destination")
            return
        self._apply_geocode("destination")

    def _onchange_prepare_branch_side(self, side):
        prefix = side
        if side == "destination" and self.destination_partner_id and not self.destination_postal_code:
            self.destination_postal_code = self.destination_partner_id.zip
        if side == "origin" and self.origin_warehouse_id and not self.origin_postal_code:
            self.origin_postal_code = self.origin_warehouse_id.partner_id.zip
        elif side == "origin" and self.origin_partner_id and not self.origin_postal_code:
            self.origin_postal_code = self.origin_partner_id.zip
        if not getattr(self, f"{prefix}_country_id") and self.env.company.country_id:
            setattr(self, f"{prefix}_country_id", self.env.company.country_id)

    @api.onchange("origin_location_type")
    def _onchange_origin_location_type(self):
        self._clear_stale_rates_if_route_mismatch()
        if self.origin_location_type != "branch":
            if self.id:
                self._branch_lines("origin").unlink()
            return
        self._clear_branch_side_on_mode_change("origin")
        self._onchange_prepare_branch_side("origin")

    @api.onchange("destination_location_type")
    def _onchange_destination_location_type(self):
        self._clear_stale_rates_if_route_mismatch()
        if self.destination_location_type != "branch":
            if self.id:
                self._branch_lines("destination").unlink()
            return
        self._clear_branch_side_on_mode_change("destination")
        self._onchange_prepare_branch_side("destination")

    def _clear_branch_side_on_mode_change(self, side):
        if self.id:
            self._branch_lines(side).unlink()

    def action_lookup_origin_zipcode(self):
        self.ensure_one()
        self._apply_geocode("origin", force=True)
        return self._wizard_action()

    def action_lookup_destination_zipcode(self):
        self.ensure_one()
        self._apply_geocode("destination", force=True)
        return self._wizard_action()

    def action_load_origin_branches(self):
        self.ensure_one()
        if self._uses_branch_route() and not self.branch_carriers_probed:
            self._refresh_available_branch_carriers()
        self._try_load_branch_side("origin", carrier_code=self._pickup_carrier_code())
        return self._wizard_action()

    def action_load_destination_branches(self):
        self.ensure_one()
        if self._uses_branch_route() and not self.branch_carriers_probed:
            self._refresh_available_branch_carriers()
        self._try_load_branch_side("destination", carrier_code=self._pickup_carrier_code())
        return self._wizard_action()

    def action_reload_branch_view(self):
        return self.action_refresh_wizard_view()

    def action_refresh_wizard_view(self):
        self.ensure_one()
        self._sync_partner_address_fields()
        self._sync_all_address_lines()
        branch_sides = [
            side
            for side in ("origin", "destination")
            if getattr(self, f"{side}_location_type") == "branch"
        ]
        if branch_sides:
            if self.selected_carrier_code:
                self._load_branches_for_selected_carrier()
        if self._is_ready_for_auto_quote() and not self.service_line_ids:
            self.with_context(envia_skip_auto_quote=True)._perform_get_quote()
        return self._wizard_action()

    def _resolve_state_from_geocode(self, country, state_payload):
        return EnviaGeocodesClient.resolve_odoo_state(self.env, country, state_payload)

    def _ensure_address_geocoded_for_quote(self, side):
        if getattr(self, f"{side}_state_id"):
            return
        if not getattr(self, f"{side}_postal_code") or not getattr(self, f"{side}_country_id"):
            return
        self._apply_geocode(side, force=False)

    def _apply_geocode(self, prefix, force=False):
        country = getattr(self, f"{prefix}_country_id")
        zipcode = getattr(self, f"{prefix}_postal_code")
        if not country or not zipcode:
            return
        zipcode = self._normalize_postal_code(country.code, zipcode.strip())
        if zipcode != getattr(self, f"{prefix}_postal_code"):
            setattr(self, f"{prefix}_postal_code", zipcode)
        if not force and len(zipcode) < 4:
            return
        entries = EnviaGeocodesClient().lookup_zipcode(country.code, zipcode)
        if not entries:
            if force:
                raise UserError(_("No Envia geocode match for postal code %s.") % zipcode)
            return
        entry = entries[0]
        locality = entry.get("locality")
        if locality:
            setattr(self, f"{prefix}_city", locality)
        state = self._resolve_state_from_geocode(country, entry.get("state") or {})
        if state:
            setattr(self, f"{prefix}_state_id", state.id)

    def _geocode_side_values(self, side):
        country = getattr(self, f"{side}_country_id")
        zipcode = getattr(self, f"{side}_postal_code")
        if not country or not zipcode:
            return {}
        zipcode = self._normalize_postal_code(country.code, zipcode.strip())
        if len(zipcode) < 4:
            return {}
        entries = EnviaGeocodesClient().lookup_zipcode(country.code, zipcode)
        if not entries:
            return {}
        entry = entries[0]
        values = {}
        locality = entry.get("locality")
        if locality:
            values[f"{side}_city"] = locality
        state = self._resolve_state_from_geocode(country, entry.get("state") or {})
        if state:
            values[f"{side}_state_id"] = state.id
        return values

    def _branch_side_stale(self, side):
        lines = self._branch_lines(side)
        if not lines:
            return True
        country = getattr(self, f"{side}_country_id")
        zipcode = self._normalize_postal_code(
            country.code if country else None,
            getattr(self, f"{side}_postal_code"),
        )
        return lines[0].zip != zipcode

    @staticmethod
    def _extract_envia_branch_code(entry):
        """Prefer human branch codes (MTY01) over numeric internal ids (468)."""
        address = entry.get("address") if isinstance(entry.get("address"), dict) else {}
        candidates = (
            entry.get("branch_code"),
            entry.get("branchCode"),
            entry.get("code"),
            entry.get("reference"),
            address.get("branch_code"),
            address.get("branchCode"),
            address.get("code"),
            address.get("reference"),
            entry.get("branch_id"),
            entry.get("branchId"),
            entry.get("id"),
        )
        numeric_fallback = ""
        for candidate in candidates:
            if candidate in (None, False, ""):
                continue
            code = str(candidate).strip()
            if not code:
                continue
            if code.isdigit():
                if not numeric_fallback:
                    numeric_fallback = code
                continue
            return code
        return numeric_fallback

    def _load_branches(self, side, carrier_codes=None):
        company = self.env.company
        token = company._envia_get_shipping_api_token()
        if not token:
            raise UserError(_("Configure your Envia shipping API token in Settings first."))
        country = getattr(self, f"{side}_country_id")
        zipcode = getattr(self, f"{side}_postal_code")
        if not country:
            raise UserError(_("Select a country before loading branches."))
        if not zipcode or not zipcode.strip():
            raise UserError(_("Enter a postal code before loading branches."))
        zipcode = self._normalize_postal_code(country.code, zipcode.strip())
        setattr(self, f"{side}_postal_code", zipcode)
        self._apply_geocode(side)
        state = getattr(self, f"{side}_state_id")
        city = getattr(self, f"{side}_city")
        if carrier_codes is None:
            carrier_codes = self._get_branch_carrier_codes(country)
        elif isinstance(carrier_codes, str):
            carrier_codes = [carrier_codes]
        if not carrier_codes:
            raise UserError(_("No active Envia carriers are configured for this country."))
        client = EnviaClient(company._envia_get_base_url(), token)
        envia_state = EnviaOfficialAdapter.envia_state_code(
            country.code,
            state.code if state else None,
        ) or None
        merged = []
        seen = set()
        for carrier_code in carrier_codes:
            try:
                branches = client.get_branches(
                    queries_base_url=company._envia_get_queries_base_url(),
                    carrier=carrier_code,
                    country_code=country.code,
                    zipcode=zipcode,
                    search_type=1 if side == "origin" else 2,
                    city=city.strip() if city else None,
                    state_code=envia_state,
                )
            except UserError:
                continue
            for entry in branches:
                if not isinstance(entry, dict):
                    continue
                external_id = str(
                    entry.get("id")
                    or entry.get("branch_id")
                    or entry.get("branchId")
                    or entry.get("reference")
                    or entry.get("name")
                    or ""
                )
                dedupe_key = (carrier_code, external_id)
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)
                entry["_envia_carrier_code"] = carrier_code
                merged.append(entry)
        if not merged:
            raise UserError(
                _("No pickup points returned near %(zip)s. Try another postal code.")
                % {"zip": zipcode}
            )
        merged.sort(
            key=lambda entry: (
                float(entry.get("distance") or 9999),
                entry.get("_envia_carrier_code") or "",
            )
        )
        selected = self._get_selected_branch(side)[:1]
        selected_key = (
            (selected.branch_code or selected.external_id, selected.carrier)
            if selected
            else False
        )
        self._branch_lines(side).unlink()
        lines = []
        for index, entry in enumerate(merged[:30]):
            carrier_code = entry["_envia_carrier_code"]
            address = entry.get("address")
            address_data = address if isinstance(address, dict) else {}
            external_id = str(
                entry.get("id")
                or entry.get("branch_id")
                or entry.get("branchId")
                or index
            )
            lines.append(
                {
                    "wizard_id": self.id,
                    "side": side,
                    "external_id": external_id,
                    "branch_code": self._extract_envia_branch_code(entry),
                    "number": (
                        address_data.get("number")
                        or entry.get("number")
                        or ""
                    ),
                    "name": (
                        entry.get("reference")
                        or entry.get("name")
                        or entry.get("description")
                        or entry.get("branch_id")
                        or carrier_code
                    ),
                    "street": (
                        address_data.get("address")
                        or address_data.get("street")
                        or entry.get("street")
                        or ""
                    ),
                    "city": (
                        address_data.get("city")
                        or address_data.get("locality")
                        or entry.get("city")
                        or entry.get("locality")
                        or ""
                    ),
                    "zip": self._normalize_postal_code(
                        country.code,
                        address_data.get("postalCode")
                        or address_data.get("zipcode")
                        or entry.get("zipcode")
                        or entry.get("zip_code")
                        or entry.get("postalCode")
                        or "",
                    ),
                    "distance": entry.get("distance"),
                    "state_code": (
                        address_data.get("state")
                        or entry.get("state")
                        or entry.get("state_code")
                        or ""
                    ),
                    "country_code": (
                        address_data.get("country")
                        or entry.get("country_code")
                        or country.code
                    ),
                    "carrier": carrier_code,
                    "phone": entry.get("phone") or "",
                    "email": entry.get("email") or "",
                }
            )
        self.env["envia.quote.wizard.branch"].create(lines)
        if selected_key:
            branch_code, carrier = selected_key
            match = self._find_branch_option(side, branch_code, carrier)
            if match:
                self._branch_lines(side).write({"is_selected": False})
                match.is_selected = True
        elif len(lines) == 1:
            # ponytail: single branch near ZIP — auto-select to avoid a dead-end confirm.
            self._branch_lines(side)[:1].action_select_branch()

    def _get_selected_branch(self, side):
        return self._branch_lines(side).filtered("is_selected")[:1]

    def _pickup_carrier_code(self):
        """Carrier from destination branch or the selected rate (rate-first flow)."""
        self.ensure_one()
        destination = self._get_selected_branch("destination")
        if destination and destination.carrier:
            return destination.carrier
        selected = self.service_line_ids.filtered("is_selected")[:1]
        if selected and selected.carrier:
            return selected.carrier
        return None

    def _pickup_route_branches_ready(self):
        """True when the destination ocurre branch is selected (origin is optional)."""
        self.ensure_one()
        if not self._side_needs_branch_selection("destination"):
            return not self._uses_branch_route() or self.origin_location_type == "branch"
        return bool(self._get_selected_branch("destination"))

    def _restrict_branch_lines_to_carrier(self, carrier):
        """Keep only the selected carrier on the destination ocurre list."""
        self.ensure_one()
        if not carrier:
            return
        self._branch_lines("destination").filtered(
            lambda line, code=carrier: line.carrier and line.carrier != code
        ).unlink()

    def _find_branch_option(self, side, branch_code, carrier=None):
        self.ensure_one()
        if not branch_code:
            return self.env["envia.quote.wizard.branch"]
        lines = self._branch_lines(side).filtered(
            lambda line: line.branch_code == branch_code or line.external_id == branch_code
        )
        if carrier:
            lines = lines.filtered(lambda line: line.carrier == carrier)
        return lines[:1]

    def action_select_branch_option(self, side, branch_code, carrier):
        self.ensure_one()
        branch = self._find_branch_option(side, branch_code, carrier)
        if not branch:
            raise UserError(
                _(
                    "Pickup location list changed. Click Reload pickup locations and select again."
                )
            )
        return branch.action_select_branch()

    def _resolve_state_from_branch(self, country, branch):
        """Resolve state from the branch; never overwrite the user's Near ZIP."""
        if not country:
            return self.env["res.country.state"]
        for code in EnviaOfficialAdapter.odoo_state_codes(country.code, branch.state_code):
            state = self.env["res.country.state"].search(
                [("country_id", "=", country.id), ("code", "=", code)],
                limit=1,
            )
            if state:
                return state
        # Fall back to geocode of the ZIP the user entered (search area), not branch.zip.
        if getattr(self, f"{branch.side}_postal_code"):
            self._apply_geocode(branch.side, force=False)
            return getattr(self, f"{branch.side}_state_id")
        return self.env["res.country.state"]

    def _apply_branch_to_side(self, branch):
        """Mark branch selection; destination ocurre uses branch address for rating."""
        self.ensure_one()
        prefix = branch.side
        country = self.env["res.country"].search(
            [("code", "=", branch.country_code or getattr(self, f"{prefix}_country_id").code)],
            limit=1,
        )
        values = {}
        if country:
            values[f"{prefix}_country_id"] = country.id
        if prefix == "destination":
            zipcode = branch.zip or self.destination_postal_code
            dest_country = country or self.destination_country_id
            if zipcode and dest_country:
                zipcode = self._normalize_postal_code(dest_country.code, zipcode)
            values.update(
                {
                    "destination_street": branch.street or branch.name,
                    "destination_postal_code": zipcode or self.destination_postal_code,
                    "destination_city": branch.city or self.destination_city,
                }
            )
        if values:
            self.with_context(envia_apply_branch=True).write(values)
        state = self._resolve_state_from_branch(country, branch)
        if state:
            self.with_context(envia_apply_branch=True).write({f"{prefix}_state_id": state.id})
        self._align_pickup_carrier(branch)

    def _align_pickup_carrier(self, branch):
        """Destination ocurre fixes the carrier; keep only that carrier's branches."""
        self.ensure_one()
        carrier = branch.carrier
        if not carrier:
            return
        stale_rates = self.service_line_ids.filtered(lambda line: line.carrier != carrier)
        if stale_rates:
            stale_rates.unlink()
        self._restrict_branch_lines_to_carrier(carrier)

    def _side_envia_state(self, side, contact):
        country = getattr(self, f"{side}_country_id")
        state = getattr(self, f"{side}_state_id")
        state_code = state.code if state else (contact.state if contact else "")
        return EnviaOfficialAdapter.envia_state_code(
            country.code if country else None,
            state_code,
        )

    def action_reload_origin_address(self):
        self.ensure_one()
        self._apply_partner_address(self.origin_partner_id, "origin")
        if self._is_ready_for_auto_quote():
            return self.action_get_quote()
        return self._wizard_action()

    def action_reload_destination_address(self):
        self.ensure_one()
        self._apply_partner_address(self.destination_partner_id, "destination")
        if self._is_ready_for_auto_quote():
            return self.action_get_quote()
        return self._wizard_action()

    def action_open_origin_warehouse(self):
        self.ensure_one()
        if not self.origin_warehouse_id:
            raise UserError(_("Select a ship-from warehouse first."))
        return {
            "type": "ir.actions.act_window",
            "name": _("Edit Warehouse"),
            "res_model": "stock.warehouse",
            "view_mode": "form",
            "res_id": self.origin_warehouse_id.id,
            "target": "new",
        }

    def action_open_origin_partner(self):
        self.ensure_one()
        if self.origin_warehouse_id:
            return self.action_open_origin_warehouse()
        if not self.origin_partner_id:
            raise UserError(_("Select a ship-from warehouse first."))
        return self._open_partner_action(self.origin_partner_id)

    def action_open_destination_partner(self):
        self.ensure_one()
        if not self.destination_partner_id:
            raise UserError(_("Select a ship-to contact first."))
        return self._open_partner_action(self.destination_partner_id)

    def action_fill_sandbox_test_route(self):
        self.ensure_one()
        mexico = self.env.ref("base.mx", raise_if_not_found=False)
        if not mexico:
            raise UserError(_("Mexico is not available in this database."))
        origin_state = self.env.ref("base.state_mx_nl", raise_if_not_found=False)
        if not origin_state:
            origin_state = self.env["res.country.state"].search(
                [("country_id", "=", mexico.id), ("code", "in", ["NLE", "NL"])],
                limit=1,
            )
        destination_state = self.env.ref("base.state_mx_df", raise_if_not_found=False)
        if not destination_state:
            destination_state = self.env["res.country.state"].search(
                [("country_id", "=", mexico.id), ("code", "in", ["CMX", "CX", "DIF"])],
                limit=1,
            )
        self.write(
            {
                "origin_postal_code": "67192",
                "origin_city": "Guadalupe",
                "origin_country_id": mexico.id,
                "origin_state_id": origin_state.id if origin_state else False,
                "destination_postal_code": "03100",
                "destination_city": "Ciudad de Mexico",
                "destination_country_id": mexico.id,
                "destination_state_id": destination_state.id if destination_state else False,
            }
        )
        return self._wizard_action()

    @staticmethod
    def _open_partner_action(partner):
        return {
            "type": "ir.actions.act_window",
            "name": _("Edit Contact"),
            "res_model": "res.partner",
            "view_mode": "form",
            "res_id": partner.id,
            "target": "new",
        }

    def action_back_to_address(self):
        self.ensure_one()
        self.step = "address"
        return self._wizard_action()

    def _wizard_target(self):
        self.ensure_one()
        return "new" if self.sale_order_id or self.picking_id else "current"

    def _is_modal_wizard(self):
        self.ensure_one()
        return bool(
            self.sale_order_id
            or self.picking_id
            or self.env.context.get("dialog_size")
        )

    def _wizard_stay_open(self):
        # ponytail: Odoo 19 web maps falsy call_button results to act_window_close.
        return {"type": "ir.actions.client", "tag": "envia_wizard_noop"}

    def _wizard_action(self):
        self.ensure_one()
        if self._is_modal_wizard():
            return self._wizard_stay_open()
        return self._reopen_wizard()

    @api.model
    def _get_wizard_window_action(self, res_id=None):
        wizard = self.browse(res_id) if res_id else self
        view = self.env.ref("envia.view_envia_quote_wizard_form")
        action = {
            "type": "ir.actions.act_window",
            "name": _("Ship with Envia"),
            "res_model": self._name,
            "view_mode": "form",
            "views": [(view.id, "form")],
            "target": wizard._wizard_target() if res_id else "current",
        }
        if res_id:
            action["res_id"] = res_id
            if wizard.sale_order_id or wizard.picking_id:
                action["context"] = {"dialog_size": "extra-large"}
        return action

    @api.model
    def action_open_quote_wizard(self):
        return self._get_wizard_window_action()

    def _reopen_wizard(self):
        self.ensure_one()
        return self._get_wizard_window_action(self.id)

    def action_discard(self):
        self.ensure_one()
        if self.sale_order_id:
            return {"type": "ir.actions.act_window_close"}
        return self.env.ref("envia.action_envia_quote").read()[0]

    def _build_branch_contact(self, branch, country, state):
        company_partner = self.env.company.partner_id
        phone = branch.phone or company_partner.phone or "5555555555"
        email = branch.email or company_partner.email or "shipping@company.com"
        country_code = branch.country_code or (country.code if country else "")
        state_code = branch.state_code or (state.code if state else "")
        return Contact(
            name=branch.name,
            street=branch.street or branch.name,
            number=branch.number or None,
            district=PayloadMapper._resolve_district(state=state) or None,
            city=branch.city or "",
            state=EnviaOfficialAdapter.envia_state_code(country_code, state_code),
            postal_code=branch.zip or "",
            country=country_code,
            phone=phone,
            email=email,
            branch_code=branch._envia_branch_code() or None,
        )

    def _build_contact_for_side(self, side, for_quote=False, branch_override=None):
        prefix = side
        country = getattr(self, f"{prefix}_country_id")
        state = getattr(self, f"{prefix}_state_id")
        branch = branch_override
        if not branch and getattr(self, f"{prefix}_location_type") == "branch":
            branch = self._get_selected_branch(side)
        if branch and (for_quote or branch.is_selected):
            return self._build_branch_contact(branch, country, state)
        partner = getattr(self, f"{prefix}_partner_id")
        street = getattr(self, f"{prefix}_street")
        postal_code = getattr(self, f"{prefix}_postal_code")
        city = getattr(self, f"{prefix}_city")
        contact = self._build_side_contact(
            partner,
            street,
            postal_code,
            city,
            country,
            state,
            street_number=getattr(self, f"{prefix}_street_number") or None,
            district=getattr(self, f"{prefix}_district") or None,
        )
        if side == "origin" and self.origin_warehouse_id:
            address_id = self.origin_warehouse_id._envia_origin_address_id()
            if address_id:
                contact.address_id = address_id
        return contact

    def _build_postal_placeholder_contact(self, side):
        company_partner = self.env.company.partner_id
        country = getattr(self, f"{side}_country_id")
        state = getattr(self, f"{side}_state_id")
        city = getattr(self, f"{side}_city") or "Pickup"
        return Contact(
            name=company_partner.name or "Shipper",
            street=city,
            city=city,
            state=EnviaOfficialAdapter.envia_state_code(
                country.code if country else "",
                state.code if state else "",
            ),
            postal_code=getattr(self, f"{side}_postal_code") or "",
            country=country.code if country else "",
            phone=company_partner.phone or "5555555555",
            email=company_partner.email or "shipping@company.com",
        )

    def _ensure_side_geocoded_for_quote(self, side):
        if getattr(self, f"{side}_location_type") != "branch":
            return
        if getattr(self, f"{side}_state_id"):
            return
        self._apply_geocode(side, force=False)

    def _build_side_contact(
        self,
        partner,
        street,
        postal_code,
        city,
        country,
        state,
        street_number=None,
        district=None,
    ):
        if not partner:
            raise UserError(_("Select a contact for this address."))
        contact = PayloadMapper.partner_to_contact(partner)
        contact.street = street or contact.street
        contact.postal_code = postal_code or contact.postal_code
        contact.city = city or contact.city
        contact.country = country.code
        contact.state = state.code if state else ""
        if street_number:
            contact.number = street_number
        resolved_district = PayloadMapper._resolve_district(
            district=district, state=state, partner=partner
        )
        if resolved_district:
            contact.district = resolved_district
        company_partner = self.env.company.partner_id
        if not contact.phone:
            contact.phone = company_partner.phone or "5555555555"
        if not contact.email:
            contact.email = company_partner.email or "shipping@company.com"
        missing = []
        if not contact.street:
            missing.append(_("street"))
        if not contact.city:
            missing.append(_("city"))
        if not contact.postal_code:
            missing.append(_("postal code"))
        if not contact.phone:
            missing.append(_("phone"))
        if not contact.email:
            missing.append(_("email"))
        if missing:
            raise UserError(
                _("Complete contact %(name)s before quoting: %(fields)s")
                % {"name": partner.name, "fields": ", ".join(missing)}
            )
        return contact

    def _collect_validation_errors(self, require_branches=False):
        self.ensure_one()
        errors = []
        if self.origin_address_warning:
            errors.append(
                _("Ship from — %(message)s", message=self.origin_address_warning)
            )
        if not self.origin_partner_id:
            errors.append(_("Ship from contact is required."))
        if not self.origin_street:
            errors.append(_("Origin street is required."))
        if not self.origin_city:
            errors.append(_("Origin city is required."))
        if self.origin_country_id and self.origin_country_id.state_ids and not self.origin_state_id:
            errors.append(_("Select an origin state/province."))
        if self.destination_address_warning:
            errors.append(
                _("Ship to — %(message)s", message=self.destination_address_warning)
            )
        if not self.destination_partner_id:
            errors.append(_("Ship to contact is required."))
        if not self.destination_street:
            errors.append(_("Destination street is required."))
        if not self.destination_city:
            errors.append(_("Destination city is required."))
        if self.destination_country_id and self.destination_country_id.state_ids and not self.destination_state_id:
            errors.append(_("Select a destination state/province."))
        if not self.origin_postal_code:
            errors.append(_("Origin postal code is required."))
        if not self.destination_postal_code:
            errors.append(_("Destination postal code is required."))
        if not self.origin_country_id:
            errors.append(_("Origin country is required."))
        if not self.destination_country_id:
            errors.append(_("Destination country is required."))
        if self.origin_state_id and self.origin_state_id.country_id != self.origin_country_id:
            errors.append(_("Origin state must belong to the selected origin country."))
        if self.destination_state_id and self.destination_state_id.country_id != self.destination_country_id:
            errors.append(_("Destination state must belong to the selected destination country."))
        if self.weight <= 0:
            errors.append(_("Package weight must be greater than zero."))
        if require_branches:
            for side in self._branch_sides_required_for_confirm():
                if self._get_selected_branch(side):
                    continue
                label = _("Ship from") if side == "origin" else _("Ship to")
                errors.append(
                    _("Select a %(side)s pickup location to continue.")
                    % {"side": label}
                )
        return errors

    def _branch_sides_required_for_confirm(self):
        """Only destination ocurre needs a concrete branch selection."""
        self.ensure_one()
        if self.destination_location_type != "branch":
            return set()
        return {"destination"}

    def _side_needs_branch_selection(self, side):
        """Origin=branch is 'any drop-off'; only destination is selectable."""
        self.ensure_one()
        return side == "destination" and self.destination_location_type == "branch"

    def _apply_package_dimensions_preview(self, adapter, items):
        # ponytail: package-dimensions API is off for now (token/host issues).
        # Re-enable by restoring the fetch below; ceiling: no Envia package preview/hint.
        return

    def _raise_validation_errors(self, errors):
        """Raise UserError from already-translated fragments."""
        if errors:
            # Each entry from _collect_validation_errors is _("...").
            raise UserError("\n".join(errors))

    def _validate_before_quote(self):
        self.ensure_one()
        for side in ("origin", "destination"):
            self._ensure_address_geocoded_for_quote(side)
        self._raise_validation_errors(
            self._collect_validation_errors(require_branches=False)
        )

    def _validate_before_label(self):
        self.ensure_one()
        self._raise_validation_errors(
            self._collect_validation_errors(require_branches=True)
        )

    def _get_quote_carriers(self):
        """Carrier filter for checkout.

        Only a *destination ocurre* selection locks the carrier. A selected rate or a
        leftover branch line while on Ship-Ship must not shrink Get rate results.
        """
        self.ensure_one()
        if self.destination_location_type != "branch":
            return "all"
        destination = self._get_selected_branch("destination")
        if destination and destination.carrier:
            return destination.carrier
        return "all"

    def action_get_quote(self, clear_branch_lines=True):
        self.ensure_one()
        self.rates_feedback = False
        try:
            self._perform_get_quote(clear_branch_lines=clear_branch_lines)
        except UserError as error:
            # Pickup routes: drop carriers with no matching rates instead of a
            # blocking modal, so the user only sees branches that can quote.
            if self._uses_branch_route() and self._is_no_rates_error(error):
                self._handle_no_branch_rates()
                return self._wizard_action()
            raise
        return self._wizard_action()

    @staticmethod
    def _is_no_rates_error(error):
        return "No shipping services available" in str(error)

    def _envia_location_type_labels(self):
        return {
            "address": self.env._("Domicile"),
            "branch": self.env._("Branch"),
        }

    def _route_type_label_for_wizard(self):
        self.ensure_one()
        labels = self._envia_location_type_labels()
        return f"{labels[self.origin_location_type]} - {labels[self.destination_location_type]}"

    def _handle_no_branch_rates(self):
        """Remove branches for the carrier that cannot quote this Pickup route."""
        self.ensure_one()
        carrier = self._pickup_carrier_code()
        self.service_line_ids.unlink()
        carrier_name = (carrier or "").upper() or _("the selected carrier")
        if carrier:
            for side in ("origin", "destination"):
                self._branch_lines(side).filtered(
                    lambda line, code=carrier: line.carrier == code
                ).unlink()
            failed = [
                code.strip()
                for code in (self.failed_branch_carriers or "").split(",")
                if code.strip()
            ]
            if carrier not in failed:
                failed.append(carrier)
            self.failed_branch_carriers = ",".join(failed)
            if self.branch_carriers_probed:
                available = [
                    code.strip()
                    for code in (self.available_branch_carriers or "").split(",")
                    if code.strip() and code.strip() != carrier
                ]
                self.available_branch_carriers = ",".join(available)
        self.rates_feedback = _(
            "No %(route)s rates for %(carrier)s with these branches. "
            "Those locations were removed. Load pickup locations again to choose "
            "another carrier."
        ) % {
            "route": self._route_type_label_for_wizard(),
            "carrier": carrier_name,
        }

    def _perform_get_quote(self, clear_branch_lines=False):
        self.ensure_one()
        self.rates_feedback = False
        self.envia_package_preview = False
        self.envia_package_sync_hint = False
        self._validate_before_quote()
        preserved_service_id = False
        if not clear_branch_lines:
            selected = self.service_line_ids.filtered("is_selected")[:1]
            preserved_service_id = selected.service_id if selected else False
        if clear_branch_lines:
            self._clear_branch_lines()
        company = self.env.company
        origin_contact = self._build_contact_for_side("origin")
        destination_contact = self._build_contact_for_side("destination")
        expected_drop_off = self._expected_route_drop_off()
        mapper = PayloadMapper()
        items = mapper.quote_items_for_context(
            self.sale_order_id,
            self.picking_id,
        )
        request = mapper.build_quote_request_from_values(
            {
                "origin_postal_code": self.origin_postal_code,
                "origin_country": self.origin_country_id.code,
                "origin_state": self._side_envia_state("origin", origin_contact),
                "destination_postal_code": self.destination_postal_code,
                "destination_country": self.destination_country_id.code,
                "destination_state": self._side_envia_state("destination", destination_contact),
                "weight": self.weight,
                "weight_unit": PayloadMapper.envia_weight_unit(self.env),
                "content": self.content,
                "declared_value": self.declared_value,
                "currency": self.currency_id.name,
                "carriers": self._get_quote_carriers(),
                "expected_drop_off": expected_drop_off,
                "origin_contact": origin_contact,
                "destination_contact": destination_contact,
                "items": items,
            }
        )
        adapter = get_envia_adapter(company)
        self._apply_package_dimensions_preview(adapter, items)
        response = adapter.quote(request)
        quote = self.env["envia.quote"].create_from_api_response(
            response,
            {
                "sale_order_id": self.sale_order_id.id,
                "picking_id": self.picking_id.id,
                "origin_partner_id": self.origin_partner_id.id,
                "destination_partner_id": self.destination_partner_id.id,
                "origin_postal_code": self.origin_postal_code,
                "origin_country": self.origin_country_id.code,
                "origin_state": self._side_envia_state("origin", origin_contact),
                "origin_city": self.origin_city,
                "destination_postal_code": self.destination_postal_code,
                "destination_country": self.destination_country_id.code,
                "destination_state": self._side_envia_state("destination", destination_contact),
                "destination_city": self.destination_city,
                "weight": request.weight,
                "content": self.content,
                "declared_value": self.declared_value,
                "currency_id": self.currency_id.id,
                "carriers": self._get_quote_carriers(),
                **self._quote_location_values(),
            },
        )
        self.write({"quote_id": quote.id})
        self.service_line_ids.unlink()
        lines = []
        for service in quote.service_ids:
            lines.append(
                {
                    "wizard_id": self.id,
                    "service_id": service.service_id,
                    "envia_service_id": service.envia_service_id,
                    "carrier": service.carrier,
                    "carrier_name": service.carrier_name,
                    "service_name": service.service_name,
                    "price": service.price,
                    "currency_name": service.currency_name,
                    "estimated_delivery_days": service.estimated_delivery_days,
                    "drop_off": service.drop_off or 0,
                }
            )
        self.env["envia.quote.wizard.service"].create(lines)
        if preserved_service_id:
            selected = self.service_line_ids.filtered(
                lambda line: line.service_id == preserved_service_id
            )[:1]
            if selected:
                self.service_line_ids.write({"is_selected": False})
                selected.is_selected = True

    def _quote_location_values(self):
        self.ensure_one()
        values = {
            "origin_location_type": self.origin_location_type,
            "destination_location_type": self.destination_location_type,
        }
        for side in ("origin", "destination"):
            if not self._side_needs_branch_selection(side):
                continue
            branch = self._get_selected_branch(side)
            if not branch:
                continue
            branch_code = branch._envia_branch_code()
            if not branch_code:
                raise UserError(
                    _("Selected Ship to pickup location is missing branch code from Envia.")
                )
            values.update(
                {
                    f"{side}_branch_code": branch_code,
                    f"{side}_branch_name": branch.name,
                    f"{side}_branch_street": branch.street,
                    f"{side}_branch_number": branch.number,
                }
            )
        return values

    def _uses_branch_route(self):
        self.ensure_one()
        if not self._envia_branches_enabled():
            return False
        return (
            self.origin_location_type == "branch"
            or self.destination_location_type == "branch"
        )

    def _auto_select_branch_service(self):
        self.ensure_one()
        if not self.service_line_ids:
            return
        preferred_service_id = False
        branch_carrier = False
        for side in ("destination", "origin"):
            if getattr(self, f"{side}_location_type") != "branch":
                continue
            branch = self._get_selected_branch(side)
            if not branch:
                continue
            preferred_service_id = branch.service_id or preferred_service_id
            branch_carrier = branch.carrier or branch_carrier
        selected = self.env["envia.quote.wizard.service"]
        if preferred_service_id:
            selected = self.service_line_ids.filtered(
                lambda line: line.service_id == preferred_service_id
            )[:1]
        if not selected and branch_carrier:
            selected = self.service_line_ids.filtered(
                lambda line: line.carrier == branch_carrier
            ).sorted(key=lambda line: line.price or 0.0)[:1]
        if not selected:
            selected = self.service_line_ids.sorted(key=lambda line: line.price or 0.0)[:1]
        self.service_line_ids.write({"is_selected": False})
        if selected:
            selected.write({"is_selected": True})

    def action_select_service_rate(self, service_id=None):
        self.ensure_one()
        service_id = service_id or self.env.context.get("service_id")
        if not service_id:
            return False
        previous = self.service_line_ids.filtered("is_selected")[:1]
        previous_carrier = previous.carrier if previous else False
        self.service_line_ids.write({"is_selected": False})
        selected = self.service_line_ids.filtered(
            lambda line: line.service_id == service_id
        )[:1]
        if not selected:
            return False
        selected.is_selected = True
        self._apply_wizard_rate_to_quote(selected)
        self._load_branches_for_selected_carrier()
        return False

    def _sync_branch_selection_service(self):
        self.ensure_one()
        if not self._uses_branch_route():
            return
        branch = False
        for side in ("destination", "origin"):
            if getattr(self, f"{side}_location_type") != "branch":
                continue
            branch = self._get_selected_branch(side)
            if branch:
                break
        if not branch or not branch.service_id:
            return
        self.service_line_ids.unlink()
        self.env["envia.quote.wizard.service"].create(
            {
                "wizard_id": self.id,
                "service_id": branch.service_id,
                "carrier": branch.carrier,
                "carrier_name": branch.carrier,
                "service_name": branch.service_name or branch.service_id,
                "price": branch.price,
                "currency_name": branch.currency_name,
                "estimated_delivery_days": branch.estimated_delivery_days,
                "is_selected": True,
            }
        )

    def _finalize_quote_selection(self):
        self.ensure_one()
        selected = self.service_line_ids.filtered("is_selected")[:1]
        # ponytail: skip re-quote when a rate is already chosen; sandbox checkout
        # often returns HTTP 200 + meta=error on the second call.
        if not selected:
            self._validate_before_quote()
            self._raise_validation_errors(
                self._collect_validation_errors(
                    require_branches=self.destination_location_type == "branch"
                )
            )
            self._perform_get_quote(clear_branch_lines=False)
            selected = self.service_line_ids.filtered("is_selected")[:1]
        if not selected:
            raise UserError(_("Choose a shipping rate to continue."))
        quote = self.quote_id
        quote.write(self._quote_location_values())
        return self._apply_wizard_rate_to_quote(selected)

    def _quote_service_matching_wizard_line(self, selected):
        self.ensure_one()
        quote = self.quote_id
        if not quote or not selected:
            return self.env["envia.quote.service"]
        match = quote.service_ids.filtered(
            lambda line: line.service_id == selected.service_id
        )[:1]
        if not match and selected.envia_service_id:
            match = quote.service_ids.filtered(
                lambda line: line.envia_service_id == selected.envia_service_id
            )[:1]
        return match

    def _apply_wizard_rate_to_quote(self, selected=None):
        """Write the wizard card onto envia.quote so label/create sees it."""
        self.ensure_one()
        selected = selected or self.service_line_ids.filtered("is_selected")[:1]
        service = self._quote_service_matching_wizard_line(selected)
        if service:
            service.action_select_service()
            service.quote_id._retire_sibling_quotes()
        return service

    def _is_restored_from_quote(self, quote):
        """True when this wizard already mirrors the saved quote (skip re-restore)."""
        self.ensure_one()
        if not quote:
            return False
        if (
            self.origin_location_type != quote.origin_location_type
            or self.destination_location_type != quote.destination_location_type
        ):
            return False
        if quote.selected_service_id and not self.service_line_ids.filtered("is_selected"):
            return False
        if quote.service_ids and not self.service_line_ids:
            return False
        return bool(self.service_line_ids)

    def _restore_from_quote(self, quote):
        """Update shipping cost: seed saved route/branches/rates without Envia API."""
        self.ensure_one()
        if not quote:
            return
        self._seed_scalar_fields_from_quote(quote)
        self._seed_branches_from_quote(quote)
        self._seed_service_lines_from_quote(quote)
        # Quote destination may be stale after Delivery Address changes.
        self._apply_sale_order_destination()

    def _seed_branches_from_quote(self, quote):
        """Rebuild pickup options from the persisted quote (skip branch API)."""
        self.ensure_one()
        carrier = quote.selected_service_id.carrier if quote.selected_service_id else None
        pickup_sides = [
            side
            for side in ("origin", "destination")
            if getattr(quote, f"{side}_location_type") == "branch"
            and getattr(quote, f"{side}_branch_code")
        ]
        if not pickup_sides:
            return
        skip = {
            "envia_skip_auto_quote": True,
            "envia_skip_branch_autoload": True,
            "envia_skip_route_carrier_refresh": True,
            "envia_apply_branch": True,
        }
        self._clear_branch_lines(pickup_sides)
        Branch = self.env["envia.quote.wizard.branch"]
        branches = Branch
        for side in pickup_sides:
            branch_code = getattr(quote, f"{side}_branch_code")
            branches |= Branch.create(
                {
                    "wizard_id": self.id,
                    "side": side,
                    "branch_code": branch_code,
                    "name": getattr(quote, f"{side}_branch_name") or branch_code,
                    "street": getattr(quote, f"{side}_branch_street"),
                    "number": getattr(quote, f"{side}_branch_number"),
                    "zip": getattr(quote, f"{side}_postal_code"),
                    "city": getattr(quote, f"{side}_city"),
                    "country_code": getattr(quote, f"{side}_country"),
                    "state_code": getattr(quote, f"{side}_state"),
                    "carrier": carrier,
                    "is_selected": True,
                }
            )
        for branch in branches:
            self.with_context(**skip)._apply_branch_to_side(branch)
        if carrier and self._uses_branch_route():
            self.with_context(**skip).write(
                {
                    "branch_carriers_probed": True,
                    "available_branch_carriers": carrier,
                }
            )

    def _seed_service_lines_from_quote(self, quote):
        """Rebuild rate cards from the persisted quote (skip rate API)."""
        self.ensure_one()
        services = quote.service_ids
        if not services and quote.selected_service_id:
            services = quote.selected_service_id
        if not services:
            return
        skip = {"envia_skip_auto_quote": True}
        self.with_context(**skip).write({"quote_id": quote.id})
        self.service_line_ids.unlink()
        selected_id = quote.selected_service_id.id if quote.selected_service_id else False
        self.env["envia.quote.wizard.service"].create(
            [
                {
                    "wizard_id": self.id,
                    "service_id": service.service_id,
                    "envia_service_id": service.envia_service_id,
                    "carrier": service.carrier,
                    "carrier_name": service.carrier_name,
                    "service_name": service.service_name,
                    "price": service.price,
                    "currency_name": service.currency_name or service.currency_id.name,
                    "estimated_delivery_days": service.estimated_delivery_days,
                    "drop_off": service.drop_off or 0,
                    "is_selected": service.id == selected_id,
                }
                for service in services
            ]
        )

    def _seed_scalar_fields_from_quote(self, quote):
        self.ensure_one()
        Country = self.env["res.country"]
        # Location types must always be written (including "address") so Update
        # does not keep the wizard defaults when the saved route is Ship/Pickup.
        values = {
            "origin_location_type": quote.origin_location_type or "address",
            "destination_location_type": quote.destination_location_type or "address",
            "origin_partner_id": quote.origin_partner_id.id,
            "destination_partner_id": quote.destination_partner_id.id,
            "origin_postal_code": quote.origin_postal_code,
            "destination_postal_code": quote.destination_postal_code,
            "origin_city": quote.origin_city,
            "destination_city": quote.destination_city,
            "weight": quote.weight,
            "content": quote.content,
            "declared_value": quote.declared_value,
        }
        origin_country = Country.search([("code", "=", quote.origin_country)], limit=1)
        destination_country = Country.search([("code", "=", quote.destination_country)], limit=1)
        if origin_country:
            values["origin_country_id"] = origin_country.id
        if destination_country:
            values["destination_country_id"] = destination_country.id
        if quote.currency_id:
            values["currency_id"] = quote.currency_id.id
        State = self.env["res.country.state"]
        for side in ("origin", "destination"):
            state_code = getattr(quote, f"{side}_state")
            country = origin_country if side == "origin" else destination_country
            if not state_code or not country:
                continue
            for code in EnviaOfficialAdapter.odoo_state_codes(country.code, state_code):
                state = State.search(
                    [("country_id", "=", country.id), ("code", "=", code)],
                    limit=1,
                )
                if state:
                    values[f"{side}_state_id"] = state.id
                    break
        self.with_context(
            envia_skip_auto_quote=True,
            envia_skip_branch_autoload=True,
            envia_skip_route_carrier_refresh=True,
        ).write({k: v for k, v in values.items() if v not in (None, False, "")})
        for side in ("origin", "destination"):
            if not getattr(self, f"{side}_state_id") and getattr(self, f"{side}_postal_code"):
                self._apply_geocode(side, force=False)

    def _reselect_service_line(self, service):
        self.ensure_one()
        line = self.service_line_ids.filtered(
            lambda rate: rate.service_id == service.service_id
            or (service.envia_service_id and rate.envia_service_id == service.envia_service_id)
        )[:1]
        if line:
            self.service_line_ids.write({"is_selected": False})
            line.is_selected = True

    def action_apply_shipping_to_order(self):
        self.ensure_one()
        if not self.sale_order_id:
            raise UserError(_("Open this wizard from a sale order to apply shipping cost."))
        if self.sale_order_id.state == "cancel":
            raise UserError(_("Shipping cost cannot be applied on a cancelled sale order."))
        self._finalize_quote_selection()
        self._apply_shipping_cost_to_order()
        return {"type": "ir.actions.act_window_close"}

    def _apply_shipping_cost_to_order(self):
        self.ensure_one()
        if not self.sale_order_id or self.sale_order_id.state == "cancel":
            return
        self.sale_order_id._sync_envia_shipping_line(self.quote_id)

    def action_confirm_selection(self):
        """Persist selected rate, apply shipping cost, then create the label."""
        self.ensure_one()
        self._finalize_quote_selection()
        if self.quote_id:
            self.quote_id._retire_sibling_quotes()
        self._apply_shipping_cost_to_order()
        picking = self.picking_id
        if not picking and self.sale_order_id:
            picking = self.sale_order_id.picking_ids.filtered(
                lambda item: item.picking_type_code == "outgoing"
                and item.state != "cancel"
            ).sorted("id", reverse=True)[:1]
        generate_ctx = {}
        if self.quote_id:
            generate_ctx["envia_force_quote_id"] = self.quote_id.id
        if picking and picking.carrier_id.delivery_type == "envia":
            return picking.with_context(**generate_ctx).action_envia_generate_label()
        raise UserError(_("Open the delivery and click Generate Envia Label."))
