from odoo.addons.envia.hooks import _cleanup_envia_product_dimension_artifacts


def migrate(cr, version):
    _cleanup_envia_product_dimension_artifacts(cr)
