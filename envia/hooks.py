import logging

from odoo.tools import config
from psycopg2 import sql

from .services.envia_plugin_setup import queue_pending_setup

_logger = logging.getLogger(__name__)
LEGACY_MODULE_NAME = "envia_shipping"
MODULE_NAME = "envia"
HTTP_BRIDGE_MODULE = "envia_http"
_ENVIA_PRODUCT_TEMPLATE_FIELDS = (
    "dimensional_uom_id",
    "product_length",
    "product_width",
    "product_height",
    "envia_volumetric_weight",
)


def _http_bridge_server_wide_modules():
    return config.get("server_wide_modules") or ["web", "base", "web"]


def warn_if_http_bridge_missing(*, at_install: bool = False) -> bool:
    """Return True when envia_http is listed in server_wide_modules."""
    if HTTP_BRIDGE_MODULE in _http_bridge_server_wide_modules():
        return True
    message = (
        "Add %r to server_wide_modules in odoo.conf (local and production) so "
        "POST /envia/integration/callback works without X-Odoo-Database. "
        "Example: server_wide_modules = web,base,%s. "
        "Then restart Odoo and verify with: "
        "curl -X POST https://<your-domain>/envia/integration/callback"
    )
    log = _logger.error if at_install else _logger.warning
    log(message, HTTP_BRIDGE_MODULE, HTTP_BRIDGE_MODULE)
    return False


def post_load():
    warn_if_http_bridge_missing()


def _drop_legacy_branch_carrier_columns(cr):
    """Char columns block Many2one registration on envia.quote.wizard."""
    cr.execute(
        """
        SELECT column_name
          FROM information_schema.columns
         WHERE table_name = 'envia_quote_wizard'
           AND column_name IN ('origin_branch_carrier', 'destination_branch_carrier')
           AND data_type IN ('character varying', 'text')
        """
    )
    for (column_name,) in cr.fetchall():
        cr.execute(
            f'ALTER TABLE envia_quote_wizard DROP COLUMN IF EXISTS "{column_name}"'
        )


def _cleanup_envia_product_dimension_artifacts(cr):
    """Drop legacy envia dimension views/fields left after removing product_template.py."""
    cr.execute(
        """
        DELETE FROM ir_ui_view v
         WHERE v.model = 'product.template'
           AND (
               v.arch_db::text LIKE '%%dimensional_uom_id%%'
               OR v.arch_db::text LIKE '%%envia_shipping%%'
               OR v.arch_db::text LIKE '%%envia_volumetric_weight%%'
           )
           AND (
               EXISTS (
                   SELECT 1
                     FROM ir_model_data imd
                    WHERE imd.model = 'ir.ui.view'
                      AND imd.res_id = v.id
                      AND imd.module = %s
               )
               OR NOT EXISTS (
                   SELECT 1
                     FROM ir_model_data imd
                    WHERE imd.model = 'ir.ui.view'
                      AND imd.res_id = v.id
               )
           )
           AND NOT EXISTS (
               SELECT 1
                 FROM ir_model_data imd
                WHERE imd.model = 'ir.ui.view'
                  AND imd.res_id = v.id
                  AND imd.module = 'product_dimension'
           )
        """,
        (MODULE_NAME,),
    )
    for field_name in _ENVIA_PRODUCT_TEMPLATE_FIELDS:
        cr.execute(
            """
            SELECT f.id
              FROM ir_model_fields f
              JOIN ir_model m ON m.id = f.model_id
              JOIN ir_model_data imd
                ON imd.model = 'ir.model.fields'
               AND imd.res_id = f.id
             WHERE m.model = 'product.template'
               AND f.name = %s
               AND imd.module = %s
            """,
            (field_name, MODULE_NAME),
        )
        row = cr.fetchone()
        if not row:
            continue
        field_id = row[0]
        cr.execute(
            "DELETE FROM ir_model_data WHERE model = 'ir.model.fields' AND res_id = %s",
            (field_id,),
        )
        cr.execute("DELETE FROM ir_model_fields WHERE id = %s", (field_id,))
        cr.execute(
            sql.SQL("ALTER TABLE product_template DROP COLUMN IF EXISTS {}").format(
                sql.Identifier(field_name)
            )
        )


def pre_init_hook(env):
    """Rename a legacy envia_shipping installation to envia before module load."""
    cr = env.cr
    _drop_legacy_branch_carrier_columns(cr)
    _cleanup_envia_product_dimension_artifacts(cr)
    cr.execute(
        """
        UPDATE ir_model_data
           SET name = %s
         WHERE module = 'base'
           AND model = 'ir.module.module'
           AND name = %s
        """,
        (f"module_{MODULE_NAME}", f"module_{LEGACY_MODULE_NAME}"),
    )
    cr.execute("SELECT 1 FROM ir_module_module WHERE name = %s", (LEGACY_MODULE_NAME,))
    if not cr.fetchone():
        return

    cr.execute(
        "SELECT 1 FROM ir_module_module WHERE name = %s AND id != (SELECT id FROM ir_module_module WHERE name = %s LIMIT 1)",
        (MODULE_NAME, LEGACY_MODULE_NAME),
    )
    if cr.fetchone():
        return

    cr.execute(
        "UPDATE ir_module_module SET name = %s WHERE name = %s",
        (MODULE_NAME, LEGACY_MODULE_NAME),
    )
    cr.execute(
        "UPDATE ir_module_module_dependency SET name = %s WHERE name = %s",
        (MODULE_NAME, LEGACY_MODULE_NAME),
    )
    cr.execute(
        "UPDATE ir_model_data SET module = %s WHERE module = %s",
        (MODULE_NAME, LEGACY_MODULE_NAME),
    )
    cr.execute(
        """
        UPDATE ir_config_parameter
           SET key = REPLACE(key, %s, %s)
         WHERE key LIKE %s
        """,
        (f"{LEGACY_MODULE_NAME}.", f"{MODULE_NAME}.", f"{LEGACY_MODULE_NAME}.%"),
    )
    cr.execute(
        """
        UPDATE ir_asset
           SET path = REPLACE(path, %s, %s)
         WHERE path LIKE %s
        """,
        (f"{LEGACY_MODULE_NAME}/", f"{MODULE_NAME}/", f"{LEGACY_MODULE_NAME}/%"),
    )
    cr.execute(
        """
        UPDATE ir_ui_view
           SET arch_db = REPLACE(arch_db::text, %s, %s)::jsonb
         WHERE arch_db::text LIKE %s
        """,
        (f"{LEGACY_MODULE_NAME}.", f"{MODULE_NAME}.", f"%{LEGACY_MODULE_NAME}.%"),
    )
    cr.execute(
        """
        UPDATE ir_act_window
           SET context = REPLACE(context, %s, %s)
         WHERE context LIKE %s
        """,
        (LEGACY_MODULE_NAME, MODULE_NAME, f"%{LEGACY_MODULE_NAME}%"),
    )
    cr.execute(
        """
        UPDATE ir_cron
           SET code = REPLACE(code, %s, %s)
         WHERE code LIKE %s
        """,
        (LEGACY_MODULE_NAME, MODULE_NAME, f"%{LEGACY_MODULE_NAME}%"),
    )


def post_init_hook(env):
    warn_if_http_bridge_missing(at_install=True)
    company = env.ref("base.main_company")
    if not company._envia_is_shipping_api_configured():
        queue_pending_setup(env)
    env["onboarding.onboarding"].sudo().search(
        [("route_name", "=", "envia_quotes")]
    ).with_company(company)._search_or_create_progress()
