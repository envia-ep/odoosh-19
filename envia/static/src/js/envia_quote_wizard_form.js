/** @odoo-module **/

import { useEffect, onMounted, onWillUnmount } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { FormController } from "@web/views/form/form_controller";
import { formView } from "@web/views/form/form_view";

const AUTO_QUOTE_DEBOUNCE_MS = 400;

// ponytail: only reload after actions that return envia_wizard_noop; navigation actions destroy the modal.
const WIZARD_RELOAD_ACTIONS = new Set([
    "action_get_quote",
    "action_fill_sandbox_test_route",
    "action_load_origin_branches",
    "action_load_destination_branches",
    "action_lookup_origin_zipcode",
    "action_lookup_destination_zipcode",
    "action_reload_origin_address",
    "action_reload_destination_address",
    "action_back_to_address",
]);

export class EnviaQuoteWizardController extends FormController {
    setup() {
        super.setup();
        this.orm = useService("orm");
        this._autoQuoteTimer = null;
        this._autoQuoteRunning = false;
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

        useEffect(
            () => {
                if (this._shouldScheduleAutoQuote(this.model.root)) {
                    this._scheduleAutoQuote();
                }
                return () => clearTimeout(this._autoQuoteTimer);
            },
            () => {
                const record = this.model.root;
                const serviceLines = record.data.service_line_ids;
                const serviceCount = Array.isArray(serviceLines)
                    ? serviceLines.length
                    : serviceLines?.count || 0;
                return [
                    record.resId,
                    record.data.quote_id,
                    serviceCount,
                    record.data.origin_location_type,
                    record.data.destination_location_type,
                    record.data.can_get_rates,
                    record.data.origin_postal_code,
                    record.data.destination_postal_code,
                    record.data.origin_branch_count,
                    record.data.destination_branch_count,
                    record.data.weight,
                ];
            }
        );
    }

    _hasServiceLines(record) {
        const lines = record.data.service_line_ids;
        return Array.isArray(lines) ? lines.length > 0 : Boolean(lines?.count);
    }

    _hasQuoteResults(record) {
        return Boolean(record.data.quote_id) || this._hasServiceLines(record);
    }

    _shouldScheduleAutoQuote(record) {
        if (!record.resId || this._hasQuoteResults(record)) {
            return false;
        }
        const data = record.data;
        return (
            data.origin_location_type === "address" &&
            data.destination_location_type === "address" &&
            Boolean(data.can_get_rates)
        );
    }

    async _safeReloadWizard() {
        if (!this.model.root.resId || this.__owl__.isDestroyed) {
            return;
        }
        await this.model.root.load();
    }

    async _refreshWizardView(resId) {
        await this.orm.call("envia.quote.wizard", "action_refresh_wizard_view", [[resId]]);
        await this._safeReloadWizard();
    }

    _scheduleAutoQuote() {
        clearTimeout(this._autoQuoteTimer);
        this._autoQuoteTimer = setTimeout(() => this._runAutoQuote(), AUTO_QUOTE_DEBOUNCE_MS);
    }

    async _runAutoQuote() {
        if (this._autoQuoteRunning) {
            return;
        }
        const record = this.model.root;
        if (!this._shouldScheduleAutoQuote(record)) {
            return;
        }
        this._autoQuoteRunning = true;
        try {
            if (record.dirty) {
                const saved = await this.model.root.save();
                if (!saved) {
                    return;
                }
            }
            await this._refreshWizardView(this.model.root.resId);
        } finally {
            this._autoQuoteRunning = false;
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
        const wizardId = this.model.root.resId;
        if (!serviceId || !wizardId) {
            return;
        }
        await this.orm.call(
            "envia.quote.wizard",
            "action_select_service_rate",
            [[wizardId]],
            { service_id: serviceId }
        );
        await this._safeReloadWizard();
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
        const wizardId = this.model.root.resId;
        if (!side || !branchCode || !carrier || !wizardId) {
            return;
        }
        await this.orm.call(
            "envia.quote.wizard",
            "action_select_branch_option",
            [[wizardId]],
            { side, branch_code: branchCode, carrier }
        );
        await this._safeReloadWizard();
    }

    async beforeExecuteActionButton(clickParams) {
        const saved = await super.beforeExecuteActionButton(clickParams);
        return saved !== false;
    }

    async afterExecuteActionButton(clickParams) {
        await super.afterExecuteActionButton?.(clickParams);
        if (!WIZARD_RELOAD_ACTIONS.has(clickParams.name)) {
            return;
        }
        await this._safeReloadWizard();
    }

    async onWillStart() {
        await super.onWillStart();
        const record = this.model.root;
        if (!this._shouldScheduleAutoQuote(record)) {
            return;
        }
        await this._refreshWizardView(record.resId);
    }
}

export const enviaQuoteWizardFormView = {
    ...formView,
    Controller: EnviaQuoteWizardController,
};

registry.category("views").add("envia_quote_wizard_form", enviaQuoteWizardFormView);
