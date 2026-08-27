from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools import float_compare

from ..services.website_pickup import WebsitePickupService


class SaleOrder(models.Model):
    _name = "sale.order"
    _inherit = ["sale.order", "envia.read.grouping.mixin"]

    envia_quote_ids = fields.One2many("envia.quote", "sale_order_id", string="Envia Quotes")
    envia_shipment_ids = fields.One2many("envia.shipment", "sale_order_id", string="Envia Shipments")
    envia_quote_count = fields.Integer(compute="_compute_envia_counts")
    envia_shipment_count = fields.Integer(compute="_compute_envia_counts")
    envia_enable_labels = fields.Boolean(related="company_id.envia_enable_labels")
    envia_show_quote_archive = fields.Boolean(related="company_id.envia_show_quote_archive")
    envia_can_create_shipment = fields.Boolean(compute="_compute_envia_can_create_shipment")
    envia_can_reship = fields.Boolean(compute="_compute_envia_can_reship")
    envia_status = fields.Selection(
        [
            ("none", "No Envia activity"),
            ("quoted", "Rate selected"),
            ("shipped", "Label created"),
        ],
        compute="_compute_envia_status",
        string="Envia Status",
    )
    envia_summary = fields.Char(compute="_compute_envia_status")
    envia_service_id = fields.Integer(string="Envia Service ID", copy=False)
    envia_external_order_id = fields.Char(
        string="Envia Order ID",
        copy=False,
        help="Envia ecommerce orderId from label/create; used to unlink fulfillments.",
    )

    def write(self, vals):
        to_recompute = self.env["sale.order"]
        if "partner_shipping_id" in vals:
            new_shipping_id = vals["partner_shipping_id"] or False
            to_recompute = self.filtered(
                lambda order: order.partner_shipping_id.id != new_shipping_id
                and order._envia_has_shipping_method()
            )
        result = super().write(vals)
        # Match delivery.onchange_order_line: keep the line, flag cost as stale.
        if to_recompute:
            to_recompute.filtered(lambda order: not order.recompute_delivery_price).write(
                {"recompute_delivery_price": True}
            )
        return result

    def _envia_has_shipping_method(self):
        self.ensure_one()
        if self.carrier_id.delivery_type == "envia":
            return True
        product = self.env.ref("envia.product_envia_shipping", raise_if_not_found=False)
        return bool(
            product
            and self.order_line.filtered(
                lambda line: line.is_delivery and line.product_id == product
            )
        )

    def _compute_envia_counts(self):
        for order in self:
            order.envia_quote_count = len(order.envia_quote_ids)
            order.envia_shipment_count = len(order.envia_shipment_ids)

    def _compute_envia_can_create_shipment(self):
        for order in self:
            has_quote = bool(order._get_active_envia_quote())
            order.envia_can_create_shipment = has_quote

    def _envia_pending_outgoing_pickings(self):
        self.ensure_one()
        return self.picking_ids.filtered(
            lambda picking: picking.picking_type_code == "outgoing"
            and picking.state not in ("done", "cancel")
        )

    def _envia_lines_needing_procurement(self):
        """Goods lines where ordered qty exceeds procured qty (e.g. after return)."""
        self.ensure_one()
        precision = self.env["decimal.precision"].precision_get("Product Unit")
        lines = self.order_line.filtered(
            lambda line: not line.display_type
            and not line.is_delivery
            and line.product_id.type == "consu"
        )
        return lines.filtered(
            lambda line: float_compare(
                line.product_uom_qty,
                line._get_qty_procurement(),
                precision_digits=precision,
            )
            > 0
        )

    @api.depends(
        "state",
        "picking_ids",
        "picking_ids.state",
        "picking_ids.picking_type_id.code",
        "order_line.product_uom_qty",
        "order_line.is_delivery",
        "order_line.move_ids",
        "order_line.move_ids.state",
        "order_line.move_ids.quantity",
        "order_line.move_ids.product_uom_qty",
    )
    def _compute_envia_can_reship(self):
        for order in self:
            order.envia_can_reship = (
                order.state == "sale"
                and not order._envia_pending_outgoing_pickings()
                and bool(order._envia_lines_needing_procurement())
            )

    def action_envia_reship(self):
        """Create a new linked outgoing delivery after a validated return.

        Uses Core ``_action_launch_stock_rule`` so moves keep ``sale_line_id``.
        Requires the return to update SO quantities (``to_refund``).
        """
        self.ensure_one()
        if self.state != "sale":
            raise UserError(_("Only confirmed sales orders can be reshipped."))
        pending = self._envia_pending_outgoing_pickings()
        if pending:
            return self._get_action_view_picking(pending)
        lines = self._envia_lines_needing_procurement()
        if not lines:
            raise UserError(
                _(
                    "Nothing to reship. Validate a customer return with "
                    "'Update quantities on SO/PO' enabled, without changing "
                    "the ordered quantity."
                )
            )
        before = self.picking_ids
        was_locked = self.locked
        if was_locked:
            self.action_unlock()
        try:
            lines._action_launch_stock_rule()
        finally:
            if was_locked and not self.locked:
                self.action_lock()
        new_outs = (self.picking_ids - before).filtered(
            lambda picking: picking.picking_type_code == "outgoing"
            and picking.state not in ("done", "cancel")
        ) or self._envia_pending_outgoing_pickings()
        if not new_outs:
            raise UserError(_("Could not create a new outgoing delivery."))
        vals = {}
        if self.carrier_id:
            vals["carrier_id"] = self.carrier_id.id
        if self.envia_service_id:
            vals["envia_service_id"] = self.envia_service_id
        if vals:
            new_outs.write(vals)
        return self._get_action_view_picking(new_outs)

    def _compute_envia_status(self):
        for order in self:
            shipment = order.envia_shipment_ids.filtered(lambda item: item._is_active())[:1]
            quote = order._get_active_envia_quote()
            if shipment:
                order.envia_status = "shipped"
                order.envia_summary = _(
                    "Envia label created: %(tracking)s · %(carrier)s",
                    tracking=shipment.tracking_number or shipment.name,
                    carrier=shipment.carrier_name or shipment.carrier or _("Carrier"),
                )
            elif quote:
                service = quote.selected_service_id
                order.envia_status = "quoted"
                order.envia_summary = _(
                    "Envia rate selected: %(carrier)s · %(service)s · %(price).2f %(currency)s",
                    carrier=service.carrier_name or service.carrier,
                    service=service.service_name,
                    price=service.price,
                    currency=service.currency_name or order.currency_id.name,
                )
            else:
                order.envia_status = "none"
                order.envia_summary = False

    def _get_envia_shipping_product(self):
        return self.env.ref("envia.product_envia_shipping")

    def _get_active_envia_quote(self):
        self.ensure_one()
        forced = self.env.context.get("envia_force_quote_id")
        if forced:
            quote = self.env["envia.quote"].browse(forced)
            if quote.exists():
                return quote
        return self.envia_quote_ids.filtered(
            lambda quote: quote._is_label_ready()
        ).sorted("id", reverse=True)[:1]

    def _get_envia_quote_for_delivery_line(self):
        """Label-ready quote, or latest selection still validating branch route."""
        self.ensure_one()
        quote = self._get_active_envia_quote()
        if quote:
            return quote
        return self.envia_quote_ids.filtered("selected_service_id").sorted("id", reverse=True)[:1]

    def _get_restorable_envia_quote(self):
        """Last applied quote for reopening Update shipping cost (not only label-ready)."""
        self.ensure_one()
        if not self.delivery_set and not self.env.context.get("carrier_recompute"):
            return self.env["envia.quote"]
        active = self._get_active_envia_quote()
        if active:
            return active
        with_service = self.envia_quote_ids.filtered("selected_service_id")
        if with_service:
            return with_service[:1]
        pickup = self.envia_quote_ids.filtered(
            lambda quote: quote.origin_location_type == "branch"
            or quote.destination_location_type == "branch"
        )
        if pickup:
            return pickup[:1]
        envia_product = self.env.ref("envia.product_envia_shipping", raise_if_not_found=False)
        if envia_product and self.order_line.filtered(
            lambda line: line.is_delivery and line.product_id == envia_product
        ):
            return self.envia_quote_ids[:1]
        return self.env["envia.quote"]

    def _get_envia_delivery_carrier(self):
        self.ensure_one()
        return self.env.ref("envia.delivery_carrier_envia", raise_if_not_found=False)

    def action_open_delivery_wizard(self):
        self.ensure_one()
        if not self.env.context.get("carrier_recompute"):
            return super().action_open_delivery_wizard()
        carrier = self.carrier_id
        quote = self._get_restorable_envia_quote()
        envia_carrier = self._get_envia_delivery_carrier()
        if quote and envia_carrier:
            carrier = envia_carrier
        total_weight = self._get_estimated_weight()
        if not total_weight and quote:
            total_weight = quote.weight
        view_id = self.env.ref("delivery.choose_delivery_carrier_view_form").id
        context = {
            "default_order_id": self.id,
            "default_carrier_id": carrier.id if carrier else False,
            "default_total_weight": total_weight,
            "carrier_recompute": True,
        }
        wizard = self.env["choose.delivery.carrier"].with_context(context).create(
            {
                "order_id": self.id,
                "carrier_id": carrier.id if carrier else False,
                "total_weight": total_weight or 0.0,
            }
        )
        return {
            "name": _("Update shipping cost"),
            "type": "ir.actions.act_window",
            "view_mode": "form",
            "res_model": "choose.delivery.carrier",
            "view_id": view_id,
            "views": [(view_id, "form")],
            "target": "new",
            "res_id": wizard.id,
            "context": context,
        }

    def _envia_shipping_unit_price(self, quote):
        self.ensure_one()
        service = quote.selected_service_id
        service_currency = (
            self.env["res.currency"].search([("name", "=", service.currency_name)], limit=1)
            if service.currency_name
            else self.currency_id
        )
        return service_currency._convert(
            service.price,
            self.currency_id,
            self.company_id,
            fields.Date.context_today(self),
        )

    def _envia_delivery_line_description(self, quote=None):
        self.ensure_one()
        quote = quote or self._get_envia_quote_for_delivery_line()
        if quote and quote.selected_service_id:
            service = quote.selected_service_id
            return _(
                "%(carrier)s · %(service)s",
                carrier=service.carrier_name or service.carrier,
                service=service.service_name,
            )
        return _("Live rates from Envia.com")

    def _prepare_delivery_line_vals(self, carrier, price_unit):
        values = super()._prepare_delivery_line_vals(carrier, price_unit)
        if carrier.delivery_type == "envia":
            values["name"] = self._envia_delivery_line_description()
        return values

    def _envia_sync_service_id_from_quote(self, quote=None):
        self.ensure_one()
        quote = quote or self._get_active_envia_quote()
        service = quote.selected_service_id if quote else False
        envia_service_id = service.envia_service_id if service else False
        if self.envia_service_id != envia_service_id:
            self.envia_service_id = envia_service_id
        pickings = self.picking_ids.filtered(lambda picking: picking.state not in ("done", "cancel"))
        if pickings:
            pickings.write({"envia_service_id": envia_service_id})

    def _sync_envia_shipping_line(self, quote=None):
        self.ensure_one()
        quote = quote or self._get_active_envia_quote()
        if not quote:
            raise UserError(_("Get Envia rates and select a carrier first."))
        self._envia_sync_service_id_from_quote(quote)
        price = self._envia_shipping_unit_price(quote)
        carrier = self._get_envia_delivery_carrier()
        if carrier:
            # rate_shipment applies fiscal position + margin/% + fixed_margin + free_over.
            rate = carrier.rate_shipment(self)
            if rate.get("success"):
                price = rate["price"]
            else:
                price = carrier._apply_margins(price, self)
            self.set_delivery_line(carrier, price)
            return self.order_line.filtered("is_delivery")[:1]
        product = self._get_envia_shipping_product()
        line = self.order_line.filtered(lambda item: item.product_id == product)[:1]
        values = {
            "product_id": product.id,
            "product_uom_qty": 1.0,
            "price_unit": price,
            "name": self._envia_delivery_line_description(quote),
            "tax_ids": [(5, 0, 0)],
        }
        if line:
            line.write(values)
        else:
            self.write({"order_line": [(0, 0, values)]})
        return line or self.order_line.filtered(lambda item: item.product_id == product)[:1]

    def action_open_envia_quote_wizard(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Ship with Envia"),
            "res_model": "envia.quote.wizard",
            "view_mode": "form",
            "views": [(False, "form")],
            "target": "new",
            "context": {
                "default_sale_order_id": self.id,
                "default_destination_partner_id": self.partner_shipping_id.id,
                "dialog_size": "extra-large",
            },
        }

    def action_open_envia_delivery_wizard(self):
        self.ensure_one()
        carrier = self._get_envia_delivery_carrier()
        return {
            "type": "ir.actions.act_window",
            "name": _("Add shipping"),
            "res_model": "choose.delivery.carrier",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_order_id": self.id,
                "default_carrier_id": carrier.id if carrier else False,
                "envia_force_delivery_carrier": True,
            },
        }

    def action_view_envia_quotes(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Envia Quotes"),
            "res_model": "envia.quote",
            "view_mode": "list,form",
            "domain": [("sale_order_id", "=", self.id)],
        }

    def action_view_envia_shipments(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Envia Shipments"),
            "res_model": "envia.shipment",
            "view_mode": "list,form",
            "domain": [("sale_order_id", "=", self.id)],
        }

    def action_create_envia_shipment(self):
        self.ensure_one()
        quote = self._get_active_envia_quote()
        if not quote:
            raise UserError(
                _(
                    "Get Envia rates, select a carrier, and choose the required pickup "
                    "locations before generating the label."
                )
            )
        return quote.action_open_create_shipment_wizard()

    def action_confirm(self):
        orders = self.filtered("envia_service_id")
        result = super().action_confirm()
        for order in orders:
            order.picking_ids.filtered(
                lambda picking: picking.state not in ("done", "cancel")
            ).write({"envia_service_id": order.envia_service_id})
        return result

    def _set_pickup_location(self, pickup_location_data):
        super()._set_pickup_location(pickup_location_data)
        self.ensure_one()
        if self.carrier_id.delivery_type != "envia":
            return
        location = self.pickup_location_data or {}
        option = (location.get("additional_data") or {}).get("envia_option") or {}
        if not option and location.get("id"):
            # Location id is our option id: pickup:carrier:branch:service
            option = {
                "id": location.get("id"),
                "route_type": "pickup",
                "name": location.get("name"),
                "street": location.get("street"),
                "city": location.get("city"),
                "zip": location.get("zip_code"),
                "state_code": location.get("state"),
                "country_code": location.get("country_code"),
                "lat": location.get("latitude"),
                "lng": location.get("longitude"),
            }
            parts = str(location.get("id") or "").split(":")
            if len(parts) >= 4 and parts[0] == "pickup":
                option.update(
                    {
                        "carrier": parts[1],
                        "branch_code": parts[2],
                        "service_id": ":".join(parts[3:]),
                    }
                )
        if not option.get("branch_code"):
            return
        option.setdefault("route_type", "pickup")
        WebsitePickupService(self.env).apply_selection(self, option)

    def _check_cart_is_ready_to_be_paid(self):
        super()._check_cart_is_ready_to_be_paid()
        self.ensure_one()
        if self.only_services or self.carrier_id.delivery_type != "envia":
            return
        quote = self._get_active_envia_quote()
        if not quote:
            raise ValidationError(
                _(
                    "Select an Envia shipping rate or pickup location before paying."
                )
            )
        if (
            quote.destination_location_type == "branch"
            and not quote.destination_branch_code
        ):
            raise ValidationError(
                _("Select an Envia pickup location before paying.")
            )
