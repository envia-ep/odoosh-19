/** @odoo-module **/

import { patch } from "@web/core/utils/patch";
import { patchDynamicContent } from "@web/public/utils";
import { rpc } from "@web/core/network/rpc";
import { loadCSS, loadJS } from "@web/core/assets";
import { _t } from "@web/core/l10n/translation";
import { Checkout } from "@website_sale/interactions/checkout";

const LEAFLET_JS = "https://unpkg.com/leaflet@1.9.4/dist/leaflet.js";
const LEAFLET_CSS = "https://unpkg.com/leaflet@1.9.4/dist/leaflet.css";

patch(Checkout.prototype, {
    setup() {
        super.setup();
        patchDynamicContent(this.dynamicContent, {
            ".o_envia_route_btn": { "t-on-click": this.onClickEnviaRoute.bind(this) },
            // Delegate on the list so dynamically added radios work without updateContent.
            ".o_envia_options_list": { "t-on-change": this.onChangeEnviaOption.bind(this) },
        });
        this._enviaLeafletReady = null;
        this._enviaMap = null;
        this._enviaMarkers = [];
        this._enviaOptions = [];
        this._enviaOptionsCache = {};
        this._enviaFetchPromises = {};
        this._enviaAppliedShipOptionId = null;
        this._enviaLoadSeq = 0;
    },

    async start() {
        await super.start(...arguments);
        // colibri does not await start(); boot after the current turn so isReady stays stable.
        Promise.resolve().then(() => {
            if (!this.isDestroyed) {
                this._bootEnviaPanel();
            }
        });
    },

    async _bootEnviaPanel() {
        if (this.isDestroyed) {
            return;
        }
        const checked = this.el.querySelector(
            'input[name="o_delivery_radio"][data-delivery-type="envia"]:checked'
        );
        if (!checked) {
            return;
        }
        const panel = checked
            .closest("[name='o_delivery_method']")
            ?.querySelector(".o_envia_delivery_panel");
        if (!panel) {
            return;
        }
        panel.classList.remove("d-none");
        this._enviaOptionsCache = {};
        this._enviaFetchPromises = {};
        this._enviaAppliedShipOptionId = null;
        this._prefetchEnviaOptions();
        await this._loadEnviaOptions(panel, this._enviaRouteType(panel));
    },

    async selectDeliveryMethod(ev) {
        // Capture before await: currentTarget is cleared after the event handler yields.
        const radio = ev.currentTarget;
        // Apply cheapest Ship before super rates: envia_rate_shipment reuses the
        // active quote, so a stale expensive selection would paint the badge wrong.
        if (radio?.dataset?.deliveryType === "envia") {
            const panel = radio.closest("[name='o_delivery_method']")?.querySelector(
                ".o_envia_delivery_panel"
            );
            if (panel) {
                panel.classList.remove("d-none");
                this._enviaOptionsCache = {};
                this._enviaFetchPromises = {};
                this._enviaAppliedShipOptionId = null;
                this._prefetchEnviaOptions();
                await this._loadEnviaOptions(panel, this._enviaRouteType(panel));
            }
        }
        if (this.isDestroyed || !radio) {
            return;
        }
        // Core reads ev.currentTarget; after await the real Event has currentTarget=null.
        // Pass a plain shim (do not spread Event — currentTarget may stay null).
        await this.waitFor(super.selectDeliveryMethod({ currentTarget: radio }));
        if (this.isDestroyed) {
            return;
        }
        this._toggleEnviaPanels(radio);
    },

    _toggleEnviaPanels(selectedRadio) {
        this.el.querySelectorAll(".o_envia_delivery_panel").forEach((panel) => {
            const methodRadio = panel
                .closest("[name='o_delivery_method']")
                ?.querySelector("input[name='o_delivery_radio']");
            const selected =
                methodRadio &&
                selectedRadio &&
                methodRadio.dataset.dmId === selectedRadio.dataset.dmId;
            panel.classList.toggle("d-none", !selected);
        });
    },

    async onClickEnviaRoute(ev) {
        const button = ev.currentTarget;
        const panel = button.closest(".o_envia_delivery_panel");
        if (!panel) {
            return;
        }
        panel.querySelectorAll(".o_envia_route_btn").forEach((btn) => {
            btn.classList.toggle("active", btn === button);
        });
        await this._loadEnviaOptions(panel, button.dataset.routeType);
    },

    async onChangeEnviaOption(ev) {
        const radio = ev.target?.closest?.(".o_envia_option_radio") || ev.target;
        if (!radio?.classList?.contains("o_envia_option_radio")) {
            return;
        }
        const panel = radio.closest(".o_envia_delivery_panel");
        const option = this._enviaOptions.find((item) => item.id === radio.value);
        if (!option) {
            return;
        }
        this._highlightEnviaMarker(option.id);
        // Plain rpc (no waitFor): waitFor → updateContent wipes radios / races the badge.
        await this._applyEnviaOption(panel, option);
    },

    async _applyEnviaOption(panel, option) {
        if (!option || this.isDestroyed) {
            return null;
        }
        const result = await rpc("/shop/envia/delivery/select", {
            route_type: option.route_type,
            carrier: option.carrier,
            carrier_name: option.carrier_name,
            service_id: option.service_id,
            envia_service_id: option.envia_service_id,
            service: option.service,
            branch_code: option.branch_code,
            name: option.name,
            street: option.street,
            city: option.city,
            zip: option.zip,
            state_code: option.state_code,
            country_code: option.country_code,
            address: option.address,
            lat: option.lat,
            lng: option.lng,
            base_price: option.base_price ?? option.price,
            price: option.base_price ?? option.price,
            drop_off: option.drop_off,
        });
        if (this.isDestroyed) {
            return null;
        }
        if (result?.error || result?.success === false) {
            this._setEnviaFeedback(panel, result.error || _t("Could not apply rate."));
            return null;
        }
        this._setEnviaFeedback(panel, "");
        const carrierRadio = panel
            ?.closest("[name='o_delivery_method']")
            ?.querySelector("input[name='o_delivery_radio']");
        if (carrierRadio && typeof this._updateAmountBadge === "function") {
            this._updateAmountBadge(carrierRadio, result);
        }
        if (typeof this._updateCartSummaries === "function") {
            this._updateCartSummaries(result);
        }
        if (option.route_type === "ship" && option.id) {
            this._enviaAppliedShipOptionId = option.id;
        }
        return result;
    },

    _enviaRouteType(panel) {
        return (
            panel.querySelector(".o_envia_route_btn.active")?.dataset?.routeType || "ship"
        );
    },

    _prefetchEnviaOptions() {
        // Warm ship + pickup so switching tabs does not wait on a cold RPC.
        this._ensureEnviaFetch("ship");
        const panel = this.el.querySelector(".o_envia_delivery_panel");
        if (panel?.dataset?.pickupEnabled !== "0") {
            this._ensureEnviaFetch("pickup");
        }
    },

    _fetchEnviaOptions(routeType) {
        return rpc("/shop/envia/delivery/options", { route_type: routeType });
    },

    _storeEnviaCache(routeType, result) {
        this._enviaOptionsCache[routeType] = {
            options: result?.options || [],
            error: result?.error || null,
        };
        return this._enviaOptionsCache[routeType];
    },

    _ensureEnviaFetch(routeType) {
        if (this._enviaOptionsCache[routeType]) {
            return Promise.resolve(this._enviaOptionsCache[routeType]);
        }
        if (!this._enviaFetchPromises[routeType]) {
            this._enviaFetchPromises[routeType] = this._fetchEnviaOptions(routeType)
                .then((result) => this._storeEnviaCache(routeType, result))
                .catch((error) =>
                    this._storeEnviaCache(routeType, {
                        error: error?.message || String(error),
                        options: [],
                    })
                );
        }
        return this._enviaFetchPromises[routeType];
    },

    async _ensureEnviaCache(routeType) {
        return this._ensureEnviaFetch(routeType);
    },

    async _loadEnviaOptions(panel, routeType) {
        // Plain await (no waitFor): waitFor calls updateContent and races with destroy.
        if (this.isDestroyed || !panel) {
            return;
        }
        const loadSeq = ++this._enviaLoadSeq;
        this._setEnviaFeedback(panel, _t("Loading rates…"));
        const list = panel.querySelector(".o_envia_options_list");
        const mapEl = panel.querySelector(".o_envia_map");
        if (!list || !mapEl) {
            return;
        }
        list.innerHTML = "";
        // Reset visibility; pickup map-only re-hides after options load.
        list.classList.remove("d-none");
        mapEl.classList.add("d-none");
        if (this._enviaMap) {
            this._enviaMap.remove();
            this._enviaMap = null;
            this._enviaMarkers = [];
        }
        const cached = await this._ensureEnviaCache(routeType);
        // Drop stale paints: ship fetch finishing after a Pickup click used to flash
        // door-to-door rates, then get replaced (looked like filtering).
        if (
            this.isDestroyed ||
            loadSeq !== this._enviaLoadSeq ||
            this._enviaRouteType(panel) !== routeType
        ) {
            return;
        }
        if (cached.error) {
            this._setEnviaFeedback(panel, cached.error);
            this._enviaOptions = [];
            return;
        }
        this._enviaOptions = cached.options || [];
        if (!this._enviaOptions.length) {
            this._setEnviaFeedback(
                panel,
                routeType === "pickup"
                    ? _t("No pickup locations near this address.")
                    : _t("No rates available for this address.")
            );
            return;
        }
        this._setEnviaFeedback(panel, "");
        const mapOnly = routeType === "pickup" && panel.dataset.pickupMapOnly === "1";
        list.classList.toggle("d-none", mapOnly);
        const cheapest =
            routeType === "ship" ? this._cheapestEnviaOption(this._enviaOptions) : null;
        for (const option of this._enviaOptions) {
            const li = document.createElement("li");
            li.className = "list-group-item o_envia_option_item";
            li.dataset.optionId = option.id;
            const price =
                option.price != null ? `$${Number(option.price).toFixed(2)}` : "";
            const address = option.address
                ? `<span class="o_envia_option_address text-muted">${option.address}</span>`
                : "";
            const service = option.service
                ? `<span class="o_envia_option_service">${option.service}</span>`
                : "";
            const checked = cheapest && option.id === cheapest.id ? "checked" : "";
            if (checked) {
                li.classList.add("o_envia_option_selected");
            }
            li.innerHTML = `
                <label class="o_envia_option_label">
                    <input type="radio" class="form-check-input o_envia_option_radio"
                           name="o_envia_option" value="${option.id}" ${checked}/>
                    <span class="o_envia_option_body">
                        <span class="o_envia_option_header">
                            <span class="o_envia_option_name">${option.name || ""}</span>
                            <span class="o_envia_option_price">${price}</span>
                        </span>
                        ${address}
                        ${service}
                    </span>
                </label>`;
            list.appendChild(li);
        }
        if (routeType === "pickup") {
            // Map-only mode always renders the map (list stays hidden for pin selection).
            if (mapOnly || panel.dataset.showMap !== "0") {
                await this._renderEnviaMap(panel, this._enviaOptions, loadSeq);
            }
        } else if (cheapest && this._enviaAppliedShipOptionId !== cheapest.id) {
            // Apply in background so Ship options render immediately.
            this._applyEnviaOption(panel, cheapest).catch(() => {});
        }
    },

    _cheapestEnviaOption(options) {
        let cheapest = null;
        let cheapestPrice = Infinity;
        for (const option of options || []) {
            const price = Number(option?.price);
            if (!Number.isFinite(price) || price >= cheapestPrice) {
                continue;
            }
            cheapestPrice = price;
            cheapest = option;
        }
        return cheapest;
    },

    _setEnviaFeedback(panel, message) {
        const feedback = panel.querySelector(".o_envia_options_feedback");
        if (!feedback) {
            return;
        }
        feedback.textContent = message || "";
        feedback.classList.toggle("d-none", !message);
    },

    async _ensureLeaflet() {
        if (window.L) {
            return;
        }
        if (this._enviaLeafletReady) {
            return this._enviaLeafletReady;
        }
        this._enviaLeafletReady = Promise.all([
            loadJS(LEAFLET_JS),
            loadCSS(LEAFLET_CSS),
        ]).then(() => {
            if (!window.L) {
                throw new Error("Leaflet.js failed to load");
            }
        });
        return this._enviaLeafletReady;
    },

    async _renderEnviaMap(panel, options, loadSeq = this._enviaLoadSeq) {
        if (this.isDestroyed || loadSeq !== this._enviaLoadSeq) {
            return;
        }
        const mapEl = panel.querySelector(".o_envia_map");
        const withCoords = options.filter(
            (option) => Number(option.lat) && Number(option.lng)
        );
        // Always show the map container for Pickup (Leaflet), even if some pins lack coords.
        mapEl.classList.remove("d-none");
        await this._ensureLeaflet();
        if (
            this.isDestroyed ||
            loadSeq !== this._enviaLoadSeq ||
            this._enviaRouteType(panel) !== "pickup"
        ) {
            return;
        }
        const Leaflet = window.L;
        if (this._enviaMap) {
            this._enviaMap.remove();
            this._enviaMap = null;
            this._enviaMarkers = [];
        }
        const center = withCoords.length
            ? [Number(withCoords[0].lat), Number(withCoords[0].lng)]
            : [23.6345, -102.5528];
        this._enviaMap = Leaflet.map(mapEl, {
            zoomControl: true,
            scrollWheelZoom: false,
        }).setView(center, withCoords.length ? 12 : 5);
        Leaflet.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
            maxZoom: 19,
            attribution:
                'Leaflet | &copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
        }).addTo(this._enviaMap);

        const bounds = [];
        for (const option of withCoords) {
            const lat = Number(option.lat);
            const lng = Number(option.lng);
            const marker = Leaflet.marker([lat, lng]);
            marker.optionId = option.id;
            marker.bindPopup(
                `<strong>${option.name || ""}</strong><br/>${option.address || ""}`,
                {
                    autoPan: true,
                    // Extra top padding keeps the popup fully inside the map (not over the rates list).
                    autoPanPaddingTopLeft: [16, 72],
                    autoPanPaddingBottomRight: [16, 24],
                    maxWidth: 200,
                    keepInView: true,
                }
            );
            marker.on("click", () => {
                const radio = panel.querySelector(
                    `.o_envia_option_radio[value="${CSS.escape(option.id)}"]`
                );
                if (!radio) {
                    return;
                }
                if (!radio.checked) {
                    radio.checked = true;
                    radio.dispatchEvent(new Event("change", { bubbles: true }));
                } else {
                    this._highlightEnviaMarker(option.id);
                }
            });
            marker.addTo(this._enviaMap);
            this._enviaMarkers.push(marker);
            bounds.push([lat, lng]);
        }
        if (bounds.length > 1) {
            this._enviaMap.fitBounds(bounds, { padding: [28, 28] });
        } else if (bounds.length === 1) {
            this._enviaMap.setView(bounds[0], 14);
        }
        setTimeout(() => {
            if (loadSeq === this._enviaLoadSeq) {
                this._enviaMap?.invalidateSize();
            }
        }, 80);
    },

    _highlightEnviaMarker(optionId) {
        for (const marker of this._enviaMarkers) {
            if (marker.optionId !== optionId) {
                continue;
            }
            const map = this._enviaMap;
            if (!map) {
                marker.openPopup();
                break;
            }
            // Shift the pin downward in the viewport so the top popup fits inside the map.
            const point = map.project(marker.getLatLng());
            const target = map.unproject([point.x, point.y + 56]);
            map.once("moveend", () => marker.openPopup());
            map.panTo(target, { animate: true });
            break;
        }
        const panel = this.el.querySelector(".o_envia_delivery_panel:not(.d-none)");
        panel?.querySelectorAll(".o_envia_options_list .list-group-item").forEach((item) => {
            const selected = item.dataset.optionId === optionId;
            item.classList.toggle("o_envia_option_selected", selected);
            if (selected) {
                // Keep the selected rate visible inside the scrollable list.
                item.scrollIntoView({ block: "nearest", behavior: "smooth" });
            }
        });
    },
});
