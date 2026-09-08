/** @odoo-module **/

import { _t } from "@web/core/l10n/translation";
import { ORM } from "@web/core/orm_service";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { FormController } from "@web/views/form/form_controller";
import { formView } from "@web/views/form/form_view";
import { useEffect } from "@odoo/owl";

const ENVIA_POPUP_WINDOW_NAME = "envia_oauth_connect";
const POPUP_WIDTH = 320;
const POPUP_HEIGHT = 260;
const POPUP_CLOSED_WATCH_MS = 500;
// After close: Envia callback may commit a moment later.
const VERIFY_AFTER_CLOSE_ATTEMPTS = 6;
const VERIFY_AFTER_CLOSE_DELAY_MS = 1000;
const POLL_WHILE_WAITING_MS = 2000;

/**
 * Survives FormController remount after action_run_integration writes state.
 * Close detection must live here — instance fields are wiped on reload.
 */
const sharedEnviaPopup = {
    window: null,
    watchTimer: null,
    wizardId: null,
    actionService: null,
    loadRecord: null,
    verifying: false,
    resolved: false,
};

/** Survives FormController remount/destroy after successful connect. */
const detachedOrm = new ORM();

function isDestroyedComponentError(error) {
    return error?.message === "Component is destroyed";
}

function markIntegrationResolved() {
    sharedEnviaPopup.resolved = true;
    stopSharedCloseWatch();
    sharedEnviaPopup.window = null;
}

function delay(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
}

function stopSharedCloseWatch() {
    if (sharedEnviaPopup.watchTimer) {
        clearInterval(sharedEnviaPopup.watchTimer);
        sharedEnviaPopup.watchTimer = null;
    }
}

async function verifyAfterSharedPopupClosed() {
    const wizardId = sharedEnviaPopup.wizardId;
    const actionService = sharedEnviaPopup.actionService;
    if (
        sharedEnviaPopup.resolved ||
        sharedEnviaPopup.verifying ||
        !wizardId ||
        !actionService
    ) {
        return;
    }
    sharedEnviaPopup.verifying = true;
    stopSharedCloseWatch();
    try {
        for (let attempt = 0; attempt < VERIFY_AFTER_CLOSE_ATTEMPTS; attempt++) {
            if (sharedEnviaPopup.resolved) {
                return;
            }
            if (attempt > 0) {
                await delay(VERIFY_AFTER_CLOSE_DELAY_MS);
            }
            const nextAction = await detachedOrm.call(
                "envia.plugin.connect.wizard",
                "action_poll_integration_status",
                [[wizardId]]
            );
            if (nextAction && nextAction.type) {
                markIntegrationResolved();
                try {
                    await actionService.doAction(nextAction);
                } catch (error) {
                    if (!isDestroyedComponentError(error)) {
                        throw error;
                    }
                }
                return;
            }
        }
        if (sharedEnviaPopup.resolved) {
            return;
        }
        const cancelAction = await detachedOrm.call(
            "envia.plugin.connect.wizard",
            "action_on_external_popup_closed",
            [[wizardId]]
        );
        if (cancelAction && cancelAction.type) {
            try {
                await actionService.doAction(cancelAction);
            } catch (error) {
                if (!isDestroyedComponentError(error)) {
                    throw error;
                }
            }
        } else if (sharedEnviaPopup.loadRecord) {
            try {
                await sharedEnviaPopup.loadRecord();
            } catch (error) {
                if (!isDestroyedComponentError(error)) {
                    throw error;
                }
            }
        }
    } finally {
        sharedEnviaPopup.verifying = false;
    }
}

function startSharedCloseWatch() {
    if (sharedEnviaPopup.watchTimer) {
        return;
    }
    sharedEnviaPopup.watchTimer = setInterval(() => {
        const popup = sharedEnviaPopup.window;
        if (!popup || !popup.closed) {
            return;
        }
        stopSharedCloseWatch();
        sharedEnviaPopup.window = null;
        verifyAfterSharedPopupClosed();
    }, POPUP_CLOSED_WATCH_MS);
}

export class EnviaPluginConnectWizardController extends FormController {
    setup() {
        super.setup();
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.actionService = useService("action");

        useEffect(
            () => {
                const record = this.model.root;
                this._syncSharedServices(record.resId);
                if (record.data.state !== "waiting_external") {
                    return;
                }
                this._ensureCloseWatchFromSharedPopup(record);
                const pollTimer = setInterval(async () => {
                    if (!record.resId || sharedEnviaPopup.resolved) {
                        return;
                    }
                    try {
                        const nextAction = await this.orm.call(
                            "envia.plugin.connect.wizard",
                            "action_poll_integration_status",
                            [[record.resId]]
                        );
                        if (nextAction && nextAction.type) {
                            clearInterval(pollTimer);
                            markIntegrationResolved();
                            await this.actionService.doAction(nextAction);
                        }
                    } catch (error) {
                        if (!isDestroyedComponentError(error)) {
                            throw error;
                        }
                    }
                }, POLL_WHILE_WAITING_MS);
                return () => clearInterval(pollTimer);
            },
            () => [this.model.root.data.state, this.model.root.resId]
        );
    }

    async beforeExecuteActionButton(clickParams) {
        if (
            clickParams.name === "action_run_integration" ||
            clickParams.name === "action_open_envia_integration"
        ) {
            this._openEnviaIntegrationFromRecord();
        }
        return super.beforeExecuteActionButton(clickParams);
    }

    async onWillStart() {
        await super.onWillStart();
        const record = this.model.root;
        if (!record.resId || record.data.state !== "ready") {
            return;
        }
        const nextAction = await this.orm.call(
            "envia.plugin.connect.wizard",
            "action_redirect_if_configured",
            [[record.resId]]
        );
        if (nextAction && nextAction.type) {
            await this.actionService.doAction(nextAction);
        }
    }

    _syncSharedServices(wizardId) {
        sharedEnviaPopup.actionService = this.actionService;
        sharedEnviaPopup.loadRecord = () => this.model.root.load();
        if (wizardId) {
            sharedEnviaPopup.wizardId = wizardId;
        }
    }

    _ensureCloseWatchFromSharedPopup(record) {
        const popup = sharedEnviaPopup.window;
        if (popup) {
            if (popup.closed) {
                sharedEnviaPopup.window = null;
                stopSharedCloseWatch();
                verifyAfterSharedPopupClosed();
                return;
            }
            startSharedCloseWatch();
            return;
        }
        if (record.data.external_popup_url) {
            this._openEnviaIntegrationFromRecord();
            if (!sharedEnviaPopup.window) {
                this._notifyEnviaNotOpened();
            }
        }
    }

    _buildPopupFeatures() {
        const left = Math.max(0, Math.round((window.screen.width - POPUP_WIDTH) / 2));
        const top = Math.max(0, Math.round((window.screen.height - POPUP_HEIGHT) / 2));
        return [
            `width=${POPUP_WIDTH}`,
            `height=${POPUP_HEIGHT}`,
            `left=${left}`,
            `top=${top}`,
            "resizable=yes",
            "scrollbars=yes",
            "toolbar=no",
            "menubar=no",
        ].join(",");
    }

    _notifyEnviaNotOpened() {
        const useSizedPopup = this.model.root.data.integration_use_sized_popup;
        this.notification.add(
            useSizedPopup
                ? _t(
                      "Envia.com did not open. Click Open Envia.com below or allow pop-ups for this site."
                  )
                : _t("Envia.com did not open. Click Open Envia.com below."),
            { type: "warning", sticky: true }
        );
    }

    _openEnviaIntegrationFromRecord() {
        const record = this.model.root;
        const url = record.data.external_popup_url;
        if (!url) {
            return;
        }
        this._syncSharedServices(record.resId);
        sharedEnviaPopup.resolved = false;
        // window.open (not act_url): only then can we read popup.closed.
        const features = record.data.integration_use_sized_popup ? this._buildPopupFeatures() : "";
        const popup = window.open(url, ENVIA_POPUP_WINDOW_NAME, features);
        if (!popup) {
            this._notifyEnviaNotOpened();
            return;
        }
        sharedEnviaPopup.window = popup;
        startSharedCloseWatch();
    }
}

export const enviaPluginConnectWizardFormView = {
    ...formView,
    Controller: EnviaPluginConnectWizardController,
};

registry.category("views").add("envia_plugin_connect_wizard_form", enviaPluginConnectWizardFormView);
