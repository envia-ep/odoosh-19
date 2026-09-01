import { registry } from "@web/core/registry";
import { listView } from "@web/views/list/list_view";
import { EnviaQuoteListRenderer } from "./envia_quote_list_renderer";

export const enviaQuoteListView = {
    ...listView,
    Renderer: EnviaQuoteListRenderer,
};

registry.category("views").add("envia_quote_list", enviaQuoteListView);
