/** @odoo-module **/

import { Component } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { standardActionServiceProps } from "@web/webclient/actions/action_service";

export class EnviaDashboardAction extends Component {
    static template = "envia.EnviaDashboardAction";
    static props = { ...standardActionServiceProps };

    get url() {
        return this.props.action?.params?.url || "";
    }
}

registry.category("actions").add("envia_dashboard", EnviaDashboardAction);
