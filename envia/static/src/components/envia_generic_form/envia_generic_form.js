/** @odoo-module **/

import { Component, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { _t } from "@web/core/l10n/translation";
import { Dialog } from "@web/core/dialog/dialog";
import { SelectMenu } from "@web/core/select_menu/select_menu";

const SIZE_CLASS = {
    S: "o_envia_gf_size_s",
    M: "o_envia_gf_size_m",
    L: "o_envia_gf_size_l",
    XL: "o_envia_gf_size_xl",
};

const MODEL = "envia.billing.info.wizard";

function fieldKey(field) {
    return (field.fieldName || field.fieldId || "").trim();
}

function normalizeOnChange(raw) {
    if (Array.isArray(raw)) {
        return raw.filter((item) => item && typeof item === "object");
    }
    if (raw && typeof raw === "object") {
        return [raw];
    }
    return [];
}

function conditionMatches(condition, value) {
    const raw = (condition || "").trim();
    if (!raw) {
        return true;
    }
    if (raw.startsWith("/") && raw.lastIndexOf("/") > 0) {
        const last = raw.lastIndexOf("/");
        try {
            return new RegExp(raw.slice(1, last), raw.slice(last + 1)).test(value || "");
        } catch {
            return false;
        }
    }
    return (value || "") === raw;
}

function replaceTokens(template, values, context = {}) {
    let url = String(template || "");
    const bag = { ...context, ...values };
    url = url.replace(/\{\{values\.([^}]+)\}\}/g, (_, key) => bag[key] ?? "");
    url = url.replace(/\{\{\$([^}]+)\}\}/g, (_, key) => bag[key] ?? "");
    url = url.replace(/\$([a-zA-Z_]+)/g, (_, key) => bag[key] ?? "");
    return url;
}

function iconClass(fieldIcon) {
    const raw = (fieldIcon || "").trim();
    if (!raw) {
        return "";
    }
    return raw.replace(/\bfas\b/g, "fa").replace(/\bfar\b/g, "fa");
}

/** Map Envia generic-form English labels to the active UI language. */
function translateApiLabel(field) {
    const key = fieldKey(field).toLowerCase();
    const byKey = {
        street: _t("Address line 1"),
        address1: _t("Address line 1"),
        address2: _t("Address line 2"),
        number: _t("Exterior number"),
        postal_code: _t("Zip Code"),
        postalcode: _t("Zip Code"),
        district: _t("Neighborhood"),
        neighborhood: _t("Neighborhood"),
        city: _t("City"),
        city_select: _t("City"),
        state: _t("State"),
        state_code: _t("State"),
        identification_number: _t("Identification Number"),
        reference: _t("Reference"),
        alias: _t("Alias"),
    };
    if (byKey[key]) {
        return byKey[key];
    }
    const label = String(field.fieldLabel || "").trim();
    const byLabel = {
        Address1: _t("Address line 1"),
        Address2: _t("Address line 2"),
        "Zip Code": _t("Zip Code"),
        Neighborhood: _t("Neighborhood"),
        City: _t("City"),
        State: _t("State"),
        "Identification Number": _t("Identification Number"),
        Reference: _t("Reference"),
        Alias: _t("Alias"),
        Number: _t("Exterior number"),
    };
    return byLabel[label] || field.fieldLabel || field.fieldName || key;
}

/**
 * Dynamic Envia address/billing form driven by GET /generic-form.
 *
 * - Field defs (incl. ``state`` / ``city_select``) come from generic-form.
 * - State *options* come from GET /state?country_code=XX (not inline in the schema).
 * - City options for CO come from the hidden ``city.geocode`` URL after state change.
 */
export class EnviaGenericFormDialog extends Component {
    static template = "envia.GenericFormDialog";
    static components = { Dialog, SelectMenu };
    static props = {
        close: Function,
        countryCode: { type: String, optional: true },
        formType: { type: String, optional: true },
        phoneCode: { type: String, optional: true },
        queriesBase: { type: String, optional: true },
        initialValues: { type: Object, optional: true },
        warehouseId: { type: Number, optional: true },
    };

    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.action = useService("action");
        this._initialApplied = false;

        this.state = useState({
            countryCode: (this.props.countryCode || "MX").toUpperCase(),
            formType: this.props.formType || "address_info",
            phoneCode: this.props.phoneCode || "+52",
            queriesBase: (this.props.queriesBase || "https://queries.test.envia.com").replace(
                /\/?$/,
                ""
            ),
            loading: true,
            saving: false,
            error: "",
            schema: [],
            values: {},
            options: {},
            errors: {},
            context: {},
            identity: {
                name: "",
                company: "",
                email: "",
                phone: "",
            },
            countries: [],
            warehouses: [],
            warehouseId: this.props.warehouseId || false,
            busyField: "",
        });

        onWillStart(async () => {
            this.state.countries = await this.orm.searchRead(
                "res.country",
                [["code", "!=", false]],
                ["code", "name", "phone_code"],
                { order: "name" }
            );
            this.state.warehouses = await this.orm.call(MODEL, "get_origin_warehouse_options", []);
            if (!this.state.warehouseId && this.state.warehouses.length) {
                this.state.warehouseId = Number(this.state.warehouses[0].id);
            } else if (this.state.warehouseId) {
                this.state.warehouseId = Number(this.state.warehouseId);
            }
            await this.loadSchema();
        });
    }

    get visibleFields() {
        return this.state.schema.filter((field) => field.visible !== false);
    }

    get selectedWarehouse() {
        const id = this.state.warehouseId;
        if (id === false || id === null || id === undefined || id === "") {
            return null;
        }
        const numericId = Number(id);
        return (
            this.state.warehouses.find((entry) => Number(entry.id) === numericId) || null
        );
    }

    get warehouseChoices() {
        return this.state.warehouses.map((entry) => ({
            value: Number(entry.id),
            label: entry.name,
        }));
    }

    get ui() {
        return {
            warehouse: _t("Warehouse"),
            searchWarehouse: _t("Search warehouse…"),
            warehouseAddress: _t("Warehouse contact address"),
            noWarehouseAddress: _t("No address on warehouse contact"),
            fullName: _t("Full name"),
            company: _t("Company"),
            email: _t("Email"),
            phone: _t("Phone"),
            country: _t("Country"),
            searchCountry: _t("Search country…"),
            search: _t("Search…"),
            select: _t("Select…"),
            loading: _t("Loading Envia form…"),
            saving: _t("Saving…"),
            save: _t("Save"),
            cancel: _t("Cancel"),
        };
    }

    get countryChoices() {
        return this.state.countries.map((country) => ({
            value: country.code,
            label: `${country.name} (${country.code})`,
        }));
    }

    sizeClass(field) {
        return SIZE_CLASS[field.size] || SIZE_CLASS.L;
    }

    faClass(field) {
        return iconClass(field.fieldIcon);
    }

    fieldLabelText(field) {
        return translateApiLabel(field);
    }

    fieldPlaceholderText(field) {
        const ph = String(field.fieldPlaceholder || "").trim();
        if (!ph) {
            return this.fieldLabelText(field);
        }
        return translateApiLabel({ ...field, fieldLabel: ph });
    }

    selectPlaceholder(field) {
        const ph = String(field.fieldPlaceholder || "").trim();
        return ph ? this.fieldPlaceholderText(field) : this.ui.select;
    }

    inputType(field) {
        const type = (field.fieldType || "text").toLowerCase();
        if (type === "email" || type === "number") {
            return type;
        }
        return "text";
    }

    isSelect(field) {
        return (field.fieldType || "").toLowerCase() === "select";
    }

    fieldChoices(field) {
        const key = fieldKey(field);
        return (this.state.options[key] || []).map((opt) => ({
            value: opt.key,
            label: opt.name || opt.key,
        }));
    }

    fieldValue(field) {
        return this.state.values[fieldKey(field)] || "";
    }

    fieldRequired(field) {
        return Boolean(field.rules?.required);
    }

    _mapStateOptions(states) {
        const seen = new Set();
        const mapped = [];
        for (const entry of states || []) {
            if (!entry || typeof entry !== "object") {
                continue;
            }
            const key = String(
                entry.code_2_digits ||
                    entry.code_3_digits ||
                    entry.code ||
                    entry.code_shopify ||
                    ""
            ).trim();
            if (!key || seen.has(key)) {
                continue;
            }
            seen.add(key);
            mapped.push({
                key,
                name: String(entry.name || key).trim(),
            });
        }
        return mapped;
    }

    async loadSchema() {
        this.state.loading = true;
        this.state.error = "";
        this.state.errors = {};
        this.state.context = {};
        this.state.options = {};
        try {
            const schema = await this.orm.call(MODEL, "fetch_generic_form", [
                this.state.countryCode,
                this.state.formType,
            ]);
            this.state.schema = Array.isArray(schema) ? schema : [];
            const values = { country: this.state.countryCode };
            const options = {};
            for (const field of this.state.schema) {
                const key = fieldKey(field);
                if (!key) {
                    continue;
                }
                values[key] = "";
                const defaults = field.rules?.defaultValues;
                if (Array.isArray(defaults) && defaults.length) {
                    options[key] = defaults
                        .map((entry) => ({
                            key: String(entry.key ?? entry.code ?? entry.value ?? "").trim(),
                            name: String(entry.value ?? entry.name ?? entry.key ?? "").trim(),
                        }))
                        .filter((entry) => entry.key || entry.name);
                }
            }
            const stateField = this.state.schema.find(
                (field) =>
                    this.isSelect(field) &&
                    ["state", "state_code"].includes(fieldKey(field))
            );
            if (stateField) {
                const stateKey = fieldKey(stateField);
                const states = await this.orm.call(MODEL, "fetch_states", [
                    this.state.countryCode,
                ]);
                options[stateKey] = this._mapStateOptions(states);
            }
            this.state.values = values;
            this.state.options = options;
            if (!this._initialApplied) {
                // Empty ``{}`` is truthy — treat as missing so the
                // preselected warehouse still seeds identity/address.
                if (this._hasInitialValues()) {
                    await this.applyInitialValues();
                } else {
                    await this.fillFromSelectedWarehouse();
                }
                this._initialApplied = true;
            }
        } catch (error) {
            this.state.schema = [];
            this.state.options = {};
            this.state.error =
                error?.data?.message || error?.message || _t("Could not load Envia form.");
        } finally {
            this.state.loading = false;
        }
    }

    _hasInitialValues() {
        const initial = this.props.initialValues;
        return Boolean(initial && typeof initial === "object" && Object.keys(initial).length);
    }

    async applyInitialValues() {
        if (!this._hasInitialValues()) {
            return;
        }
        await this.applyWarehouseDefaults(this.props.initialValues);
    }

    async applyWarehouseDefaults(defaults) {
        if (!defaults || typeof defaults !== "object") {
            return;
        }
        if (defaults.phone_code) {
            this.state.phoneCode = String(defaults.phone_code);
        }
        // Always replace identity/address from warehouse contact (empty clears stale values).
        if (defaults.identity && typeof defaults.identity === "object") {
            this.state.identity = {
                name: String(defaults.identity.name || ""),
                company: String(defaults.identity.company || ""),
                email: String(defaults.identity.email || ""),
                phone: String(defaults.identity.phone || ""),
            };
        }
        const seed = defaults.values;
        if (seed && typeof seed === "object") {
            const next = { ...this.state.values };
            for (const key of Object.keys(next)) {
                if (key === "country") {
                    continue;
                }
                if (Object.prototype.hasOwnProperty.call(seed, key)) {
                    next[key] = String(seed[key] ?? "");
                }
            }
            for (const [key, raw] of Object.entries(seed)) {
                next[key] = String(raw ?? "");
            }
            if (defaults.country_code) {
                next.country = String(defaults.country_code).toUpperCase();
            }
            this.state.values = next;
        }
        this.state.errors = {};
        const zip = String(this.state.values.postal_code || "").trim();
        if (!zip) {
            return;
        }
        const zipField = this.state.schema.find((field) => fieldKey(field) === "postal_code");
        if (!zipField) {
            return;
        }
        // Seed before zip on_change; geocode may clear city/state or leave {{tokens}}.
        const prior = {
            city: String(this.state.values.city || "").trim(),
            state: String(this.state.values.state || "").trim(),
            district: String(this.state.values.district || "").trim(),
        };
        await this.runOnChange(zipField, zip);
        const patch = {};
        for (const [key, before] of Object.entries(prior)) {
            if (!before) {
                continue;
            }
            const current = String(this.state.values[key] || "").trim();
            if (!current || current.includes("{{")) {
                patch[key] = before;
            }
        }
        if (Object.keys(patch).length) {
            this.state.values = { ...this.state.values, ...patch };
        }
    }

    async fillFromSelectedWarehouse() {
        const selected = this.selectedWarehouse;
        if (!selected?.defaults) {
            return;
        }
        const code = String(selected.defaults.country_code || "").toUpperCase();
        if (code && code !== this.state.countryCode) {
            this.state.countryCode = code;
            // Avoid re-entering initial fill while reloading schema for the new country.
            const alreadyApplied = this._initialApplied;
            this._initialApplied = true;
            await this.loadSchema();
            this._initialApplied = alreadyApplied;
        }
        await this.applyWarehouseDefaults(selected.defaults);
    }

    async onWarehouseSelect(value) {
        this.state.warehouseId = value ? Number(value) : false;
        this.state.errors = { ...this.state.errors, _warehouse: undefined };
        await this.fillFromSelectedWarehouse();
    }

    async onCountrySelect(value) {
        const code = String(value || "").toUpperCase();
        this.state.countryCode = code;
        const country = this.state.countries.find((item) => item.code === code);
        if (country?.phone_code) {
            this.state.phoneCode = `+${country.phone_code}`;
        }
        await this.loadSchema();
    }

    onIdentityInput(key, ev) {
        this.state.identity[key] = ev.target.value;
    }

    async onSelectField(field, value) {
        const key = fieldKey(field);
        const nextValue = value == null ? "" : String(value);
        this.state.values = { ...this.state.values, [key]: nextValue };
        if (this.state.errors[key]) {
            const next = { ...this.state.errors };
            delete next[key];
            this.state.errors = next;
        }
        await this.runOnChange(field, nextValue);
    }

    async onFieldInput(field, ev) {
        const key = fieldKey(field);
        const value = ev.target.value;
        this.state.values = { ...this.state.values, [key]: value };
        if (this.state.errors[key]) {
            const next = { ...this.state.errors };
            delete next[key];
            this.state.errors = next;
        }
        await this.runOnChange(field, value);
    }

    async runOnChange(field, value) {
        const actions = normalizeOnChange(field.on_change);
        if (!actions.length) {
            return;
        }
        this.state.busyField = fieldKey(field);
        try {
            for (const meta of actions) {
                if (!conditionMatches(meta.condition, value)) {
                    continue;
                }
                await this.applyAction(field, meta, value);
            }
        } finally {
            this.state.busyField = "";
        }
    }

    async applyAction(field, meta, value) {
        const action = (meta.action || "").trim();
        const values = this.state.values;

        for (const name of meta.clear_fields || []) {
            this.state.values = { ...this.state.values, [name]: "" };
            this.state.options = { ...this.state.options, [name]: [] };
        }

        const setOptions = meta.set_options;
        if (typeof setOptions === "string" && setOptions.trim()) {
            await this.fillOptionsFromGeocode(setOptions.trim(), value || values.state);
            return;
        }
        if (setOptions && typeof setOptions === "object" && !action) {
            for (const target of Object.keys(setOptions)) {
                await this.fillOptionsFromGeocode(target, value || values.state);
            }
            return;
        }

        if (action === "request" || (!action && meta.url)) {
            await this.applyRequest(meta, values);
            return;
        }
        if (action === "set_temporal_context") {
            this.applyTemporalContext(meta);
            return;
        }
        if (action === "set_fields" || (!action && meta.set_fields)) {
            this.applySetFields(meta, value);
            return;
        }
        if (action === "set_options") {
            this.applySetOptions(meta);
        }
    }

    async applyRequest(meta, values) {
        let url = replaceTokens(meta.url || "", {
            ...values,
            country: this.state.countryCode,
            state: values.state,
            postal_code: values.postal_code || values.postalCode,
        });
        url = url.replace(
            /https?:\/\/queries(?:-test)?\.envia\.com/gi,
            this.state.queriesBase
        );
        if (!url || url.includes("{{") || /\$[a-zA-Z_]/.test(url)) {
            return;
        }
        const name = (meta.name || "response").trim();
        const body = await this.orm.call(MODEL, "fetch_public_json", [url]);
        // Parity with wizard ``_apply_request_action``: zipcode returns a list.
        let payload = body;
        const dataPath = String(meta.data_path || "").trim();
        if (Array.isArray(body) && body.length) {
            if (dataPath === "[0]" || (!dataPath && name === "geocodes")) {
                payload = body[0];
            }
        }
        this.state.context = { ...this.state.context, [name]: payload };
    }

    applyTemporalContext(meta) {
        const specs = meta.values || {};
        const next = { ...this.state.context };
        for (const [key, spec] of Object.entries(specs)) {
            const paths = Array.isArray(spec?.path) ? spec.path : [spec?.path].filter(Boolean);
            for (const path of paths) {
                const resolved = this.pathGet(
                    path.startsWith("geocodes.") ? this.state.context.geocodes : this.state.context,
                    path.startsWith("geocodes.") ? path.slice(9) : path
                );
                if (resolved !== undefined && resolved !== null && resolved !== "") {
                    next[key] = resolved;
                    break;
                }
            }
        }
        this.state.context = next;
    }

    applySetFields(meta, selectedValue) {
        const map = meta.fields || meta.set_fields || {};
        const next = { ...this.state.values };
        const cityOpts = this.state.options.city_select || this.state.options.city || [];
        const selectedName =
            cityOpts.find((opt) => opt.key === selectedValue)?.name || selectedValue;
        const ctx = this.state.context || {};
        for (const [target, template] of Object.entries(map)) {
            let value = String(template ?? "");
            value = value
                .replaceAll("{{$city}}", selectedValue || "")
                .replaceAll("$city", selectedValue || "")
                .replaceAll("{{$city_name}}", selectedName || "")
                .replaceAll("{{$state}}", this.state.values.state || "")
                .replaceAll("$state", this.state.values.state || "")
                .replaceAll("{{state_code}}", String(ctx.state_code || ""));
            // Resolve {{geocodes.*}} and remaining {{contextKey}} (Python parity).
            value = value.replace(/\{\{([^}]+)\}\}/g, (match, rawPath) => {
                const path = String(rawPath).trim();
                let resolved;
                if (path.startsWith("geocodes.")) {
                    resolved = this.pathGet(ctx.geocodes, path.slice(9));
                } else if (Object.prototype.hasOwnProperty.call(ctx, path)) {
                    resolved = ctx[path];
                } else {
                    return match;
                }
                return resolved == null ? "" : String(resolved);
            });
            next[target] = value;
        }
        this.state.values = next;
    }

    applySetOptions(meta) {
        const fieldsMap = meta.fields || {};
        const next = { ...this.state.options };
        for (const [target, source] of Object.entries(fieldsMap)) {
            let data;
            if (String(source).startsWith("geocodes.")) {
                data = this.pathGet(this.state.context.geocodes, String(source).slice(9));
            } else {
                data = this.state.context[source];
            }
            if (!Array.isArray(data)) {
                continue;
            }
            next[target] = data
                .map((entry) => {
                    if (entry && typeof entry === "object") {
                        return {
                            key: String(entry.code ?? entry.key ?? entry.name ?? "").trim(),
                            name: String(entry.name ?? entry.value ?? entry.key ?? "").trim(),
                        };
                    }
                    return { key: String(entry), name: String(entry) };
                })
                .filter((entry) => entry.key || entry.name);
        }
        this.state.options = next;
    }

    async fillOptionsFromGeocode(targetName, stateCode) {
        const cityField =
            this.state.schema.find((field) => fieldKey(field) === "city") ||
            this.state.schema.find((field) => fieldKey(field) === targetName);
        const template = (cityField?.geocode || "").trim();
        if (!template || !stateCode) {
            return;
        }
        let url = replaceTokens(template, {
            ...this.state.values,
            state: stateCode,
            country: this.state.countryCode,
        });
        url = url.replace(
            /https?:\/\/queries(?:-test)?\.envia\.com/gi,
            this.state.queriesBase
        );
        if (!url || /\$[a-zA-Z_]/.test(url)) {
            return;
        }
        const body = await this.orm.call(MODEL, "fetch_public_json", [url]);
        const list = Array.isArray(body) ? body : body?.data;
        if (!Array.isArray(list)) {
            return;
        }
        const seen = new Set();
        const unique = [];
        for (const entry of list) {
            const key = String(entry.code ?? entry.key ?? entry.name ?? "").trim();
            if (!key || seen.has(key)) {
                continue;
            }
            seen.add(key);
            unique.push({
                key,
                name: String(entry.name ?? entry.code ?? key).trim(),
            });
        }
        const paintTarget =
            targetName === "city" && this.state.schema.some((f) => fieldKey(f) === "city_select")
                ? "city_select"
                : targetName;
        this.state.options = {
            ...this.state.options,
            [paintTarget]: unique,
            city: unique,
        };
        this.state.values = { ...this.state.values, [paintTarget]: "", city: "" };
    }

    pathGet(root, path) {
        let current = root;
        for (const part of String(path || "").replaceAll("]", "").split(".")) {
            if (current == null) {
                return undefined;
            }
            if (part.includes("[")) {
                const [name, index] = part.split("[");
                if (name) {
                    current = current[name];
                }
                current = Array.isArray(current) ? current[Number(index)] : undefined;
            } else {
                current = current?.[part];
            }
        }
        return current;
    }

    validate() {
        const errors = {};
        const { identity } = this.state;
        if (!this.state.warehouseId) {
            errors._warehouse = _t("Warehouse is required.");
        }
        if (!identity.name.trim()) {
            errors._name = _t("Full name is required.");
        }
        if (!identity.email.trim()) {
            errors._email = _t("Email is required.");
        }
        if (!identity.phone.trim()) {
            errors._phone = _t("Phone is required.");
        }
        for (const field of this.visibleFields) {
            const key = fieldKey(field);
            const rules = field.rules || {};
            const value = String(this.state.values[key] ?? "").trim();
            if (rules.required && !value) {
                errors[key] = `${translateApiLabel(field)}: ${_t("required")}`;
                continue;
            }
            if (!value) {
                continue;
            }
            if (rules.min && value.length < Number(rules.min)) {
                errors[key] = `${_t("Minimum length")}: ${rules.min}`;
            }
            if (rules.max && value.length > Number(rules.max)) {
                errors[key] = `${_t("Maximum length")}: ${rules.max}`;
            }
            if (
                (field.fieldType || "").toLowerCase() === "email" &&
                !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value)
            ) {
                errors[key] = _t("Enter a valid email.");
            }
        }
        this.state.errors = errors;
        return Object.keys(errors).length === 0;
    }

    buildPayload() {
        const { identity, countryCode, values, options } = this.state;
        const name = identity.name.trim();
        const companyRaw = (identity.company || "").trim();
        // Envia /user-address: company min length 2; fall back to full name.
        const company = companyRaw.length >= 2 ? companyRaw : name;
        const payload = {
            name,
            company,
            email: identity.email.trim(),
            phone: identity.phone.trim(),
            phone_code: countryCode,
            country: countryCode,
        };
        for (const [key, raw] of Object.entries(values)) {
            if (key === "city_select" || key === "country" || key === "company") {
                continue;
            }
            const value = String(raw ?? "").trim();
            if (value) {
                payload[key] = value;
            }
        }
        if (!payload.city) {
            const selected = String(values.city_select || "").trim();
            const opt = (options.city_select || options.city || []).find(
                (entry) => entry.key === selected
            );
            if (opt?.name) {
                payload.city = opt.name;
            } else if (selected) {
                payload.city = selected;
            }
        }
        return payload;
    }

    async onValidate() {
        if (!this.validate()) {
            this.notification.add(_t("Please fix the highlighted fields."), { type: "danger" });
            return;
        }
        this.state.saving = true;
        try {
            const result = await this.orm.call(MODEL, "save_billing_address", [
                this.buildPayload(),
                this.state.warehouseId || false,
            ]);
            this.notification.add(
                result.reused
                    ? `${_t("This origin address already exists in Envia. It was linked to the shop.")} (${result.address_id})`
                    : `${_t("Origin address saved and linked to the shop.")} (${result.address_id})`,
                { type: "success" }
            );
            this.props.close();
            // Refresh Settings so Warehouse origins list picks up upsert_match.
            this.action.doAction({ type: "ir.actions.client", tag: "soft_reload" });
        } catch (error) {
            const message =
                error?.data?.message ||
                error?.message ||
                _t("Could not save the billing address.");
            this.notification.add(message, { type: "danger", sticky: true });
        } finally {
            this.state.saving = false;
        }
    }
}

function openEnviaGenericForm(env, action) {
    const params = action.params || {};
    env.services.dialog.add(EnviaGenericFormDialog, {
        countryCode: params.country_code || "MX",
        formType: params.form || "address_info",
        phoneCode: params.phone_code || "+52",
        queriesBase: params.queries_base_url || "https://queries.test.envia.com",
        initialValues: params.initial_values || undefined,
        warehouseId: params.warehouse_id || undefined,
    });
}

registry.category("actions").add("envia_generic_form", openEnviaGenericForm);
