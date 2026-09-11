from odoo import api, fields, models


class EnviaQuoteOnboardingWizard(models.TransientModel):
    _name = "envia.quote.onboarding.wizard"
    _description = "Envia Quote Onboarding Wizard"

    company_id = fields.Many2one(
        "res.company",
        required=True,
        default=lambda self: self.env.company,
    )

    def _dismiss(self) -> None:
        self.ensure_one()
        self.company_id.envia_quote_onboarding_pending = False

    def action_go_to_quotes(self):
        self.ensure_one()
        self._dismiss()
        return self.env["ir.actions.act_window"]._for_xml_id("envia.action_envia_config_settings")

    @api.model
    def get_entry_action(self):
        company = self.env.company
        company.envia_quote_onboarding_pending = False
        return self.env["ir.actions.act_window"]._for_xml_id("envia.action_envia_config_settings")
