/** @odoo-module **/

import {
    Component,
    onMounted,
    onPatched,
    onWillUnmount,
    useEffect,
    useRef,
    useState,
} from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { standardFieldProps } from "@web/views/fields/standard_field_props";
import { loadCSS, loadJS } from "@web/core/assets";
import { _t } from "@web/core/l10n/translation";

const LEAFLET_JS = "https://unpkg.com/leaflet@1.9.4/dist/leaflet.js";
const LEAFLET_CSS = "https://unpkg.com/leaflet@1.9.4/dist/leaflet.css";

const SELECT_ACTIONS = {
    origin: {
        "envia.quote.wizard": "action_select_origin_branch_code",
        "choose.delivery.carrier": "action_envia_select_origin_branch_code",
    },
    destination: {
        "envia.quote.wizard": "action_select_destination_branch_code",
        "choose.delivery.carrier": "action_envia_select_destination_branch_code",
    },
};

function pointsSignature(points) {
    return points
        .map((p) => `${p.code}:${p.latitude}:${p.longitude}`)
        .sort()
        .join("|");
}

function uniqueSorted(values) {
    return [...new Set(values.filter(Boolean))].sort((a, b) =>
        String(a).localeCompare(String(b), undefined, { sensitivity: "base" })
    );
}

function asCoord(value) {
    const number = typeof value === "number" ? value : Number(value);
    return Number.isFinite(number) ? number : null;
}

export class EnviaBranchMap extends Component {
    static template = "envia.EnviaBranchMap";
    static props = { ...standardFieldProps };

    setup() {
        this.orm = useService("orm");
        this.mapRef = useRef("map");
        this.mapDestRef = useRef("mapDest");
        this.state = useState({
            points: this._readPoints(),
            selecting: false,
            filterState: "",
            filterLocality: "",
            filterCode: "",
        });
        this._map = null;
        this._markers = {};
        this._pointsSig = "";
        this._leafletReady = null;
        this._resizeObserver = null;
        this._syncToken = 0;
        onMounted(() => {
            this._installResizeObserver();
            this._syncMap();
        });
        onPatched(() => {
            // Section often becomes visible after the first rate select.
            if (this.showMap) {
                if (this._map) {
                    this._invalidateSoon(this._map);
                } else {
                    this._syncMap();
                }
            }
        });
        onWillUnmount(() => {
            this._resizeObserver?.disconnect();
            this._resizeObserver = null;
            this._destroyMap();
        });
        useEffect(
            () => {
                this.state.points = this._readPoints();
                this._syncMap();
            },
            () => [this.props.record.data[this.props.name]]
        );
        useEffect(
            () => {
                this._syncMap();
            },
            () => [
                this.state.filterState,
                this.state.filterLocality,
                this.state.filterCode,
            ]
        );
    }

    get side() {
        return this.props.options?.side === "destination" ? "destination" : "origin";
    }

    get points() {
        return this.state.points;
    }

    get stateOptions() {
        return uniqueSorted(this.points.map((point) => point.state));
    }

    get localityOptions() {
        const rows = this.state.filterState
            ? this.points.filter((point) => point.state === this.state.filterState)
            : this.points;
        return uniqueSorted(rows.map((point) => point.locality || point.city));
    }

    get filteredPoints() {
        return this.points.filter((point) => {
            if (this.state.filterState && point.state !== this.state.filterState) {
                return false;
            }
            if (this.state.filterLocality) {
                const locality = point.locality || point.city || "";
                if (locality !== this.state.filterLocality) {
                    return false;
                }
            }
            if (this.state.filterCode && point.code !== this.state.filterCode) {
                return false;
            }
            return true;
        });
    }

    get hasActiveFilters() {
        return Boolean(
            this.state.filterState || this.state.filterLocality || this.state.filterCode
        );
    }

    get selectedLabel() {
        const selected = this.points.find((point) => point.selected);
        return selected?.label || "";
    }

    get mappable() {
        const source = this.side === "origin" ? this.filteredPoints : this.points;
        return source
            .map((point) => {
                const latitude = asCoord(point.latitude);
                const longitude = asCoord(point.longitude);
                if (
                    !point.code ||
                    latitude === null ||
                    longitude === null ||
                    (latitude === 0 && longitude === 0)
                ) {
                    return null;
                }
                return { ...point, latitude, longitude };
            })
            .filter(Boolean);
    }

    get showMap() {
        return this.mappable.length > 0;
    }

    get showEmpty() {
        return !this.state.selecting && this.points.length === 0;
    }

    get showSelectOnlyHint() {
        return !this.state.selecting && this.points.length > 0 && !this.mappable.length;
    }

    get placeholder() {
        return this.side === "destination"
            ? _t("Select a destination branch")
            : _t("Select an origin branch");
    }

    get emptyMessage() {
        return this.side === "destination"
            ? _t("No destination pickup points available.")
            : _t("No origin pickup points available.");
    }

    get selectOnlyMessage() {
        return _t("Map unavailable for these locations; choose from the list.");
    }

    _readPoints() {
        const raw = this.props.record.data[this.props.name];
        if (!raw) {
            return [];
        }
        try {
            const parsed = JSON.parse(raw);
            return Array.isArray(parsed) ? parsed : [];
        } catch {
            return [];
        }
    }

    _ensureLeaflet() {
        if (!this._leafletReady) {
            this._leafletReady = Promise.all([loadJS(LEAFLET_JS), loadCSS(LEAFLET_CSS)]);
        }
        return this._leafletReady;
    }

    _installResizeObserver() {
        const el = this._mapElement();
        if (!el || typeof ResizeObserver === "undefined") {
            return;
        }
        this._resizeObserver?.disconnect();
        this._resizeObserver = new ResizeObserver(() => {
            if (this._map) {
                this._map.invalidateSize();
            } else if (this.showMap) {
                this._syncMap();
            }
        });
        this._resizeObserver.observe(el);
    }

    _destroyMap() {
        if (this._map) {
            this._map.remove();
            this._map = null;
            this._markers = {};
            this._pointsSig = "";
        }
    }

    _invalidateSoon(map) {
        for (const ms of [0, 50, 150, 300, 600]) {
            setTimeout(() => {
                if (this._map === map) {
                    map.invalidateSize();
                }
            }, ms);
        }
    }

    _mapElement() {
        return this.side === "origin" ? this.mapRef.el : this.mapDestRef.el;
    }

    async _syncMap() {
        const token = ++this._syncToken;
        const points = this.mappable;
        const el = this._mapElement();
        if (!points.length) {
            this._destroyMap();
            return;
        }
        if (!el) {
            // Origin layout mounts the canvas on the next patch.
            setTimeout(() => {
                if (token === this._syncToken) {
                    this._syncMap();
                }
            }, 50);
            return;
        }
        try {
            await this._ensureLeaflet();
        } catch {
            this._destroyMap();
            return;
        }
        if (token !== this._syncToken || !window.L || !this._mapElement()) {
            return;
        }
        this._installResizeObserver();
        const sig = pointsSignature(points);
        if (this._map && this._pointsSig === sig) {
            this._highlightSelected(points);
            this._invalidateSoon(this._map);
            return;
        }
        this._destroyMap();
        // Guard against Leaflet init on a zero-size box in the modal.
        if (el.clientWidth < 40 || el.clientHeight < 40) {
            setTimeout(() => {
                if (token === this._syncToken) {
                    this._syncMap();
                }
            }, 100);
            return;
        }
        const map = window.L.map(el, { scrollWheelZoom: false });
        window.L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
            attribution: "&copy; OpenStreetMap",
        }).addTo(map);
        const bounds = [];
        for (const point of points) {
            const marker = window.L.marker([point.latitude, point.longitude]).addTo(map);
            marker.bindPopup(point.label || point.code);
            marker.on("click", () => this._select(point.code));
            this._markers[point.code] = marker;
            bounds.push([point.latitude, point.longitude]);
        }
        this._map = map;
        this._pointsSig = sig;
        this._highlightSelected(points, bounds);
        this._invalidateSoon(map);
    }

    _highlightSelected(points, bounds = null) {
        const selected = points.find((point) => point.selected);
        if (selected && this._markers[selected.code] && this._map) {
            this._map.setView([selected.latitude, selected.longitude], 16);
            this._markers[selected.code].openPopup();
            return;
        }
        if (bounds?.length && this._map) {
            this._map.fitBounds(bounds, { padding: [24, 24] });
        }
    }

    onClearFilters() {
        this.state.filterState = "";
        this.state.filterLocality = "";
        this.state.filterCode = "";
    }

    onStateFilter(ev) {
        this.state.filterState = ev.target.value || "";
        this.state.filterLocality = "";
        this.state.filterCode = "";
    }

    onLocalityFilter(ev) {
        this.state.filterLocality = ev.target.value || "";
        this.state.filterCode = "";
    }

    async onBranchFilter(ev) {
        const code = ev.target.value || "";
        this.state.filterCode = code;
        if (code) {
            await this._select(code);
        }
    }

    async onSelect(ev) {
        const code = ev.target.value || "";
        if (code) {
            this.state.filterCode = code;
        }
        await this._select(code);
    }

    async _select(code) {
        if (!code || !this.props.record.resId || this.state.selecting) {
            return;
        }
        const model = this.props.record.resModel;
        const action = SELECT_ACTIONS[this.side]?.[model];
        if (!action) {
            return;
        }
        this.state.selecting = true;
        try {
            await this.orm.call(model, action, [[this.props.record.resId], code]);
            await this.props.record.load();
            this.state.points = this._readPoints();
            await this._syncMap();
        } finally {
            this.state.selecting = false;
        }
    }
}

registry.category("fields").add("envia_branch_map", {
    component: EnviaBranchMap,
});
