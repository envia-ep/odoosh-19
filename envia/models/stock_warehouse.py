from odoo import fields, models


class StockWarehouse(models.Model):
    _inherit = "stock.warehouse"

    envia_origin_id = fields.Many2one(
        "envia.warehouse.origin",
        string="Envia Origin Link",
        compute="_compute_envia_origin",
    )
    envia_origin_label = fields.Char(
        string="Envia origin",
        compute="_compute_envia_origin",
    )

    def _compute_envia_origin(self) -> None:
        Origin = self.env["envia.warehouse.origin"]
        for warehouse in self:
            match = Origin.search([("warehouse_id", "=", warehouse.id)], limit=1)
            warehouse.envia_origin_id = match
            warehouse.envia_origin_label = match.envia_address_label if match else False

    def action_envia_link_origin(self):
        self.ensure_one()
        return self.env["envia.warehouse.origin.wizard"].with_context(
            default_warehouse_id=self.id,
            active_id=self.id,
            active_model="stock.warehouse",
        ).action_open_wizard()

    def action_envia_create_origin(self):
        self.ensure_one()
        return self.env["envia.billing.info.wizard"].action_open_billing_info_wizard(
            partner=self.partner_id,
            warehouse=self,
        )

    def _envia_origin_address_id(self):
        self.ensure_one()
        return (self.envia_origin_id.envia_address_id or "").strip() or False
