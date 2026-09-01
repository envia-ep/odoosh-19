def migrate(cr, version):
    """Bring existing DBs to the 19.0.1.0.0 public release state."""
    from odoo import SUPERUSER_ID, api

    env = api.Environment(cr, SUPERUSER_ID, {})

    product = env.ref("envia.product_envia_shipping", raise_if_not_found=False)
    if product and product.name == "Shipping":
        product.name = "Shipping - Envia.com"

    env["delivery.carrier"].search([("delivery_type", "=", "envia")]).write(
        {"integration_level": "rate"}
    )
    env["res.company"].search([]).write({"envia_enable_labels": True})

    cron = env.ref("envia.ir_cron_envia_tracking_sync", raise_if_not_found=False)
    if cron:
        cron.unlink()
    cr.execute("DROP TABLE IF EXISTS envia_tracking_event CASCADE")

    langs = [
        code
        for code, _name in env["res.lang"].get_installed()
        if code == "es_419" or code.startswith("es")
    ]
    if langs:
        env["ir.module.module"]._load_module_terms(["envia"], langs, overwrite=True)
