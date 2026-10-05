-- Run once before upgrading the renamed module (odoo_dev example):
-- docker compose exec -T db psql -U odoo -d odoo_dev -f - < addons/envia/scripts/migrate_envia_shipping_to_envia.sql

UPDATE ir_module_module SET name = 'envia' WHERE name = 'envia_shipping';
UPDATE ir_module_module_dependency SET name = 'envia' WHERE name = 'envia_shipping';
UPDATE ir_model_data SET module = 'envia' WHERE module = 'envia_shipping';
UPDATE ir_config_parameter
   SET key = REPLACE(key, 'envia_shipping.', 'envia.')
 WHERE key LIKE 'envia_shipping.%';
UPDATE ir_asset
   SET path = REPLACE(path, 'envia_shipping/', 'envia/')
 WHERE path LIKE 'envia_shipping/%';
UPDATE ir_ui_view
   SET arch_db = REPLACE(arch_db::text, 'envia_shipping.', 'envia.')::jsonb
 WHERE arch_db::text LIKE '%envia_shipping.%';
UPDATE ir_act_window
   SET context = REPLACE(context, 'envia_shipping', 'envia')
 WHERE context LIKE '%envia_shipping%';
UPDATE ir_cron
   SET code = REPLACE(code, 'envia_shipping', 'envia')
 WHERE code LIKE '%envia_shipping%';
