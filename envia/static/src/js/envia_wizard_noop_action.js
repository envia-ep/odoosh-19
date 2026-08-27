/** @odoo-module **/

import { registry } from "@web/core/registry";

// ponytail: Odoo 19 maps falsy call_button results to act_window_close; modal wizards use this instead.
registry.category("actions").add("envia_wizard_noop", () => {});
