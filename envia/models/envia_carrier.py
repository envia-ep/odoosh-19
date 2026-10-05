from odoo import models, fields


class EnviaCarrier(models.Model):
    _name = "envia.carrier"
    _description = "Envia Carrier"
    _order = "name, code"
    _rec_names_search = ["name", "code"]

    name = fields.Char(required=True, translate=True)
    code = fields.Char(required=True, index=True)
    color = fields.Integer(string="Color Index")
    active = fields.Boolean(default=True)
    country_codes = fields.Char(
        string="Country Codes",
        help="Comma-separated ISO country codes where this carrier is commonly available.",
    )

    _envia_carrier_code_unique = models.Constraint(
        "unique(code)",
        "Carrier code must be unique.",
    )
