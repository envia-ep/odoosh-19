import { ListRenderer } from "@web/views/list/list_renderer";
import { EnviaOnboardingBanner } from "../../components/envia_onboarding/envia_onboarding";

export class EnviaQuoteListRenderer extends ListRenderer {
    static template = "envia.QuoteListRenderer";
    static components = {
        ...ListRenderer.components,
        EnviaOnboardingBanner,
    };
}
