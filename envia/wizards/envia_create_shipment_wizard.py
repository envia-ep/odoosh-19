from odoo import _, fields, models
from odoo.exceptions import UserError


class EnviaCreateShipmentWizard(models.TransientModel):
    _name = "envia.create.shipment.wizard"
    _description = "Create Envia Shipment Wizard"

    quote_id = fields.Many2one("envia.quote", required=True)
    sale_order_id = fields.Many2one("sale.order")
    picking_id = fields.Many2one("stock.picking")
    selected_service_name = fields.Char(related="quote_id.selected_service_id.service_name", readonly=True)
    selected_carrier_name = fields.Char(related="quote_id.selected_service_id.carrier_name", readonly=True)
    selected_price = fields.Float(related="quote_id.selected_service_id.price", readonly=True)
    selected_currency_name = fields.Char(related="quote_id.selected_service_id.currency_name", readonly=True)
    route_summary = fields.Char(compute="_compute_route_summary")
    package_summary = fields.Char(compute="_compute_package_summary")

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

    def action_create_shipment(self):
        self.ensure_one()
        if not self.quote_id.selected_service_id:
            raise UserError(_("Select a carrier service before creating the shipment."))
        if self.picking_id and not self.quote_id.picking_id:
            self.quote_id.picking_id = self.picking_id.id
        shipment = self.env["envia.shipment"].action_create_shipment_from_quote(self.quote_id)
        return {
            "type": "ir.actions.act_window",
            "name": _("Envia Shipment"),
            "res_model": "envia.shipment",
            "view_mode": "form",
            "res_id": shipment.id,
            "target": "current",
        }
