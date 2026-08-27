/** @odoo-module **/

import { BooleanField, booleanField } from "@web/views/fields/boolean/boolean_field";
import { ConfirmationDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

export class EnviaEnableLabelsField extends BooleanField {
    setup() {
        super.setup();
        this.dialogService = useService("dialog");
    }

    onChange(newValue) {
        super.onChange(newValue);
        if (!newValue) {
            return;
        }
        this.dialogService.add(ConfirmationDialog, {
            title: _t("Enable labels in Envia"),
            body: _t(
                'This only shows the buttons in Odoo. Also turn on "Label generation from the store" in Envia for this shop.'
            ),
        });
    }
}

export const enviaEnableLabelsField = {
    ...booleanField,
    component: EnviaEnableLabelsField,
};

registry.category("fields").add("envia_enable_labels", enviaEnableLabelsField);
