from datetime import datetime

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from ..const import ENVIA_LOCATION_TYPE_SELECTION
from ..services.payload_mapper import PayloadMapper


class EnviaQuote(models.Model):
    _name = "envia.quote"
    _description = "Envia Quote"
    _order = "create_date desc"

    name = fields.Char(default="New", required=True, copy=False)
    quote_id = fields.Char(string="External Quote ID", copy=False)
    sale_order_id = fields.Many2one("sale.order", ondelete="set null")
    picking_id = fields.Many2one("stock.picking", ondelete="set null")
    origin_partner_id = fields.Many2one("res.partner", string="Ship From", ondelete="set null")
    destination_partner_id = fields.Many2one("res.partner", string="Ship To", ondelete="set null")
    company_id = fields.Many2one("res.company", default=lambda self: self.env.company, required=True)
    origin_postal_code = fields.Char(required=True)
    origin_country = fields.Char(required=True)
    origin_state = fields.Char()
    origin_city = fields.Char()
    origin_location_type = fields.Selection(
        ENVIA_LOCATION_TYPE_SELECTION,
        default="address",
    )
    origin_branch_code = fields.Char()
    origin_branch_name = fields.Char()
    origin_branch_street = fields.Char()
    origin_branch_number = fields.Char()
    destination_postal_code = fields.Char(required=True)
    destination_country = fields.Char(required=True)
    destination_state = fields.Char()
    destination_city = fields.Char()
    destination_location_type = fields.Selection(
        ENVIA_LOCATION_TYPE_SELECTION,
        default="address",
    )
    destination_branch_code = fields.Char()
    destination_branch_name = fields.Char()
    destination_branch_street = fields.Char()
    destination_branch_number = fields.Char()
    weight = fields.Float(required=True)
    content = fields.Char(required=True)
    declared_value = fields.Float()
    currency_id = fields.Many2one(
        "res.currency",
        default=lambda self: self.env.company.currency_id,
    )
    carriers = fields.Char(default="all")
    valid_until = fields.Datetime()
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("quoted", "Quoted"),
            ("expired", "Expired"),
            ("used", "Used"),
        ],
        default="draft",
    )
    service_ids = fields.One2many("envia.quote.service", "quote_id", string="Services")
    selected_service_id = fields.Many2one("envia.quote.service", string="Selected Service")
    selected_service_label = fields.Char(compute="_compute_selected_service_label")
    envia_enable_labels = fields.Boolean(related="company_id.envia_enable_labels")
    envia_show_quote_archive = fields.Boolean(related="company_id.envia_show_quote_archive")
    shipment_ids = fields.One2many("envia.shipment", "quote_id", string="Shipments")

    @api.depends(
        "selected_service_id",
        "selected_service_id.carrier_name",
        "selected_service_id.carrier",
        "selected_service_id.service_name",
        "selected_service_id.price",
        "selected_service_id.currency_name",
        "currency_id",
    )
    def _compute_selected_service_label(self):
        for quote in self:
            service = quote.selected_service_id
            if not service:
                quote.selected_service_label = False
                continue
            quote.selected_service_label = _(
                "%(carrier)s · %(service)s · %(price).2f %(currency)s",
                carrier=service.carrier_name or service.carrier,
                service=service.service_name,
                price=service.price,
                currency=service.currency_name or quote.currency_id.name,
            )

    @api.model
    def get_quotes_onboarding_data(self):
        onboarding = self.env.ref(
            "envia.onboarding_onboarding_envia_quotes",
            raise_if_not_found=False,
        )
        if not onboarding or onboarding.is_onboarding_closed:
            return False
        if onboarding.current_onboarding_state == "done":
            return False
        ob_vals = onboarding._prepare_rendering_values()
        return {
            "close_method": onboarding.panel_close_action_name,
            "steps": [
                {
                    "id": step.id,
                    "title": step.title,
                    "description": step.description,
                    "state": ob_vals["state"][step.id],
                    "action": step.panel_step_open_action_name,
                }
                for step in ob_vals["steps"]
            ],
        }

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", "New") == "New":
                vals["name"] = self.env["ir.sequence"].next_by_code("envia.quote") or "New"
        records = super().create(vals_list)
        pending_companies = records.company_id.filtered("envia_quote_onboarding_pending")
        if pending_companies:
            pending_companies.envia_quote_onboarding_pending = False
        return records

    def action_open_create_shipment_wizard(self):
        self.ensure_one()
        self._validate_label_generation()
        return {
            "type": "ir.actions.act_window",
            "name": _("Generate Label"),
            "res_model": "envia.create.shipment.wizard",
            "view_mode": "form",
            "views": [(False, "form")],
            "target": "new",
            "context": {
                "default_quote_id": self.id,
                "default_sale_order_id": self.sale_order_id.id,
                "default_picking_id": self.picking_id.id,
                "dialog_size": "extra-large",
            },
        }

    def _check_quote_valid(self):
        self.ensure_one()
        if self.valid_until and self.valid_until < fields.Datetime.now():
            self.state = "expired"
            raise UserError(_("This quote has expired. Request a new quote."))

    _DROP_OFF_REQUIRED_BRANCHES = {
        # Origin=branch means "drop at any carrier branch"; only the destination
        # ocurre branch is a concrete selectable code sent as branch_code.
        1: (),
        2: ("destination",),
        3: ("destination",),
    }

    def _branch_fields_complete(self):
        self.ensure_one()
        if self.destination_location_type == "branch" and not self.destination_branch_code:
            return False
        return True

    def _route_matches_selected_service(self):
        self.ensure_one()
        selected = self.selected_service_id
        if not selected or not selected.drop_off:
            return True
        for side in self._DROP_OFF_REQUIRED_BRANCHES.get(selected.drop_off, ()):
            if getattr(self, f"{side}_location_type") != "branch" or not getattr(
                self, f"{side}_branch_code"
            ):
                return False
        return True

    def _is_label_ready(self):
        self.ensure_one()
        return (
            self.state == "quoted"
            and bool(self.selected_service_id)
            and self._branch_fields_complete()
            and self._route_matches_selected_service()
        )

    def _retire_sibling_quotes(self):
        """Mark older quoted rates used so label/create cannot reuse them."""
        self.ensure_one()
        siblings = self.env["envia.quote"]
        if self.sale_order_id:
            siblings |= self.sale_order_id.envia_quote_ids
        if self.picking_id:
            siblings |= self.picking_id.envia_quote_ids
        (siblings - self).filtered(lambda item: item.state == "quoted").write(
            {"state": "used"}
        )

    @staticmethod
    def _envia_module_str(value, *, money=False):
        if value in (None, False, ""):
            return ""
        if money:
            return f"{float(value):.2f}"
        return str(value)

    def _envia_module_branch_code(self):
        self.ensure_one()
        service = self.selected_service_id
        if not service:
            return ""
        required = self._DROP_OFF_REQUIRED_BRANCHES.get(service.drop_off or 0, ())
        candidates = []
        for side in ("destination", "origin"):
            code = (getattr(self, f"{side}_branch_code") or "").strip()
            if not code:
                continue
            if side in required:
                return code
            candidates.append(code)
        return candidates[0] if candidates else ""

    def _envia_module_values(self):
        """Selected rate payload for sale.order / stock.picking envia_module."""
        self.ensure_one()
        service = self.selected_service_id
        if not service:
            return {
                "branch_code": "",
                "service_id": "",
                "service_name": "",
                "carrier_id": "",
                "carrier_name": "",
                "cost": "",
            }
        return {
            "branch_code": self._envia_module_str(self._envia_module_branch_code()),
            "service_id": self._envia_module_str(service.envia_service_id or ""),
            "service_name": self._envia_module_str(service.service_name or ""),
            "carrier_id": self._envia_module_str(service.carrier or ""),
            "carrier_name": self._envia_module_str(
                service.carrier_name or service.carrier or ""
            ),
            "cost": self._envia_module_str(service.price, money=True),
        }

    def _sync_envia_service_id_targets(self):
        for quote in self:
            service = quote.selected_service_id
            envia_service_id = service.envia_service_id if service else False
            if quote.sale_order_id:
                quote.sale_order_id._envia_sync_service_id_from_quote(quote)
            elif quote.picking_id:
                quote.picking_id.envia_service_id = envia_service_id

    def _validate_label_generation(self):
        self.ensure_one()
        if not self.company_id.envia_enable_labels:
            raise UserError(
                _("Enable label generation in Settings > Envia.com before creating labels.")
            )
        self._check_quote_valid()
        if not self.selected_service_id:
            raise UserError(_("Select a carrier service before creating the shipment."))
        if self.destination_location_type == "branch" and not self.destination_branch_code:
            raise UserError(
                _(
                    "Pickup destination is missing on this quote. "
                    "Reopen Ship with Envia and select the branch again."
                )
            )
        selected = self.selected_service_id
        if selected.drop_off:
            for side in self._DROP_OFF_REQUIRED_BRANCHES.get(selected.drop_off, ()):
                if getattr(self, f"{side}_location_type") != "branch" or not getattr(
                    self, f"{side}_branch_code"
                ):
                    label = _("origin") if side == "origin" else _("destination")
                    raise UserError(
                        _(
                            "The selected carrier service requires a %(side)s pickup location. "
                            "Reopen Ship with Envia, select the branch, and generate the label again."
                        )
                        % {"side": label}
                    )

    def _get_shipment_partners(self):
        self.ensure_one()
        origin = self.origin_partner_id
        if not origin:
            origin = self.company_id._envia_get_default_origin_partner()
        destination = self.destination_partner_id
        if not destination and self.picking_id:
            destination = self.picking_id.partner_id
        elif not destination and self.sale_order_id:
            destination = self.sale_order_id.partner_shipping_id
        if not origin:
            raise UserError(_("Origin address is missing on this quote."))
        if self.destination_location_type != "branch" and not destination:
            raise UserError(_("Destination address is missing on this quote."))
        return origin, destination

    def _build_shipment_contact(self, side):
        self.ensure_one()
        company = self.company_id
        prefix = side
        location_type = getattr(self, f"{prefix}_location_type") or "address"
        country_code = getattr(self, f"{prefix}_country")
        state_code = getattr(self, f"{prefix}_state")
        postal_code = getattr(self, f"{prefix}_postal_code")
        city = getattr(self, f"{prefix}_city")
        if location_type == "branch":
            branch_code = getattr(self, f"{prefix}_branch_code")
            if not branch_code:
                label = _("origin") if side == "origin" else _("destination")
                raise UserError(_("Pickup %(side)s is missing branch data on the quote.") % {"side": label})
            return PayloadMapper.build_branch_contact(
                getattr(self, f"{prefix}_branch_name"),
                getattr(self, f"{prefix}_branch_street"),
                city,
                state_code,
                postal_code,
                country_code,
                branch_code,
                company,
                number=getattr(self, f"{prefix}_branch_number") or None,
            )
        origin_partner, destination_partner = self._get_shipment_partners()
        partner = origin_partner if side == "origin" else destination_partner
        country = self.env["res.country"].search([("code", "=", country_code)], limit=1)
        state = self.env["res.country.state"].search(
            [("country_id", "=", country.id), ("code", "in", [state_code] if state_code else [])],
            limit=1,
        ) if country and state_code else self.env["res.country.state"]
        return PayloadMapper.build_side_contact(
            partner, postal_code, city, country, state, company
        )

    def action_open_quote_wizard(self):
        return self.env["envia.quote.wizard"].action_open_quote_wizard()

    @api.model
    def create_from_api_response(self, response, values):
        quote = self.create(
            {
                **values,
                "quote_id": response.quote_id,
                "valid_until": self._parse_valid_until(response.valid_until),
                "state": "draft",
            }
        )
        service_lines = []
        for service in response.services:
            service_lines.append(
                {
                    "quote_id": quote.id,
                    "service_id": str(service.service_id),
                    "envia_service_id": service.envia_service_id,
                    "carrier": service.carrier,
                    "carrier_name": service.carrier_name,
                    "service_name": service.service_name,
                    "price": service.price,
                    "currency_name": service.currency,
                    "estimated_delivery_days": service.estimated_delivery_days,
                    "drop_off": service.drop_off,
                    "max_weight": service.max_weight,
                    "restrictions": "\n".join(service.restrictions),
                    "additional_services_available": ", ".join(service.additional_services_available),
                }
            )
        self.env["envia.quote.service"].create(service_lines)
        return quote

    def _parse_valid_until(self, value):
        if not value:
            return False
        if isinstance(value, datetime):
            return value
        try:
            return fields.Datetime.to_datetime(value.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return False
