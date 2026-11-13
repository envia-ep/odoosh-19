from odoo import api, models


class OnboardingOnboarding(models.Model):
    _inherit = "onboarding.onboarding"

    @api.model
    def action_close_panel_envia_quotes(self):
        self.action_close_panel("envia.onboarding_onboarding_envia_quotes")

    def _envia_sync_quote_onboarding_steps(self):
        company = self.env.company
        step_model = self.env["onboarding.onboarding.step"].sudo().with_company(company)
        if company._envia_is_shipping_api_configured():
            step_model.action_validate_step("envia.onboarding_onboarding_step_connect")
        if self.env["sale.order"].search_count(
            [("state", "in", ("sale", "done"))],
            limit=1,
        ):
            step_model.action_validate_step("envia.onboarding_onboarding_step_sale_order")
        if self.env["envia.quote"].search_count([("company_id", "=", company.id)], limit=1):
            step_model.action_validate_step("envia.onboarding_onboarding_step_get_rates")
        if self.env["envia.shipment"].search_count([("company_id", "=", company.id)], limit=1):
            step_model.action_validate_step("envia.onboarding_onboarding_step_create_label")

    def _prepare_rendering_values(self):
        self.ensure_one()
        if self == self.env.ref("envia.onboarding_onboarding_envia_quotes", raise_if_not_found=False):
            self._envia_sync_quote_onboarding_steps()
        return super()._prepare_rendering_values()
