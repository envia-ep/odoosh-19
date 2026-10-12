def migrate(cr, version):
    """Align existing 19.0.1.0.0 DBs with the 19.0.2.0.0 feature release."""
    from odoo import SUPERUSER_ID, api

    env = api.Environment(cr, SUPERUSER_ID, {})

    env["delivery.carrier"].search([("delivery_type", "=", "envia")]).write(
        {"integration_level": "rate"}
    )
    env["res.company"].search([]).write({"envia_enable_labels": True})
