from odoo import fields, models


class ProductTemplate(models.Model):
    _inherit = "product.template"

    envia_product_code = fields.Char(
        string="Envia HS Code",
        help="Harmonized System (HS) code sent to Envia as productCode for customs.",
    )


class ProductProduct(models.Model):
    _inherit = "product.product"

    envia_product_code = fields.Char(
        related="product_tmpl_id.envia_product_code",
        readonly=False,
    )
