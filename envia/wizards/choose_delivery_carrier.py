from odoo import _, api, fields, models
from odoo.exceptions import UserError

from ..services.payload_mapper import PayloadMapper


class ChooseDeliveryCarrier(models.TransientModel):
    _inherit = "choose.delivery.carrier"

    # ponytail: web save must not sync related x2many back to the quote wizard.
    _ENVIA_SAVE_STRIP_FIELDS = frozenset(
        {
            "envia_service_line_ids",
            "envia_origin_branch_line_ids",
            "envia_destination_branch_line_ids",
        }
    )

    envia_wizard_id = fields.Many2one("envia.quote.wizard", ondelete="cascade")
    envia_is_sandbox = fields.Boolean(related="envia_wizard_id.is_sandbox")
    envia_can_get_rates = fields.Boolean(related="envia_wizard_id.can_get_rates")
    envia_show_service_rates = fields.Boolean(related="envia_wizard_id.show_service_rates")
    envia_blocking_message = fields.Char(related="envia_wizard_id.blocking_message")
    envia_cheapest_rate_label = fields.Char(related="envia_wizard_id.cheapest_rate_label")
    envia_selected_service_label = fields.Char(related="envia_wizard_id.selected_service_label")
    envia_allowed_origin_warehouse_ids = fields.Many2many(
        related="envia_wizard_id.allowed_origin_warehouse_ids",
    )
    envia_origin_warehouse_id = fields.Many2one(
        related="envia_wizard_id.origin_warehouse_id",
        readonly=False,
    )
    envia_origin_readonly = fields.Boolean(related="envia_wizard_id.origin_readonly")
    envia_destination_partner_readonly = fields.Boolean(
        related="envia_wizard_id.destination_partner_readonly",
    )
    envia_origin_linked_contact_display = fields.Char(
        related="envia_wizard_id.origin_linked_contact_display",
    )
    envia_origin_partner_id = fields.Many2one(
        related="envia_wizard_id.origin_partner_id",
        readonly=False,
    )
    envia_allowed_destination_partner_ids = fields.Many2many(
        related="envia_wizard_id.allowed_destination_partner_ids",
    )
    envia_origin_location_type = fields.Selection(
        related="envia_wizard_id.origin_location_type",
        readonly=False,
    )
    envia_destination_location_type = fields.Selection(
        related="envia_wizard_id.destination_location_type",
        readonly=False,
    )
    envia_destination_partner_id = fields.Many2one(
        related="envia_wizard_id.destination_partner_id",
        readonly=False,
    )
    envia_origin_street = fields.Char(related="envia_wizard_id.origin_street", readonly=False)
    envia_origin_street_number = fields.Char(
        related="envia_wizard_id.origin_street_number",
        readonly=False,
    )
    envia_origin_district = fields.Char(
        related="envia_wizard_id.origin_district",
        readonly=False,
    )
    envia_origin_postal_code = fields.Char(
        related="envia_wizard_id.origin_postal_code",
        readonly=False,
    )
    envia_origin_city = fields.Char(related="envia_wizard_id.origin_city", readonly=False)
    envia_origin_country_id = fields.Many2one(
        related="envia_wizard_id.origin_country_id",
        readonly=False,
    )
    envia_origin_state_id = fields.Many2one(
        related="envia_wizard_id.origin_state_id",
        readonly=False,
    )
    envia_destination_street = fields.Char(
        related="envia_wizard_id.destination_street",
        readonly=False,
    )
    envia_destination_street_number = fields.Char(
        related="envia_wizard_id.destination_street_number",
        readonly=False,
    )
    envia_destination_district = fields.Char(
        related="envia_wizard_id.destination_district",
        readonly=False,
    )
    envia_destination_postal_code = fields.Char(
        related="envia_wizard_id.destination_postal_code",
        readonly=False,
    )
    envia_destination_city = fields.Char(
        related="envia_wizard_id.destination_city",
        readonly=False,
    )
    envia_destination_country_id = fields.Many2one(
        related="envia_wizard_id.destination_country_id",
        readonly=False,
    )
    envia_destination_state_id = fields.Many2one(
        related="envia_wizard_id.destination_state_id",
        readonly=False,
    )
    envia_show_origin_branch_picker = fields.Boolean(
        related="envia_wizard_id.show_origin_branch_picker",
    )
    envia_show_destination_branch_picker = fields.Boolean(
        related="envia_wizard_id.show_destination_branch_picker",
    )
    envia_origin_branch_count = fields.Integer(related="envia_wizard_id.origin_branch_count")
    envia_destination_branch_count = fields.Integer(
        related="envia_wizard_id.destination_branch_count",
    )
    envia_origin_branch_load_error = fields.Char(
        related="envia_wizard_id.origin_branch_load_error",
    )
    envia_destination_branch_load_error = fields.Char(
        related="envia_wizard_id.destination_branch_load_error",
    )
    envia_origin_branch_line_ids = fields.One2many(
        related="envia_wizard_id.origin_branch_line_ids",
    )
    envia_destination_branch_line_ids = fields.One2many(
        related="envia_wizard_id.destination_branch_line_ids",
    )
    envia_origin_selected_branch_label = fields.Char(
        related="envia_wizard_id.origin_selected_branch_label",
    )
    envia_destination_selected_branch_label = fields.Char(
        related="envia_wizard_id.destination_selected_branch_label",
    )
    envia_service_line_ids = fields.One2many(related="envia_wizard_id.service_line_ids")
    envia_rates_feedback = fields.Char(related="envia_wizard_id.rates_feedback")
    envia_weight_warning = fields.Char(related="envia_wizard_id.weight_warning")
    envia_origin_sync_warning = fields.Char(
        related="envia_wizard_id.origin_envia_sync_warning"
    )
    envia_package_preview = fields.Text(related="envia_wizard_id.envia_package_preview")
    envia_package_sync_hint = fields.Char(related="envia_wizard_id.envia_package_sync_hint")
    envia_enable_labels = fields.Boolean(related="order_id.company_id.envia_enable_labels")
    envia_show_quote_archive = fields.Boolean(
        related="order_id.company_id.envia_show_quote_archive"
    )
    envia_enable_branches = fields.Boolean(compute="_compute_envia_company_options")
    envia_has_selected_rate = fields.Boolean(default=False)

    @api.depends("order_id", "order_id.company_id.envia_enable_branches")
    def _compute_envia_company_options(self):
        for wizard in self:
            order = wizard._resolve_order()
            company = order.company_id if order else self.env.company
            wizard.envia_enable_branches = company.envia_enable_branches

    def _should_default_envia_carrier(self, order):
        envia_carrier = order._get_envia_delivery_carrier()
        if not envia_carrier:
            return False
        if order.company_id.envia_default_carrier:
            return True
        return bool(order._get_restorable_envia_quote())

    def _apply_envia_carrier_default(self):
        for wizard in self:
            order = wizard._resolve_order()
            if not order or wizard.carrier_id:
                continue
            if wizard._should_default_envia_carrier(order):
                wizard.carrier_id = order._get_envia_delivery_carrier()

    def _resolve_order(self):
        self.ensure_one()
        if self.order_id:
            return self.order_id
        order_id = self.env.context.get("default_order_id")
        return self.env["sale.order"].browse(order_id) if order_id else self.env["sale.order"]

    def _envia_package_weight(self):
        self.ensure_one()
        order = self.order_id
        if order:
            return PayloadMapper.sale_order_package_weight(order)
        if self.total_weight:
            return PayloadMapper.normalize_package_weight(self.total_weight)
        return PayloadMapper.DEFAULT_PACKAGE_WEIGHT

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        order_id = res.get("order_id") or self.env.context.get("default_order_id")
        if not order_id:
            return res
        order = self.env["sale.order"].browse(order_id)
        envia_carrier = order._get_envia_delivery_carrier()
        if envia_carrier and not res.get("carrier_id"):
            quote = order._get_restorable_envia_quote()
            if (
                order.company_id.envia_default_carrier
                or quote
                or self.env.context.get("envia_force_delivery_carrier")
            ):
                res["carrier_id"] = envia_carrier.id
        quote = order._get_restorable_envia_quote()
        if quote and not res.get("total_weight") and quote.weight:
            res["total_weight"] = quote.weight
        return res

    @api.depends("partner_id", "order_id")
    def _compute_available_carrier(self):
        super()._compute_available_carrier()
        envia_carrier = self.env.ref("envia.delivery_carrier_envia", raise_if_not_found=False)
        if not envia_carrier:
            return
        ctx_carrier_id = self.env.context.get("default_carrier_id")
        for wizard in self:
            order = wizard._resolve_order()
            quote = order._get_restorable_envia_quote() if order else self.env["envia.quote"]
            use_envia = bool(quote) or wizard.carrier_id == envia_carrier
            if not use_envia and ctx_carrier_id:
                use_envia = ctx_carrier_id == envia_carrier.id
            if not use_envia:
                continue
            if envia_carrier not in wizard.available_carrier_ids:
                wizard.available_carrier_ids = wizard.available_carrier_ids | envia_carrier

    @staticmethod
    def _strip_envia_related_x2many(vals):
        for field in ChooseDeliveryCarrier._ENVIA_SAVE_STRIP_FIELDS:
            vals.pop(field, None)

    @api.model_create_multi
    def create(self, vals_list):
        # order_id is invisible; after onchange/restore the web client may omit it
        # on save. Recover from context or the linked envia quote wizard.
        default_order = self.env.context.get("default_order_id")
        default_carrier = self.env.context.get("default_carrier_id")
        for vals in vals_list:
            self._strip_envia_related_x2many(vals)
            if not vals.get("order_id"):
                if default_order:
                    vals["order_id"] = default_order
                elif vals.get("envia_wizard_id"):
                    quote_wizard = self.env["envia.quote.wizard"].browse(
                        vals["envia_wizard_id"]
                    )
                    if quote_wizard.sale_order_id:
                        vals["order_id"] = quote_wizard.sale_order_id.id
            if not vals.get("carrier_id") and default_carrier:
                vals["carrier_id"] = default_carrier
            if not vals.get("carrier_id") and vals.get("order_id"):
                order = self.env["sale.order"].browse(vals["order_id"])
                envia_carrier = order._get_envia_delivery_carrier()
                quote = order._get_restorable_envia_quote()
                if envia_carrier and (
                    quote
                    or order.company_id.envia_default_carrier
                    or self.env.context.get("envia_force_delivery_carrier")
                ):
                    vals["carrier_id"] = envia_carrier.id
            if not vals.get("total_weight") and vals.get("order_id"):
                order = self.env["sale.order"].browse(vals["order_id"])
                quote = order._get_restorable_envia_quote()
                if quote and quote.weight:
                    vals["total_weight"] = quote.weight
        wizards = super().create(vals_list)
        for wizard in wizards:
            if wizard.delivery_type != "envia":
                continue
            wizard._ensure_envia_wizard()
            wizard._restore_envia_wizard_from_order()
            quote_wizard = wizard.envia_wizard_id
            if not quote_wizard.is_seeded_from_order or not quote_wizard.service_line_ids:
                wizard._prepare_envia_quote_wizard()
        return wizards

    def write(self, vals):
        vals = dict(vals)
        self._strip_envia_related_x2many(vals)
        return super().write(vals)

    @api.onchange("carrier_id", "total_weight")
    def _onchange_carrier_id(self):
        res = super()._onchange_carrier_id()
        if self.delivery_type == "envia" and self.carrier_id:
            # Modal paints from onchange; restore here so Update opens on Pickup
            # with branches already loaded (create alone is too late for the UI).
            self._ensure_envia_wizard()
            self._restore_envia_wizard_from_order()
            self._prepare_envia_quote_wizard()
        return res

    @api.onchange("envia_origin_location_type", "envia_destination_location_type")
    def _onchange_envia_location_types(self):
        if self.env.context.get("carrier_recompute"):
            return
        wizard = self.envia_wizard_id
        if not wizard or not self.envia_service_line_ids:
            return
        if all(
            wizard._rate_drop_off_matches_route(line.drop_off)
            for line in self.envia_service_line_ids
        ):
            return
        self.envia_service_line_ids = [(5, 0, 0)]
        if wizard.id:
            wizard.with_context(envia_skip_auto_quote=True).write({"quote_id": False})
            wizard.service_line_ids.unlink()
        self.delivery_price = 0
        self.display_price = 0
        self.delivery_message = False
        self.envia_has_selected_rate = False

    @api.onchange("order_id")
    def _onchange_order_id(self):
        self._apply_envia_carrier_default()
        if self.delivery_type == "envia":
            self._ensure_envia_wizard()
            self._restore_envia_wizard_from_order()
            self._prepare_envia_quote_wizard()
            return
        res = super()._onchange_order_id()
        if self.delivery_type == "envia":
            self._ensure_envia_wizard()
            self._restore_envia_wizard_from_order()
            self._prepare_envia_quote_wizard()
        return res

    def _prepare_envia_quote_wizard(self):
        for carrier_wizard in self:
            quote_wizard = carrier_wizard.envia_wizard_id
            if not quote_wizard:
                continue
            quote_wizard._apply_sale_order_destination()
            quote_wizard._sync_partner_address_fields()
            for side in ("origin", "destination"):
                if getattr(quote_wizard, f"{side}_state_id"):
                    continue
                quote_wizard._apply_geocode(side, force=False)
            carrier_wizard._sync_envia_wizard_package()

    def _sync_envia_wizard_package(self):
        for carrier_wizard in self:
            quote_wizard = carrier_wizard.envia_wizard_id
            if not quote_wizard:
                continue
            values = {
                "weight": carrier_wizard._envia_package_weight(),
            }
            if any(quote_wizard[field] != values[field] for field in values):
                quote_wizard.with_context(envia_skip_auto_quote=True).write(values)

    def _ensure_envia_wizard(self):
        QuoteWizard = self.env["envia.quote.wizard"]
        for wizard in self:
            if wizard.delivery_type != "envia":
                continue
            order = wizard.order_id
            shipping = order.partner_shipping_id or order.partner_id if order.ids else False
            if wizard.envia_wizard_id:
                wizard.envia_wizard_id._apply_sale_order_destination()
                continue
            wizard.envia_wizard_id = QuoteWizard.create(
                {
                    "sale_order_id": order.id if order.ids else False,
                    "destination_partner_id": shipping.id if shipping else False,
                    "weight": wizard._envia_package_weight(),
                }
            )

    def _restore_envia_wizard_from_order(self):
        """Seed the modal from the order's last applied Envia quote (Update flow)."""
        for wizard in self:
            if wizard.delivery_type != "envia" or not wizard.envia_wizard_id:
                continue
            quote_wizard = wizard.envia_wizard_id
            # Already seeded on open: keep the user's later Ship/Pickup edits.
            if quote_wizard.is_seeded_from_order:
                continue
            order = wizard.order_id
            if not order.ids:
                continue
            # Address/weight changed: keep route prefs, drop stale rates → Get rate.
            if order.recompute_delivery_price:
                wizard._envia_seed_stale_route_without_rates(quote_wizard, order)
                continue
            saved_quote = order._get_restorable_envia_quote()
            if not saved_quote:
                continue
            if quote_wizard._is_restored_from_quote(saved_quote):
                quote_wizard.is_seeded_from_order = True
                continue
            quote_wizard._restore_from_quote(saved_quote)
            quote_wizard.is_seeded_from_order = True
            wizard._sync_envia_wizard_package()
            wizard._sync_delivery_price_from_envia()
            # Re-bind the Many2one so onchange/NewId keeps the restored wizard
            # (invalidate would drop the in-memory link on unsaved records).
            wizard.envia_wizard_id = quote_wizard

    def _envia_seed_stale_route_without_rates(self, quote_wizard, order):
        """Restore Ship/Pickup prefs but force a fresh Get rate for the new destination."""
        self.ensure_one()
        saved_quote = order._get_restorable_envia_quote()
        if saved_quote and not quote_wizard._is_restored_from_quote(saved_quote):
            quote_wizard._seed_scalar_fields_from_quote(saved_quote)
        quote_wizard._apply_sale_order_destination()
        skip = {
            "envia_skip_auto_quote": True,
            "envia_skip_branch_autoload": True,
            "envia_skip_address_sync": True,
        }
        quote_wizard.with_context(**skip)._clear_quote_results()
        if quote_wizard.origin_branch_line_ids or quote_wizard.destination_branch_line_ids:
            quote_wizard._clear_branch_lines()
        quote_wizard.is_seeded_from_order = True
        self.write(
            {
                "delivery_price": 0.0,
                "display_price": 0.0,
                "envia_has_selected_rate": False,
                "delivery_message": False,
            }
        )
        self.envia_wizard_id = quote_wizard

    def _envia_reopen_action(self):
        self.ensure_one()
        title = _("Update shipping cost") if self.env.context.get("carrier_recompute") else _(
            "Add a shipping method"
        )
        context = dict(self.env.context)
        if self.order_id:
            context["default_order_id"] = self.order_id.id
        if self.carrier_id:
            context["default_carrier_id"] = self.carrier_id.id
        return {
            "name": title,
            "type": "ir.actions.act_window",
            "view_mode": "form",
            "views": [(False, "form")],
            "res_model": "choose.delivery.carrier",
            "res_id": self.id,
            "target": "new",
            "context": context,
        }

    def _envia_delivery_price(self, service_line):
        self.ensure_one()
        price = service_line.price
        rate_currency = self.env["res.currency"].search(
            [("name", "=", service_line.currency_name)],
            limit=1,
        )
        if rate_currency and rate_currency != self.order_id.currency_id:
            price = rate_currency._convert(
                price,
                self.order_id.currency_id,
                self.order_id.company_id,
                self.order_id.date_order or fields.Date.context_today(self),
            )
        company = self.company_id or self.order_id.company_id
        price = self.carrier_id.product_id._get_tax_included_unit_price(
            company,
            company.currency_id,
            self.order_id.date_order,
            "sale",
            fiscal_position=self.order_id.fiscal_position_id,
            product_price_unit=price,
            product_currency=self.order_id.currency_id,
        )
        return self.carrier_id._apply_margins(price, self.order_id)

    def _sync_delivery_price_from_envia(self):
        self.ensure_one()
        selected = self.envia_wizard_id.service_line_ids.filtered("is_selected")[:1]
        if not selected:
            self.envia_has_selected_rate = False
            return
        price = self._envia_delivery_price(selected)
        self.write(
            {
                "delivery_price": price,
                "display_price": price,
                "envia_has_selected_rate": True,
                "delivery_message": _(
                    "%(carrier)s - %(service)s",
                    carrier=selected.carrier_name or selected.carrier,
                    service=selected.service_name,
                ),
            }
        )

    def update_price(self):
        self.ensure_one()
        if self.delivery_type != "envia":
            return super().update_price()
        self._ensure_envia_wizard()
        self._prepare_envia_quote_wizard()
        quote_wizard = self.envia_wizard_id
        # Update shipping: requote every carrier unless destination ocurre is active.
        # Clear the prior selection so Get rate does not look like "refresh current method".
        if quote_wizard.destination_location_type != "branch" and quote_wizard.service_line_ids:
            quote_wizard.service_line_ids.write({"is_selected": False})
        # Keep branches when ocurre is in use; branch selection still locks carrier.
        quote_wizard.action_get_quote(clear_branch_lines=False)
        self._sync_delivery_price_from_envia()
        # Stay on the same dialog (False would close it in Odoo 19). JS reloads + scrolls.
        return {"type": "ir.actions.client", "tag": "envia_wizard_noop"}

    def action_envia_select_service(self, service_id=None):
        self.ensure_one()
        service_id = service_id or self.env.context.get("service_id")
        if not service_id or not self.envia_wizard_id:
            return False
        quote_wizard = self.envia_wizard_id
        previous = quote_wizard.service_line_ids.filtered("is_selected")[:1]
        previous_carrier = previous.carrier if previous else False
        quote_wizard.action_select_service_rate(service_id=service_id)
        self._sync_delivery_price_from_envia()
        selected = quote_wizard.service_line_ids.filtered("is_selected")[:1]
        # Reopen only when the pickup carrier changes (branch list must refresh).
        if (
            quote_wizard._uses_branch_route()
            and selected
            and previous_carrier
            and previous_carrier != selected.carrier
        ):
            return self._envia_reopen_action()
        return False

    def action_envia_sync_price(self):
        self.ensure_one()
        self._sync_delivery_price_from_envia()
        return False

    def action_envia_select_branch(self, side=None, branch_code=None, carrier=None):
        self.ensure_one()
        wizard = self.envia_wizard_id
        if not wizard:
            return False
        result = wizard.action_select_branch_option(
            side=side or self.env.context.get("side"),
            branch_code=branch_code or self.env.context.get("branch_code"),
            carrier=carrier or self.env.context.get("carrier"),
        )
        self._sync_delivery_price_from_envia()
        if result and result.get("type") == "ir.actions.act_window":
            return result
        return self._envia_reopen_action() if wizard._pickup_route_branches_ready() else False

    def action_envia_open_origin_warehouse(self):
        self.ensure_one()
        self._ensure_envia_wizard()
        return self.envia_wizard_id.action_open_origin_warehouse()

    def action_envia_open_origin_partner(self):
        self.ensure_one()
        self._ensure_envia_wizard()
        return self.envia_wizard_id.action_open_origin_partner()

    def action_envia_open_destination_partner(self):
        self.ensure_one()
        self._ensure_envia_wizard()
        return self.envia_wizard_id.action_open_destination_partner()

    def action_envia_fill_sandbox_test_route(self):
        self.ensure_one()
        self._ensure_envia_wizard()
        self.envia_wizard_id.action_fill_sandbox_test_route()
        return self._envia_reopen_action()

    def action_envia_load_origin_branches(self):
        self.ensure_one()
        self._ensure_envia_wizard()
        self.envia_wizard_id.action_load_origin_branches()
        return self._envia_reopen_action()

    def action_envia_load_destination_branches(self):
        self.ensure_one()
        self._ensure_envia_wizard()
        self.envia_wizard_id.action_load_destination_branches()
        return self._envia_reopen_action()

    def button_confirm(self):
        self.ensure_one()
        if self.delivery_type == "envia" and self.envia_wizard_id:
            selected = self.envia_wizard_id.service_line_ids.filtered("is_selected")[:1]
            if not selected:
                raise UserError(_("Choose a shipping rate to continue."))
            self.envia_wizard_id._finalize_quote_selection()
            self._sync_delivery_price_from_envia()
        result = super().button_confirm()
        if self.delivery_type == "envia" and self.carrier_id and not self.order_id.carrier_id:
            self.order_id.carrier_id = self.carrier_id
        return result
