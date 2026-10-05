from odoo import _, api, fields, models
from odoo.tools.misc import format_datetime


class ResUsersApikeys(models.Model):
    _inherit = "res.users.apikeys"

    envia_expiration_label = fields.Char(
        compute="_compute_envia_expiration_label",
        string="Expiration Label",
    )

    @api.depends("expiration_date")
    def _compute_envia_expiration_label(self) -> None:
        for api_key in self:
            if api_key.expiration_date:
                api_key.envia_expiration_label = format_datetime(
                    self.env,
                    api_key.expiration_date,
                    dt_format="medium",
                )
            else:
                api_key.envia_expiration_label = _("No expiration")
