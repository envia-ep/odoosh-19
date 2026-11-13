from odoo import _, api, fields, models
from odoo.exceptions import UserError

from ..services.international import (
    DUTIES_PAYMENT_SELECTION,
    export_reason_selection,
    is_international,
    normalize_export_reason,
)


class EnviaCreateShipmentWizard(models.TransientModel):
    _name = "envia.create.shipment.wizard"
    _description = "Create Envia Shipment Wizard"

    quote_id = fields.Many2one("envia.quote", required=True)
    sale_order_id = fields.Many2one("sale.order")
    picking_id = fields.Many2one("stock.picking")
    selected_service_name = fields.Char(
        related="quote_id.selected_service_id.service_name", readonly=True
    )
    selected_carrier_name = fields.Char(
        related="quote_id.selected_service_id.carrier_name", readonly=True
    )
    selected_price = fields.Float(related="quote_id.selected_service_id.price", readonly=True)
    selected_currency_name = fields.Char(
        related="quote_id.selected_service_id.currency_name", readonly=True
    )
    route_summary = fields.Char(compute="_compute_route_summary")
    package_summary = fields.Char(compute="_compute_package_summary")
    is_international = fields.Boolean(compute="_compute_is_international")
    identification_number_origin = fields.Char(string="Origin Tax ID")
    destination_identification_number = fields.Char(string="Destination Tax ID")
    export_reason = fields.Selection(
        selection=lambda self: export_reason_selection(),
        string="Export Reason",
        default="sale",
    )
    duties_payment_entity = fields.Selection(
        selection=DUTIES_PAYMENT_SELECTION,
        string="Duties Payer",
        default="sender",
    )

    @api.depends(
        "quote_id",
        "quote_id.origin_country",
        "quote_id.destination_country",
    )
    def _compute_is_international(self):
        for wizard in self:
            quote = wizard.quote_id
            if quote and wizard.sale_order_id:
                wizard.is_international = wizard.sale_order_id._envia_is_international(
                    quote
                )
            else:
                wizard.is_international = bool(
                    quote
                    and is_international(
                        quote.origin_country, quote.destination_country
                    )
                )

    def _compute_route_summary(self):
        for wizard in self:
            quote = wizard.quote_id
            if not quote:
                wizard.route_summary = False
                continue
            origin = f"{quote.origin_postal_code} {quote.origin_state or '?'}, {quote.origin_country}"
            destination = (
                f"{quote.destination_postal_code} {quote.destination_state or '?'}, "
                f"{quote.destination_country}"
            )
            wizard.route_summary = f"{origin} → {destination}"

    def _compute_package_summary(self):
        for wizard in self:
            quote = wizard.quote_id
            if not quote:
                wizard.package_summary = False
                continue
            wizard.package_summary = _(
                "%(weight)s kg · %(content)s",
                weight=quote.weight,
                content=quote.content,
            )

    @api.model
    def default_get(self, fields_list):
        defaults = super().default_get(fields_list)
        quote = self.env["envia.quote"].browse(defaults.get("quote_id") or False)
        order = self.env["sale.order"].browse(defaults.get("sale_order_id") or False)
        if not order and quote:
            order = quote.sale_order_id
        if quote:
            defaults.setdefault(
                "identification_number_origin",
                quote.identification_number_origin
                or (order._envia_resolve_origin_tax_id(quote) if order else False)
                or False,
            )
            defaults.setdefault(
                "destination_identification_number",
                (order._envia_resolve_destination_tax_id(quote) if order else False)
                or False,
            )
            defaults.setdefault(
                "export_reason",
                quote.export_reason
                or (order.envia_export_reason if order else False)
                or "sale",
            )
            defaults.setdefault(
                "duties_payment_entity",
                quote.duties_payment_entity
                or (order.envia_duties_payment_entity if order else False)
                or "sender",
            )
        elif order:
            defaults.setdefault(
                "identification_number_origin",
                order._envia_resolve_origin_tax_id(),
            )
            defaults.setdefault(
                "destination_identification_number",
                order._envia_resolve_destination_tax_id(),
            )
            defaults.setdefault("export_reason", order.envia_export_reason or "sale")
            defaults.setdefault(
                "duties_payment_entity",
                order.envia_duties_payment_entity or "sender",
            )
        return defaults

    def _persist_customs(self):
        self.ensure_one()
        destination_tax = (self.destination_identification_number or "").strip()
        if not destination_tax and self.sale_order_id:
            destination_tax = self.sale_order_id._envia_resolve_destination_tax_id(
                self.quote_id
            )
        values = {
            "identification_number_origin": (
                self.identification_number_origin or ""
            ).strip()
            or False,
            "destination_identification_number": destination_tax or False,
            "export_reason": normalize_export_reason(self.export_reason) or "sale",
            "duties_payment_entity": self.duties_payment_entity or "sender",
        }
        if self.quote_id:
            self.quote_id.write(values)
        if self.sale_order_id:
            self.sale_order_id.write(
                {
                    "envia_identification_number_origin": values[
                        "identification_number_origin"
                    ],
                    "envia_destination_identification_number": values[
                        "destination_identification_number"
                    ],
                    "envia_export_reason": values["export_reason"],
                    "envia_duties_payment_entity": values["duties_payment_entity"],
                }
            )

    def action_create_shipment(self):
        self.ensure_one()
        if not self.quote_id.selected_service_id:
            raise UserError(_("Select a carrier service before creating the shipment."))
        self._persist_customs()
        if self.picking_id and not self.quote_id.picking_id:
            self.quote_id.picking_id = self.picking_id.id
        # Ecommerce label/create path (delivery Generate Envia Label).
        if self.picking_id and self.picking_id.carrier_id.delivery_type == "envia":
            # Do not set envia_skip_dedicated_cursor: label/create needs the
            # post-persist commit so SERIALIZATION_FAILURE retries (Envia
            # XML-RPC writes the picking) do not drop orderId/shipmentId.
            return self.picking_id.with_context(
                envia_customs_confirmed=True,
                envia_force_quote_id=self.quote_id.id,
            ).action_envia_generate_label()
        shipment = self.env["envia.shipment"].action_create_shipment_from_quote(
            self.quote_id
        )
        return {
            "type": "ir.actions.act_window",
            "name": _("Envia Shipment"),
            "res_model": "envia.shipment",
            "view_mode": "form",
            "res_id": shipment.id,
            "target": "current",
        }
