from __future__ import annotations

import json
import re

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from ..services.envia_client import EnviaApiError, EnviaClient
from ..services.envia_geocodes_client import EnviaGeocodesClient

# Covered by the fixed identity block; not duplicated as dynamic lines.
_IDENTITY_FIELD_IDS = frozenset({"nombre", "lastname", "email", "phone"})
_STATE_CODE_KEYS = ("code_2_digits", "code_3_digits", "code_shopify", "code")
# Select fields promoted to the wizard form (list editable M2o domains break NewId options).
_PROMOTED_STATE_IDS = frozenset({"state"})
_PROMOTED_CITY_IDS = frozenset({"city_select"})


class EnviaBillingInfoWizard(models.TransientModel):
    _name = "envia.billing.info.wizard"
    _description = "Envia Billing Info Wizard"

    company_id = fields.Many2one(
        "res.company",
        required=True,
        default=lambda self: self.env.company,
    )
    country_id = fields.Many2one("res.country", string="Country", required=True)
    name = fields.Char(string="Full name")
    company_name = fields.Char(string="Company name")
    email = fields.Char(string="Email")
    phone_dial_code = fields.Char(string="Dial code", default="+52")
    phone = fields.Char(string="Phone")
    schema_warning = fields.Char(readonly=True)
    schema_json = fields.Text(readonly=True)
    show_state_field = fields.Boolean(readonly=True)
    show_city_field = fields.Boolean(readonly=True)
    state_label = fields.Char(readonly=True, default="State")
    city_label = fields.Char(readonly=True, default="City")
    state_required = fields.Boolean(readonly=True)
    city_required = fields.Boolean(readonly=True)
    option_ids = fields.One2many(
        "envia.billing.info.option",
        "wizard_id",
        string="Select options",
    )
    state_option_id = fields.Many2one(
        "envia.billing.info.option",
        string="State",
        # Options are flushed to DB on country/state onchange so name_search works.
        domain="[('wizard_id', '=', id), ('kind', '=', 'state')]",
    )
    city_option_id = fields.Many2one(
        "envia.billing.info.option",
        string="City",
        domain="[('wizard_id', '=', id), ('kind', '=', 'city')]",
    )
    line_ids = fields.One2many(
        "envia.billing.info.line",
        "wizard_id",
        string="Address fields",
    )

    def _persisted_wizard(self):
        """Real wizard row behind an onchange NewId (dialog opened with res_id)."""
        self.ensure_one()
        if self._origin:
            return self._origin
        if self.ids and all(isinstance(item, int) for item in self.ids):
            return self
        return self.browse()

    def _sync_promoted_options_to_db(
        self,
        *,
        keep_state_key: str = "",
        keep_city_key: str = "",
    ) -> None:
        """Persist option/line rows so promoted Many2one domains resolve via name_search."""
        self.ensure_one()
        wiz = self._persisted_wizard()
        if not wiz:
            return

        option_vals = [
            {"kind": opt.kind or False, "key": opt.key, "name": opt.name}
            for opt in self.option_ids
        ]
        line_vals_list = []
        for line in self.line_ids:
            line_vals = {
                "field_id": line.field_id,
                "data_name": line.data_name,
                "label": line.label,
                "placeholder": line.placeholder or False,
                "data_type": line.data_type,
                "required": line.required,
                "geocode_template": line.geocode_template or False,
                "on_change_json": line.on_change_json or False,
                "value": line.value or False,
            }
            if line.option_ids:
                line_vals["option_ids"] = [
                    (0, 0, {"key": opt.key, "name": opt.name})
                    for opt in line.option_ids
                ]
            line_vals_list.append(line_vals)

        Option = self.env["envia.billing.info.option"]
        Line = self.env["envia.billing.info.line"]
        Option.search([("wizard_id", "=", wiz.id)]).unlink()
        Line.search([("wizard_id", "=", wiz.id)]).unlink()

        created_options = Option
        if option_vals:
            created_options = Option.create(
                [{**vals, "wizard_id": wiz.id} for vals in option_vals]
            )

        created_lines = Line
        for line_vals in line_vals_list:
            created_lines |= Line.create({**line_vals, "wizard_id": wiz.id})

        state_option = created_options.filtered(
            lambda opt: opt.kind == "state" and opt.key == keep_state_key
        )[:1] if keep_state_key else Option
        city_option = created_options.filtered(
            lambda opt: opt.kind == "city" and opt.key == keep_city_key
        )[:1] if keep_city_key else Option

        wiz.write(
            {
                "schema_json": self.schema_json or False,
                "schema_warning": self.schema_warning or False,
                "show_state_field": bool(self.show_state_field),
                "show_city_field": bool(self.show_city_field),
                "state_required": bool(self.state_required),
                "city_required": bool(self.city_required),
                "state_label": self.state_label or "State",
                "city_label": self.city_label or "City",
                "state_option_id": state_option.id if state_option else False,
                "city_option_id": city_option.id if city_option else False,
            }
        )
        # Point the onchange cache at real rows (virtual ids break M2O name_search).
        self.option_ids = created_options
        self.line_ids = created_lines
        self.state_option_id = state_option if state_option else False
        self.city_option_id = city_option if city_option else False

    @api.onchange("state_option_id")
    def _onchange_state_option_id(self):
        state_key = self.state_option_id.key if self.state_option_id else ""
        geocode = self._geocode_template_for_target(
            "city_select"
        ) or self._geocode_template_for_target("city")
        option_commands = (
            self._load_provinces_options(state_key, geocode) if state_key else []
        )
        self._replace_city_options(option_commands)
        # Clear postal twin when schema asks (CO).
        for field_name in ("city", "city_select", "postal_code", "postalCode"):
            target = self._line_by_name(field_name)
            if target:
                self._clear_line_value(target)
        self._sync_promoted_options_to_db(keep_state_key=state_key)

    @api.onchange("city_option_id")
    def _onchange_city_option_id(self):
        if not self.city_option_id:
            return
        field_def = self._schema_field("city_select") or self._schema_field("city")
        raw = (field_def or {}).get("on_change") or []
        # Minimal stand-in so set_fields can read the selected city key/name.
        synthetic = type(
            "SyntheticLine",
            (),
            {
                "option_id": self.city_option_id,
                "value": self.city_option_id.key,
            },
        )()
        context: dict = {}
        for meta in self._normalize_on_change(raw):
            self._apply_on_change_action(synthetic, meta, context)

    def _queries_base_url(self) -> str:
        self.ensure_one()
        company = self.company_id or self.env.company
        return company._envia_get_queries_base_url()

    def _schema_fields(self) -> list[dict]:
        self.ensure_one()
        if not self.schema_json:
            return []
        try:
            data = json.loads(self.schema_json)
        except json.JSONDecodeError:
            return []
        return data if isinstance(data, list) else []

    def _schema_field(self, name: str) -> dict:
        name = (name or "").strip()
        for field_def in self._schema_fields():
            if not isinstance(field_def, dict):
                continue
            if (field_def.get("fieldId") or "").strip() == name:
                return field_def
            if (field_def.get("fieldName") or "").strip() == name:
                return field_def
        return {}

    @api.onchange("country_id")
    def _onchange_country_id(self):
        if self.country_id and self.country_id.phone_code:
            self.phone_dial_code = f"+{self.country_id.phone_code}"
        if self.country_id:
            self._load_form_lines()
            self._sync_promoted_options_to_db()
        else:
            self.line_ids = [(5, 0, 0)]
            self.option_ids = [(5, 0, 0)]
            self.state_option_id = False
            self.city_option_id = False
            self.show_state_field = False
            self.show_city_field = False

    @staticmethod
    def _state_option_vals(entry: dict) -> dict | None:
        key = ""
        for code_key in _STATE_CODE_KEYS:
            key = str(entry.get(code_key) or "").strip()
            if key:
                break
        if not key:
            return None
        return {"key": key, "name": str(entry.get("name") or key).strip()}

    @staticmethod
    def _province_option_vals(entry: dict) -> dict | None:
        key = str(entry.get("code") or entry.get("name") or "").strip()
        if not key:
            return None
        return {"key": key, "name": str(entry.get("name") or key).strip()}

    def _fetch_state_options(self, country_code: str, *, kind: str = "") -> list[tuple]:
        try:
            states = EnviaClient.get_states(self._queries_base_url(), country_code)
        except (EnviaApiError, UserError):
            return []
        commands = []
        for entry in states:
            if not isinstance(entry, dict):
                continue
            vals = self._state_option_vals(entry)
            if not vals:
                continue
            if kind:
                vals = {**vals, "kind": kind}
            commands.append((0, 0, vals))
        return commands

    def _is_promoted_state(self, field_id: str) -> bool:
        return (field_id or "").strip() in _PROMOTED_STATE_IDS

    def _is_promoted_city(self, field_id: str) -> bool:
        return (field_id or "").strip() in _PROMOTED_CITY_IDS

    def _line_by_name(self, name: str):
        self.ensure_one()
        name = (name or "").strip()
        return self.line_ids.filtered(
            lambda line: line.field_id == name or line.data_name == name
        )[:1]

    def _option_target_line(self, name: str):
        self.ensure_one()
        select_line = self._line_by_name(f"{name}_select")
        if select_line and select_line.data_type == "select":
            return select_line
        return self._line_by_name(name)

    def _set_promoted_select(self, field_name: str, value: str) -> bool:
        """Set wizard-level state/city from a geocode set_fields value."""
        value = (value or "").strip()
        if not value:
            return False
        if self.show_state_field and (
            self._is_promoted_state(field_name) or field_name == "state"
        ):
            option = self.option_ids.filtered(
                lambda opt, key=value: opt.kind == "state"
                and (opt.key == key or opt.name == key)
            )[:1]
            if not option:
                self.option_ids = [(0, 0, {"kind": "state", "key": value, "name": value})]
                option = self.option_ids.filtered(
                    lambda opt, key=value: opt.kind == "state" and opt.key == key
                )[:1]
            self.state_option_id = option
            return True
        if self.show_city_field and (
            self._is_promoted_city(field_name) or field_name in ("city", "city_select")
        ):
            option = self.option_ids.filtered(
                lambda opt, key=value: opt.kind == "city"
                and (opt.key == key or opt.name == key)
            )[:1]
            if not option:
                self.option_ids = [(0, 0, {"kind": "city", "key": value, "name": value})]
                option = self.option_ids.filtered(
                    lambda opt, key=value: opt.kind == "city" and opt.key == key
                )[:1]
            self.city_option_id = option
            return True
        return False

    def _replace_city_options(self, option_commands: list[tuple]) -> None:
        """Rebuild city options on the wizard without dropping state options."""
        self.ensure_one()
        state_key = self.state_option_id.key if self.state_option_id else ""
        commands = [(5, 0, 0)]
        for opt in self.option_ids.filtered(lambda item: item.kind == "state"):
            commands.append(
                (0, 0, {"kind": "state", "key": opt.key, "name": opt.name})
            )
        for _cmd, _xid, vals in option_commands:
            commands.append(
                (0, 0, {"kind": "city", "key": vals["key"], "name": vals["name"]})
            )
        self.city_option_id = False
        self.option_ids = commands
        if state_key:
            self.state_option_id = self.option_ids.filtered(
                lambda opt: opt.kind == "state" and opt.key == state_key
            )[:1]

    def _geocode_template_for_target(self, target_name: str) -> str:
        for candidate in (target_name, target_name.removesuffix("_select")):
            field_def = self._schema_field(candidate)
            template = (field_def.get("geocode") or "").strip()
            if template:
                return template
            line = self._line_by_name(candidate)
            if line and line.geocode_template:
                return line.geocode_template
        return ""

    def _resolve_geocode_url(self, template: str, *, state_code: str = "", zipcode: str = "") -> str:
        """Substitute placeholders; keep absolute non-envia hosts as-is."""
        self.ensure_one()
        url = (template or "").strip()
        if not url:
            return ""
        # Only remap official Envia queries hosts to the active sandbox/prod base.
        queries_base = self._queries_base_url().rstrip("/")
        url = re.sub(
            r"https?://queries(?:-test)?\.envia\.com",
            queries_base,
            url,
            flags=re.IGNORECASE,
        )
        country_code = (self.country_id.code or "").strip().upper()
        replacements = {
            "$state": state_code,
            "{{$state}}": state_code,
            "$country": country_code,
            "{{$country}}": country_code,
            "{{values.country}}": country_code,
            "$zipcode": zipcode,
            "{{$zipcode}}": zipcode,
            "{{values.postal_code}}": zipcode,
        }
        # Longer tokens first so ``{{$state}}`` wins over ``$state``.
        for token in sorted(replacements, key=len, reverse=True):
            url = url.replace(token, replacements[token])
        return url

    def _load_provinces_options(self, state_code: str, geocode_template: str) -> list[tuple]:
        """GET the schema ``geocode`` URL as-is (host + query string included)."""
        url = self._resolve_geocode_url(geocode_template, state_code=state_code)
        if not url or "$" in url:
            return []
        try:
            body = EnviaClient._public_get_json(url)
            if isinstance(body, list):
                provinces = body
            else:
                data = body.get("data") if isinstance(body, dict) else None
                provinces = data if isinstance(data, list) else []
        except (EnviaApiError, UserError):
            return []
        commands = []
        for entry in provinces:
            if not isinstance(entry, dict):
                continue
            vals = self._province_option_vals(entry)
            if vals:
                commands.append((0, 0, vals))
        return commands

    def _clear_line_value(self, line) -> None:
        line.value = False
        line.option_id = False
        if line.data_type == "select":
            line.option_ids = [(5, 0, 0)]

    def _normalize_on_change(self, raw) -> list[dict]:
        """Docs use an array; some country payloads still send a single object."""
        if isinstance(raw, list):
            return [item for item in raw if isinstance(item, dict)]
        if isinstance(raw, dict):
            # Legacy CO-style object without explicit action key.
            if raw.get("set_options") or raw.get("set_fields") or raw.get("clear_fields"):
                return [raw]
            if raw.get("action"):
                return [raw]
        return []

    def apply_line_on_change(self, line) -> None:
        self.ensure_one()
        if not line:
            return
        try:
            raw = json.loads(line.on_change_json or "null")
        except json.JSONDecodeError:
            return
        context: dict = {}
        for meta in self._normalize_on_change(raw):
            self._apply_on_change_action(line, meta, context)

    @staticmethod
    def _path_get(root, path: str):
        """Resolve dotted paths like ``state.code.2digit`` or ``suburbs[0]``."""
        current = root
        for part in path.replace("]", "").split("."):
            if current is None:
                return None
            if "[" in part:
                name, index = part.split("[", 1)
                if name:
                    if not isinstance(current, dict):
                        return None
                    current = current.get(name)
                try:
                    current = current[int(index)] if isinstance(current, list) else None
                except (TypeError, ValueError, IndexError):
                    return None
            else:
                if isinstance(current, dict):
                    current = current.get(part)
                else:
                    return None
        return current

    def _apply_on_change_action(self, line, meta: dict, context: dict) -> None:
        action = (meta.get("action") or "").strip()

        for field_name in meta.get("clear_fields") or []:
            target = self._line_by_name(field_name)
            if target:
                self._clear_line_value(target)

        # Legacy CO-style: set_options = "city" (loads provinces URL from schema geocode).
        set_options = meta.get("set_options")
        if isinstance(set_options, str) and set_options.strip() and line.option_id:
            self._fill_options_from_geocode(set_options.strip(), line.option_id.key)

        if isinstance(set_options, dict) and line.option_id:
            for target_name in set_options:
                self._fill_options_from_geocode(str(target_name), line.option_id.key)

        if action == "request":
            self._apply_request_action(line, meta, context)
            return

        if action == "set_temporal_context":
            values = meta.get("values") or {}
            if not isinstance(values, dict):
                return
            for key, spec in values.items():
                if not isinstance(spec, dict):
                    continue
                paths = spec.get("path") or []
                if isinstance(paths, str):
                    paths = [paths]
                for path in paths:
                    path = str(path or "")
                    if path.startswith("geocodes."):
                        value = self._path_get(context.get("geocodes"), path[9:])
                    else:
                        value = self._path_get(context, path)
                    if value not in (None, False, ""):
                        context[key] = value
                        break
            return

        if action == "set_fields" or (
            not action and isinstance(meta.get("set_fields"), dict)
        ):
            fields_map = meta.get("fields") if action == "set_fields" else meta.get("set_fields")
            if not isinstance(fields_map, dict):
                fields_map = meta.get("fields") or {}
            self._apply_set_fields(line, fields_map, context)
            return

        if action == "set_options":
            fields_map = meta.get("fields") or {}
            if not isinstance(fields_map, dict):
                return
            for target_name, source in fields_map.items():
                source = str(source or "")
                options_data = None
                if source.startswith("geocodes."):
                    options_data = self._path_get(context.get("geocodes"), source[9:])
                elif source in context:
                    options_data = context.get(source)
                if not isinstance(options_data, list):
                    continue
                target = self._option_target_line(str(target_name))
                if not target:
                    # Prefer visible text twin when select twin is hidden.
                    target = self._line_by_name(str(target_name))
                if not target:
                    continue
                if target.data_type == "select":
                    commands = [(5, 0, 0)]
                    for entry in options_data:
                        if isinstance(entry, dict):
                            name = str(
                                entry.get("name")
                                or entry.get("value")
                                or entry.get("key")
                                or ""
                            ).strip()
                            key = str(
                                entry.get("code") or entry.get("key") or name
                            ).strip()
                        else:
                            name = str(entry).strip()
                            key = name
                        if not name:
                            continue
                        commands.append((0, 0, {"key": key, "name": name}))
                    target.option_id = False
                    target.option_ids = commands
            return

        # Legacy set_fields without action (CO city_select).
        if isinstance(meta.get("set_fields"), dict) and line.option_id:
            self._apply_set_fields(line, meta["set_fields"], context)

    def _apply_request_action(self, line, meta: dict, context: dict) -> None:
        condition = (meta.get("condition") or "").strip()
        value = (line.value or "").strip()
        if condition.startswith("/") and condition.endswith("/"):
            pattern = condition[1:-1]
            if not re.search(pattern, value or ""):
                return
        url = self._resolve_geocode_url(
            meta.get("url") or "",
            state_code=(line.option_id.key if line.option_id else ""),
            zipcode=value,
        )
        name = (meta.get("name") or "response").strip()
        if "geocodes.envia.com/zipcode" in url or name == "geocodes":
            country_code = (self.country_id.code or "").strip().upper()
            entries = EnviaGeocodesClient().lookup_zipcode(country_code, value)
            if not entries:
                return
            data_path = (meta.get("data_path") or "[0]").strip()
            if data_path == "[0]":
                context[name] = entries[0]
            else:
                context[name] = entries
            return
        if not url or "$" in url:
            return
        try:
            body = EnviaClient._public_get_json(url)
        except (EnviaApiError, UserError):
            return
        context[name] = body

    def _apply_set_fields(self, line, fields_map: dict, context: dict) -> None:
        if not isinstance(fields_map, dict):
            return
        selected = line.option_id.key if line.option_id else (line.value or "")
        selected_name = line.option_id.name if line.option_id else selected
        geocodes = context.get("geocodes") or {}
        for field_name, template in fields_map.items():
            value = str(template or "")
            value = value.replace("{{$city}}", selected).replace("$city", selected)
            value = value.replace("{{$city_name}}", selected_name)
            value = value.replace("{{$state}}", selected).replace("$state", selected)
            value = value.replace("{{state_code}}", str(context.get("state_code") or ""))
            value = value.replace(
                "{{geocodes.locality}}",
                str(geocodes.get("locality") or ""),
            )
            if "{{geocodes.suburbs[0]}}" in value:
                suburbs = geocodes.get("suburbs") or []
                suburb = suburbs[0] if suburbs else ""
                value = value.replace("{{geocodes.suburbs[0]}}", str(suburb))
            # Generic {{geocodes.*}} / {{context}} leftovers via path.
            for match in re.findall(r"\{\{([^}]+)\}\}", value):
                path = match.strip()
                resolved = None
                if path.startswith("geocodes."):
                    resolved = self._path_get(geocodes, path[9:])
                elif path in context:
                    resolved = context.get(path)
                if resolved is not None:
                    value = value.replace("{{%s}}" % match, str(resolved))
            if self._set_promoted_select(str(field_name), value):
                continue
            target = self._line_by_name(str(field_name))
            if not target:
                continue
            if target.data_type == "select":
                option = target.option_ids.filtered(
                    lambda opt, key=value: opt.key == key or opt.name == key
                )[:1]
                if not option and value:
                    target.option_ids = [(0, 0, {"key": value, "name": value})]
                    option = target.option_ids.filtered(lambda opt, key=value: opt.key == key)[:1]
                target.option_id = option
            else:
                target.value = value

    def _fill_options_from_geocode(self, target_name: str, state_code: str) -> None:
        geocode_template = self._geocode_template_for_target(target_name)
        option_commands = self._load_provinces_options(state_code, geocode_template)
        if self.show_city_field and (
            self._is_promoted_city(target_name) or target_name in ("city", "city_select")
        ):
            self._replace_city_options(option_commands)
            return
        target = self._option_target_line(target_name)
        if not target:
            return
        target.option_id = False
        target.option_ids = [(5, 0, 0)] + option_commands

    def _load_form_lines(self) -> None:
        self.ensure_one()
        self.schema_warning = False
        self.schema_json = False
        self.show_state_field = False
        self.show_city_field = False
        self.state_required = False
        self.city_required = False
        self.state_option_id = False
        self.city_option_id = False
        self.option_ids = [(5, 0, 0)]
        country_code = (self.country_id.code or "").strip().upper()
        if not country_code:
            self.line_ids = [(5, 0, 0)]
            return

        try:
            schema = EnviaClient.get_address_structure(
                self._queries_base_url(),
                country_code,
            )
        except (EnviaApiError, UserError) as error:
            self.line_ids = [(5, 0, 0)]
            self.schema_warning = _(
                "Envia could not load address structure for %(country)s (%(code)s): %(error)s"
            ) % {
                "country": self.country_id.display_name,
                "code": country_code,
                "error": str(error),
            }
            return

        self.schema_json = json.dumps(schema)
        state_options = self._fetch_state_options(country_code, kind="state")
        option_commands = [(5, 0, 0)]
        commands = [(5, 0, 0)]
        for field_def in schema:
            if not isinstance(field_def, dict):
                continue
            # Only paint visible fields (hidden twins stay in schema_json for geocode).
            if not field_def.get("visible", True):
                continue
            field_id = (field_def.get("fieldId") or "").strip()
            if not field_id or field_id in _IDENTITY_FIELD_IDS:
                continue
            rules = field_def.get("rules") or {}
            label = (field_def.get("fieldLabel") or field_id).strip()
            field_type = (field_def.get("fieldType") or "text").strip().lower()
            # State/city selects live on the wizard form (list M2o domains break NewId).
            if self._is_promoted_state(field_id) and field_type == "select":
                self.show_state_field = True
                self.state_label = label or _("State")
                self.state_required = bool(rules.get("required"))
                option_commands.extend(state_options)
                continue
            if self._is_promoted_city(field_id) and field_type == "select":
                self.show_city_field = True
                self.city_label = label or _("City")
                self.city_required = bool(rules.get("required"))
                continue
            data_type = "select" if field_type == "select" else "text"
            option_values = [
                option
                for option in (rules.get("defaultValues") or [])
                if isinstance(option, dict) and str(option.get("key") or "").strip()
            ]
            line_vals = {
                "field_id": field_id,
                "data_name": (
                    field_def.get("dataName")
                    or field_def.get("fieldName")
                    or field_id
                ).strip(),
                "label": label,
                "placeholder": (field_def.get("fieldPlaceholder") or "").strip() or False,
                "data_type": data_type,
                "required": bool(rules.get("required")),
                "geocode_template": (field_def.get("geocode") or "").strip() or False,
                "on_change_json": json.dumps(field_def.get("on_change") or []),
            }
            if data_type == "select" and option_values:
                line_vals["option_ids"] = [
                    (
                        0,
                        0,
                        {
                            "key": str(option.get("key") or "").strip(),
                            "name": str(
                                option.get("value") or option.get("key") or ""
                            ).strip(),
                        },
                    )
                    for option in option_values
                ]
            commands.append((0, 0, line_vals))
        self.option_ids = option_commands
        self.line_ids = commands
        if len(commands) <= 1 and not self.show_state_field and not self.show_city_field:
            self.schema_warning = _(
                "Envia returned no visible address fields for %(code)s."
            ) % {"code": country_code}

    def action_validate(self):
        self.ensure_one()
        missing = []
        if not (self.name or "").strip():
            missing.append(_("Full name"))
        if not (self.email or "").strip():
            missing.append(_("Email"))
        if not (self.phone or "").strip():
            missing.append(_("Phone"))
        if not (self.phone_dial_code or "").strip():
            missing.append(_("Dial code"))
        if self.show_state_field and self.state_required and not self.state_option_id:
            missing.append(self.state_label or _("State"))
        if self.show_city_field and self.city_required and not self.city_option_id:
            missing.append(self.city_label or _("City"))
        for line in self.line_ids:
            if not line.required:
                continue
            if line.data_type == "select":
                if not line.option_id:
                    missing.append(line.label or line.field_id)
            elif not (line.value or "").strip():
                missing.append(line.label or line.field_id)
        if missing:
            raise UserError(
                _("Please fill in the required fields: %s") % ", ".join(missing)
            )
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Billing info validated"),
                "message": _("All required billing fields look complete."),
                "type": "success",
                "sticky": False,
                "next": {"type": "ir.actions.act_window_close"},
            },
        }

    @api.model
    def fetch_generic_form(self, country_code: str, form: str = "address_info") -> list:
        """Proxy ``GET /generic-form`` (avoids browser CORS)."""
        country_code = (country_code or "").strip().upper()
        form = (form or "address_info").strip() or "address_info"
        if not country_code:
            raise UserError(_("Country code is required."))
        base = self.env.company._envia_get_queries_base_url()
        return EnviaClient.get_generic_form(base, country_code, form=form)

    @api.model
    def fetch_states(self, country_code: str) -> list:
        """Proxy ``GET /state`` for select fields without inline options."""
        country_code = (country_code or "").strip().upper()
        if not country_code:
            return []
        base = self.env.company._envia_get_queries_base_url()
        try:
            return EnviaClient.get_states(base, country_code)
        except (EnviaApiError, UserError):
            return []

    @api.model
    def fetch_public_json(self, url: str):
        """Proxy arbitrary Envia Queries GET used by ``on_change`` request/geocode."""
        url = (url or "").strip()
        if not url.startswith(("http://", "https://")):
            raise UserError(_("Invalid Envia request URL."))
        return EnviaClient._public_get_json(url)

    @api.model
    def get_origin_warehouse_options(self):
        """Warehouse choices for the origin address form (id + readable address)."""
        warehouses = self.env["stock.warehouse"].search(
            [("company_id", "=", self.env.company.id)],
            order="name",
        )
        options = []
        for warehouse in warehouses:
            location = warehouse.lot_stock_id
            if not location:
                continue
            partner = warehouse.partner_id
            parts = []
            if partner:
                if partner.street:
                    parts.append(partner.street)
                if partner.street2:
                    parts.append(partner.street2)
                city_line = " ".join(
                    part for part in (partner.zip or "", partner.city or "") if part
                )
                if city_line:
                    parts.append(city_line)
                if partner.state_id:
                    parts.append(partner.state_id.name)
                if partner.country_id:
                    parts.append(partner.country_id.name)
            options.append(
                {
                    "id": warehouse.id,
                    "name": warehouse.display_name,
                    "location_id": location.id,
                    "address_label": ", ".join(parts)
                    if parts
                    else _("No address on warehouse contact"),
                    "defaults": (
                        self.partner_to_form_defaults(partner) if partner else False
                    ),
                }
            )
        return options

    @api.model
    def _envia_resolve_origin_warehouse(self, warehouse_id=None):
        """Resolve warehouse for Envia origin match / ``location_id``."""
        if warehouse_id:
            warehouse = self.env["stock.warehouse"].browse(int(warehouse_id))
            if warehouse.exists():
                return warehouse
        company = self.env.company
        if company.envia_default_origin_warehouse_id:
            return company.envia_default_origin_warehouse_id
        return self.env["stock.warehouse"].search(
            [("company_id", "=", company.id)],
            limit=1,
        )

    @api.model
    def _norm_origin_part(self, value) -> str:
        return " ".join(str(value or "").casefold().split())

    @api.model
    def _matching_shop_origin(self, addresses, payload: dict):
        """Return an existing shop origin that matches street+number, zip, city."""
        street = self._norm_origin_part(
            f"{payload.get('street') or ''} {payload.get('number') or ''}"
        )
        postal = self._norm_origin_part(
            payload.get("postal_code") or payload.get("zip")
        )
        if not street or not postal or not isinstance(addresses, list):
            return None
        city = self._norm_origin_part(payload.get("city"))
        country = self._norm_origin_part(
            payload.get("country") or payload.get("country_code")
        )
        for address in addresses or []:
            if not isinstance(address, dict):
                continue
            if self._norm_origin_part(address.get("street")) != street:
                continue
            if self._norm_origin_part(address.get("zip")) != postal:
                continue
            if city and self._norm_origin_part(address.get("city")) != city:
                continue
            if country and self._norm_origin_part(address.get("country_code")) != country:
                continue
            return address
        return None

    @api.model
    def save_billing_address(self, form_values: dict, warehouse_id=None) -> dict:
        """Create Envia user address then link it as the shop origin default.

        Always sends ``location_iden`` (``stock.warehouse.lot_stock_id`` as string)
        in the ``POST /user-address`` payload.
        """
        company = self.env.company
        shop_id = (company.envia_shop_id or "").strip()
        if not shop_id:
            raise UserError(
                _("Connect Envia first: shop id is missing on this company.")
            )
        # Bearer = company.envia_api_token (shipping API key).
        token = (company.envia_api_token or "").strip()
        if not token:
            raise UserError(
                _("Configure the Envia API token in Settings > Envia Shipping.")
            )

        payload = {
            key: value
            for key, value in (form_values or {}).items()
            if value not in (None, False, "")
        }
        name = str(payload.get("name") or "").strip()
        company_name = str(payload.get("company") or "").strip()
        if len(company_name) < 2:
            company_name = name
        if len(company_name) < 2:
            raise UserError(
                _("Company must be at least 2 characters (or use a longer full name).")
            )
        payload["name"] = name
        payload["company"] = company_name
        payload["category_id"] = int(payload.get("category_id") or 1)
        payload["type"] = int(payload.get("type") or 1)
        payload["shop_id"] = int(shop_id) if shop_id.isdigit() else shop_id

        if not warehouse_id:
            raise UserError(_("Select the warehouse this origin address belongs to."))
        warehouse = self._envia_resolve_origin_warehouse(warehouse_id)
        if not warehouse or not warehouse.lot_stock_id:
            raise UserError(
                _(
                    "No warehouse stock location found for the selected warehouse."
                )
            )
        # Envia API expects the key ``location_iden`` (string stock.location id).
        payload["location_iden"] = str(warehouse.lot_stock_id.id)

        client = EnviaClient(company._envia_get_queries_base_url(), token)
        origins = client.get_shop_default_addresses(shop_id)
        existing = self._matching_shop_origin(origins, payload)
        reused = bool(existing and existing.get("id"))
        if reused:
            address_id = str(existing["id"])
            create_body = existing
        else:
            create_body = client.create_user_address(payload)
            address_id = EnviaClient.extract_address_id(create_body)
        # Same Bearer token and Queries host; both endpoints are POST.
        match_body = client.set_shop_default_address(shop_id, address_id)
        label_parts = [
            name,
            str(payload.get("street") or "").strip(),
            str(payload.get("number") or "").strip(),
            str(payload.get("city") or "").strip(),
            str(payload.get("postal_code") or "").strip(),
        ]
        label = " · ".join(part for part in label_parts if part) or address_id
        warehouse_match = self.env["envia.warehouse.origin"].upsert_match(
            company,
            warehouse,
            {
                "id": address_id,
                "label": label,
                "name": name,
                "street": payload.get("street") or False,
                "city": payload.get("city") or False,
                "zip": payload.get("postal_code") or False,
                "phone": payload.get("phone") or False,
                "email": payload.get("email") or False,
                "country_code": payload.get("country") or False,
                "state_code": payload.get("state") or False,
            },
            update_partner=False,
        )
        return {
            "address_id": address_id,
            "create": create_body,
            "match": match_body,
            "warehouse_origin_id": warehouse_match.id,
            "reused": reused,
        }

    @api.model
    def partner_to_form_defaults(self, partner):
        """Map ``res.partner`` address fields to Envia generic-form seeds."""
        partner.ensure_one()
        country = partner.country_id or self.env.company.country_id
        if not country:
            country = self.env["res.country"].search([("code", "=", "MX")], limit=1)
        country_code = (country.code or "MX").upper() if country else "MX"
        phone_code = (
            f"+{country.phone_code}" if country and country.phone_code else "+52"
        )
        phone = (partner.phone or getattr(partner, "mobile", None) or "").strip()
        local_phone = phone
        dial = phone_code.lstrip("+")
        for prefix in (phone_code, f"+{dial}", f"00{dial}"):
            if phone.startswith(prefix):
                local_phone = phone[len(prefix) :].lstrip()
                break
        return {
            "country_code": country_code,
            "phone_code": phone_code,
            "identity": {
                "name": partner.name or "",
                "company": (partner.parent_id.name or partner.name or ""),
                "email": partner.email or "",
                "phone": local_phone,
            },
            "values": {
                "street": partner.street or "",
                "number": "",
                "district": partner.street2 or "",
                "postal_code": partner.zip or "",
                "city": partner.city or "",
                "state": (partner.state_id.code or "").strip().upper(),
            },
        }

    @api.model
    def action_open_billing_info_wizard(self, partner=None, warehouse=None):
        company = self.env.company
        if partner is None and self.env.context.get("default_partner_id"):
            partner = self.env["res.partner"].browse(
                self.env.context["default_partner_id"]
            )
        if warehouse is None and self.env.context.get("default_warehouse_id"):
            warehouse = self.env["stock.warehouse"].browse(
                self.env.context["default_warehouse_id"]
            )
        if not warehouse:
            warehouse = self._envia_resolve_origin_warehouse()
        defaults = False
        if partner:
            defaults = self.partner_to_form_defaults(partner)
            country_code = defaults["country_code"]
            phone_code = defaults["phone_code"]
        else:
            country = company.country_id
            if not country:
                country = self.env["res.country"].search([("code", "=", "MX")], limit=1)
            country_code = (country.code or "MX").upper() if country else "MX"
            phone_code = (
                f"+{country.phone_code}" if country and country.phone_code else "+52"
            )
        params = {
            "country_code": country_code,
            "form": "address_info",
            "phone_code": phone_code,
            "queries_base_url": company._envia_get_queries_base_url(),
        }
        if warehouse:
            params["warehouse_id"] = warehouse.id
        if defaults:
            params["initial_values"] = {
                "identity": defaults["identity"],
                "values": defaults["values"],
                "phone_code": defaults["phone_code"],
            }
        return {
            "type": "ir.actions.client",
            "tag": "envia_generic_form",
            "name": _("Origin address"),
            "params": params,
        }


class EnviaBillingInfoLine(models.TransientModel):
    _name = "envia.billing.info.line"
    _description = "Envia Billing Info Field Line"
    _order = "id"

    wizard_id = fields.Many2one(
        "envia.billing.info.wizard",
        required=True,
        ondelete="cascade",
    )
    field_id = fields.Char(required=True)
    data_name = fields.Char(required=True)
    label = fields.Char(required=True)
    placeholder = fields.Char()
    data_type = fields.Selection(
        [
            ("text", "Text"),
            ("select", "Select"),
        ],
        required=True,
        default="text",
    )
    required = fields.Boolean()
    geocode_template = fields.Char()
    on_change_json = fields.Char()
    value = fields.Char(string="Text value")
    option_ids = fields.One2many(
        "envia.billing.info.option",
        "line_id",
        string="Options",
    )
    option_id = fields.Many2one(
        "envia.billing.info.option",
        string="Selected option",
        domain="[('id', 'in', option_ids)]",
    )

    @api.onchange("option_id")
    def _onchange_option_id(self):
        if self.wizard_id and self.option_id:
            self.wizard_id.apply_line_on_change(self)

    @api.onchange("value")
    def _onchange_value(self):
        if self.wizard_id and (self.value or "").strip() and self.on_change_json:
            self.wizard_id.apply_line_on_change(self)


class EnviaBillingInfoOption(models.TransientModel):
    _name = "envia.billing.info.option"
    _description = "Envia Billing Info Select Option"
    _order = "name, id"

    wizard_id = fields.Many2one(
        "envia.billing.info.wizard",
        ondelete="cascade",
    )
    line_id = fields.Many2one(
        "envia.billing.info.line",
        ondelete="cascade",
    )
    kind = fields.Char(index=True)
    key = fields.Char(required=True)
    name = fields.Char(required=True)
