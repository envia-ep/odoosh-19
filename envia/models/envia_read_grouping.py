from odoo import api, fields, models


class EnviaReadGroupingMixin(models.AbstractModel):
    _name = "envia.read.grouping.mixin"
    _description = "Envia Module Payload"

    envia_module = fields.Json(
        string="Envia Module",
        compute="_compute_envia_module",
    )

    @api.depends(
        "envia_quote_ids",
        "envia_quote_ids.selected_service_id",
        "envia_quote_ids.selected_service_id.carrier",
        "envia_quote_ids.selected_service_id.carrier_name",
        "envia_quote_ids.selected_service_id.service_name",
        "envia_quote_ids.selected_service_id.envia_service_id",
        "envia_quote_ids.selected_service_id.price",
        "envia_quote_ids.selected_service_id.drop_off",
        "envia_quote_ids.origin_branch_code",
        "envia_quote_ids.destination_branch_code",
        "envia_service_id",
        "envia_status",
    )
    def _compute_envia_module(self):
        for record in self:
            record.envia_module = record._envia_module_values()

    def _get_envia_module_quote(self):
        self.ensure_one()
        forced = self.env.context.get("envia_force_quote_id")
        if forced:
            quote = self.env["envia.quote"].browse(forced)
            if quote.exists():
                return quote
        service_id = getattr(self, "envia_service_id", False)
        quotes = self.envia_quote_ids
        if service_id:
            matched = quotes.filtered(
                lambda item: item.selected_service_id.envia_service_id == service_id
            ).sorted("id", reverse=True)[:1]
            if matched:
                return matched
        if hasattr(self, "_get_active_envia_quote"):
            quote = self._get_active_envia_quote()
            if quote:
                return quote
        return quotes.filtered("selected_service_id").sorted("id", reverse=True)[:1]

    def _envia_module_values(self):
        self.ensure_one()
        quote = self._get_envia_module_quote()
        if quote:
            return quote._envia_module_values()
        return {
            "branch_code": "",
            "service_id": "",
            "service_name": "",
            "carrier_id": "",
            "carrier_name": "",
            "cost": "",
        }
