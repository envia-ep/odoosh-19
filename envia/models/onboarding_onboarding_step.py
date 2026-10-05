from odoo import _, api, models


class OnboardingOnboardingStep(models.Model):
    _inherit = "onboarding.onboarding.step"

    @api.model
    def action_open_step_envia_connect(self):
        return self.env["envia.plugin.connect.wizard"].action_open_connect_wizard()

    @api.model
    def _envia_confirmed_sale_order(self):
        return self.env["sale.order"].search(
            [("state", "in", ("sale", "done"))],
            limit=1,
            order="id desc",
        )

    @api.model
    def action_open_step_envia_sale_order(self):
        self.action_validate_step("envia.onboarding_onboarding_step_sale_order")
        order = self._envia_confirmed_sale_order()
        if order:
            return {
                "type": "ir.actions.act_window",
                "name": _("Sale Order"),
                "res_model": "sale.order",
                "res_id": order.id,
                "view_mode": "form",
                "views": [(False, "form")],
                "target": "current",
            }
        return {
            "type": "ir.actions.act_window",
            "name": _("Sales Orders"),
            "res_model": "sale.order",
            "view_mode": "list,form",
            "views": [(False, "list"), (False, "form")],
            "domain": [("state", "in", ("sale", "done"))],
            "target": "current",
        }

    @api.model
    def action_open_step_envia_get_rates(self):
        order = self._envia_confirmed_sale_order()
        if not order:
            return self.action_open_step_envia_sale_order()
        return order.action_open_envia_delivery_wizard()

    @api.model
    def action_open_step_envia_create_label(self):
        quote = self.env["envia.quote"].search(
            [
                ("company_id", "=", self.env.company.id),
                ("selected_service_id", "!=", False),
                ("state", "=", "quoted"),
            ],
            limit=1,
            order="create_date desc",
        )
        if quote:
            return quote.action_open_create_shipment_wizard()
        quote = self.env["envia.quote"].search(
            [("company_id", "=", self.env.company.id), ("state", "=", "quoted")],
            limit=1,
            order="create_date desc",
        )
        if quote:
            return {
                "type": "ir.actions.act_window",
                "name": _("Envia Quote"),
                "res_model": "envia.quote",
                "res_id": quote.id,
                "view_mode": "form",
                "views": [(False, "form")],
                "target": "current",
            }
        return self.action_open_step_envia_get_rates()
