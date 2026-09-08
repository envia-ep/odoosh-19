/** @odoo-module **/

import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { CopyButton } from "@web/core/copy_button/copy_button";
import { standardFieldProps } from "@web/views/fields/standard_field_props";

import { Component, useState } from "@odoo/owl";

export class EnviaApiKeyField extends Component {
    static template = "envia.EnviaApiKeyField";
    static components = { CopyButton };
    static props = {
        ...standardFieldProps,
    };

    setup() {
        this.state = useState({ isRevealed: false });
        this.copySuccessText = _t("Copied");
    }

    get value() {
        return this.props.record.data[this.props.name] || "";
    }

    get buttonTitle() {
        return this.state.isRevealed ? _t("Hide value") : _t("Reveal value");
    }

    get maskedValue() {
        return "•".repeat(Math.min(this.value.length, 24));
    }

    get copyButtonClassName() {
        return "o_btn_char_copy btn-sm";
    }

    get copyButtonIcon() {
        return "fa-clipboard";
    }

    get helperText() {
        return _t("Read-only. Reveal to view or use the clipboard icon to copy.");
    }

    get revealButtonClass() {
        return this.state.isRevealed ? "fa-eye-slash" : "fa-eye";
    }

    onToggleReveal() {
        this.state.isRevealed = !this.state.isRevealed;
    }
}

export const enviaApiKeyField = {
    component: EnviaApiKeyField,
    displayName: _t("Envia API Key"),
    supportedTypes: ["char"],
};

registry.category("fields").add("envia_api_key", enviaApiKeyField);
