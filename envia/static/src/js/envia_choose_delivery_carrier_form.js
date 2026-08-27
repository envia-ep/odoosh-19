/** @odoo-module **/

import { onMounted, onWillUnmount } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { FormController } from "@web/views/form/form_controller";
import { formView } from "@web/views/form/form_view";

export class EnviaChooseDeliveryCarrierController extends FormController {
    setup() {
        super.setup();
        this.orm = useService("orm");
        this.action = useService("action");
        this._optionClickHandler = (ev) => {
            this._onRateCardClick(ev);
            this._onBranchCardClick(ev);
        };
        onMounted(() => {
            this.rootRef.el?.addEventListener("click", this._optionClickHandler, true);
        });
        onWillUnmount(() => {
            this.rootRef.el?.removeEventListener("click", this._optionClickHandler, true);
        });
    }

    async _applyServerAction(action) {
        if (action && action.type === "ir.actions.act_window" && action.res_model) {
            await this.action.doAction(action, { stackPosition: "replaceCurrentAction" });
            return true;
        }
        return false;
    }

    async _safeReloadForm() {
        const record = this.model.root;
        if (!record.resId || this.__owl__.isDestroyed) {
            return;
        }
        await record.load();
    }

    _getWizardId(record) {
        const wizard = record.data.envia_wizard_id;
        if (Array.isArray(wizard)) {
            return wizard[0] || null;
        }
        return wizard?.id || wizard || null;
    }

    async _ensureSavedRecord() {
        const record = this.model.root;
        if (record.resId) {
            return true;
        }
        return Boolean(await record.save());
    }

    _syncRateSelectionInUI(serviceId) {
        const lines = this.model.root.data.envia_service_line_ids;
        const records = lines?.records || lines;
        if (!Array.isArray(records)) {
            return;
        }
        for (const line of records) {
            const selected = line.data?.service_id === serviceId;
            if (line.data) {
                line.data.is_selected = selected;
            }
        }
    }

    async _syncPriceFieldsFromServer() {
        const record = this.model.root;
        if (!record.resId) {
            return;
        }
        const [data] = await this.orm.read(
            "choose.delivery.carrier",
            [record.resId],
            [
                "display_price",
                "delivery_price",
                "delivery_message",
                "envia_has_selected_rate",
                "envia_selected_service_label",
            ]
        );
        if (!data) {
            return;
        }
        for (const field of [
            "display_price",
            "delivery_price",
            "delivery_message",
            "envia_has_selected_rate",
            "envia_selected_service_label",
        ]) {
            record.data[field] = data[field];
        }
    }

    async _onRateCardClick(ev) {
        const card = ev.target.closest(".o_envia_select_rate");
        if (!card || !this.rootRef.el?.contains(card)) {
            return;
        }
        ev.preventDefault();
        ev.stopPropagation();
        const serviceId = card.dataset.serviceId;
        const record = this.model.root;
        if (!serviceId || !(await this._ensureSavedRecord())) {
            return;
        }
        this._syncRateSelectionInUI(serviceId);
        const action = await this.orm.call(
            "choose.delivery.carrier",
            "action_envia_select_service",
            [[record.resId]],
            { service_id: serviceId }
        );
        this._syncRateSelectionInUI(serviceId);
        if (await this._applyServerAction(action)) {
            return;
        }
        await this._safeReloadForm();
    }

    async _onBranchCardClick(ev) {
        const card = ev.target.closest(".o_envia_select_branch");
        if (!card || !this.rootRef.el?.contains(card)) {
            return;
        }
        ev.preventDefault();
        ev.stopPropagation();
        const side = card.dataset.branchSide;
        const branchCode = card.dataset.branchCode;
        const carrier = card.dataset.carrier;
        const record = this.model.root;
        if (!side || !branchCode || !carrier) {
            return;
        }
        if (!(await this._ensureSavedRecord())) {
            return;
        }
        const action = await this.orm.call(
            "choose.delivery.carrier",
            "action_envia_select_branch",
            [[record.resId]],
            { side, branch_code: branchCode, carrier }
        );
        if (await this._applyServerAction(action)) {
            return;
        }
        await this._safeReloadForm();
        await this._syncPriceFieldsFromServer();
    }
}

export const enviaChooseDeliveryCarrierFormView = {
    ...formView,
    Controller: EnviaChooseDeliveryCarrierController,
};

registry.category("views").add(
    "envia_choose_delivery_carrier_form",
    enviaChooseDeliveryCarrierFormView
);
