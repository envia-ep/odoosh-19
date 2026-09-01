import { rpc } from "@web/core/network/rpc";
import { useService } from "@web/core/utils/hooks";
import { Component, onWillStart, useState } from "@odoo/owl";

export class EnviaOnboardingBanner extends Component {
    static template = "envia.OnboardingBanner";

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.state = useState({ data: null });
        onWillStart(async () => {
            this.state.data = await this.orm.call(
                "envia.quote",
                "get_quotes_onboarding_data",
                [],
            );
        });
    }

    get steps() {
        return this.state.data?.steps || [];
    }

    async onboardingLinkClicked(step) {
        const result = await rpc("/web/dataset/call_button", {
            model: "onboarding.onboarding.step",
            method: step.action,
            args: [],
            kwargs: {},
        });
        if (!result?.type) {
            await this.reloadOnboarding();
            return;
        }
        await this.action.doAction(result, {
            onClose: () => this.reloadOnboarding(),
        });
    }

    async closeOnboarding() {
        await this.orm.call("onboarding.onboarding", "action_close_panel_envia_quotes", []);
        await this.reloadOnboarding();
    }

    async reloadOnboarding() {
        this.state.data = await this.orm.call(
            "envia.quote",
            "get_quotes_onboarding_data",
            [],
        );
    }
}
