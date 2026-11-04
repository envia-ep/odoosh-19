from odoo import fields, models


class EnviaQuoteService(models.Model):
    _name = "envia.quote.service"
    _description = "Envia Quote Service Option"
    _order = "price asc"

    quote_id = fields.Many2one("envia.quote", required=True, ondelete="cascade")
    service_id = fields.Char(string="Service ID", required=True)
    envia_service_id = fields.Integer(string="Envia Service ID")
    carrier = fields.Char(required=True)
    carrier_name = fields.Char()
    service_name = fields.Char(required=True)
    price = fields.Float(required=True)
    currency_id = fields.Many2one(
        "res.currency",
        default=lambda self: self.env.company.currency_id,
    )
    currency_name = fields.Char()
    estimated_delivery_days = fields.Integer()
    drop_off = fields.Integer()
    max_weight = fields.Float()
    restrictions = fields.Text()
    additional_services_available = fields.Text()
    is_selected = fields.Boolean()

    def action_select_service(self):
        self.ensure_one()
        quote = self.quote_id
        quote.service_ids.write({"is_selected": False})
        self.is_selected = True
        quote.write({"selected_service_id": self.id})
        quote._sync_envia_service_id_targets()
        quote.write(
            {
                "state": "quoted"
                if quote._branch_fields_complete() and quote._route_matches_selected_service()
                else "draft"
            }
        )
        quote._retire_sibling_quotes()
        return True
