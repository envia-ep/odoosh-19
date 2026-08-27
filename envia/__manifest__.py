{
    "name": "Envia.com Quote and Shipping",
    "version": "19.0.2.0.0",
    "category": "Inventory/Delivery",
    "summary": "The best shipping solution for your business. Connect to 150+ couriers worldwide for domestic and international shipments.",
    "description": """
Envia.com integration for Odoo 19
=================================

Connect your Odoo store with Envia.com and quote live carrier rates on sale
orders through the native Add shipping flow. Labels are generated manually on
the delivery order (Generate / Replace Envia Label); Validate does not create
labels. Tracking uses the Core carrier contract.

Installation
------------

Install only **Envia.com** from Apps. Odoo installs all module dependencies
automatically (including ``envia_http`` from the same package, plus standard
apps such as Sales and Inventory).

Server administrators must add ``envia_http`` to ``server_wide_modules`` in
``odoo.conf`` and restart Odoo so OAuth and integration callbacks work before
a database is selected::

    server_wide_modules = web,base,envia_http

Supported on **Odoo.sh** and **on-premise** (Community or Enterprise).
Not compatible with standard **Odoo Online** (odoo.com SaaS).

OAuth endpoints
---------------

This package always uses Envia.com **production** API hosts.
Developers override with ``ENVIA_ENVIRONMENT=sandbox`` and related ``ENVIA_*``
env vars (e.g. Docker). OAuth URLs in module data must use production client
credentials from Envia.com before release.
    """,
    "author": "Envia.com",
    "website": "https://envia.com",
    "license": "Other proprietary",
    "depends": [
        "base",
        "mail",
        "onboarding",
        "sale",
        "stock",
        "sale_stock",
        "stock_delivery",
        "delivery",
        "website_sale",
        "envia_http",
    ],
    "data": [
        "data/ir_sequence_data.xml",
        "security/envia_security.xml",
        "security/ir.model.access.csv",
        "security/envia_warehouse_origin_security.xml",
        "data/ir_cron_data.xml",
        "data/envia_carrier_data.xml",
        "data/envia_product_data.xml",
        "data/delivery_carrier_data.xml",
        "data/envia_oauth_config_data.xml",
        "data/envia_onboarding_data.xml",
        "views/res_config_settings_views.xml",
        "views/res_users_apikeys_views.xml",
        "views/envia_quote_views.xml",
        "views/envia_shipment_views.xml",
        "views/sale_order_views.xml",
        "views/choose_delivery_carrier_views.xml",
        "views/stock_picking_views.xml",
        "views/stock_warehouse_views.xml",
        "views/product_views.xml",
        "wizards/envia_quote_wizard_views.xml",
        "wizards/envia_create_shipment_wizard_views.xml",
        "wizards/envia_plugin_connect_wizard_views.xml",
        "wizards/envia_quote_onboarding_wizard_views.xml",
        "wizards/envia_billing_info_wizard_views.xml",
        "wizards/envia_warehouse_origin_wizard_views.xml",
        "views/website_sale_delivery_templates.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "envia/static/src/scss/envia_wizards.scss",
            "envia/static/src/js/envia_api_key_field.js",
            "envia/static/src/xml/envia_api_key_field.xml",
            "envia/static/src/js/envia_enable_labels_field.js",
            "envia/static/src/js/envia_dashboard_action.js",
            "envia/static/src/xml/envia_dashboard_action.xml",
            "envia/static/src/js/envia_plugin_connect_wizard_form.js",
            "envia/static/src/js/envia_wizard_noop_action.js",
            "envia/static/src/js/envia_quote_wizard_form.js",
            "envia/static/src/js/envia_choose_delivery_carrier_form.js",
            "envia/static/src/components/envia_onboarding/**/*",
            "envia/static/src/components/envia_generic_form/**/*",
            "envia/static/src/views/envia_quote_list/**/*",
        ],
        "web.assets_frontend": [
            "envia/static/src/website_sale_delivery/envia_checkout.scss",
            "envia/static/src/website_sale_delivery/envia_checkout.js",
        ],
    },
    "images": [
        "static/description/banner.png",
        "static/description/screenshot_connect_wizard.png",
        "static/description/screenshot_envia_portal.png",
        "static/description/screenshot_settings.png",
        "static/description/screenshot_product_dimensions.png",
        "static/description/screenshot_add_shipping_start.png",
        "static/description/screenshot_add_shipping_carrier.png",
        "static/description/screenshot_add_shipping_route.png",
        "static/description/screenshot_add_shipping_rates.png",
        "static/description/screenshot_add_shipping_order.png",
    ],
    "installable": True,
    "application": True,
    "post_init_hook": "post_init_hook",
    "post_load": "post_load",
    "pre_init_hook": "pre_init_hook",
}
