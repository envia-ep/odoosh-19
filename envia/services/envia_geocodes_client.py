import logging
from typing import Any

import requests

from odoo.exceptions import UserError

from .i18n import _
from .envia_official_adapter import EnviaOfficialAdapter

_logger = logging.getLogger(__name__)

GEOCODES_BASE_URL = "https://geocodes.envia.com/"


class EnviaGeocodesClient:
    def lookup_zipcode(self, country_code: str, zipcode: str) -> list[dict[str, Any]]:
        country_code = (country_code or "").strip().upper()
        zipcode = (zipcode or "").strip()
        if not country_code or not zipcode:
            return []
        url = f"{GEOCODES_BASE_URL}zipcode/{country_code}/{zipcode}"
        _logger.info("Envia Geocodes GET %s", url)
        try:
            response = requests.get(url, timeout=30)
        except requests.RequestException as error:
            raise UserError(_("Envia Geocodes connection error: %s") % error) from error
        if response.status_code == 404:
            return []
        if response.status_code >= 400:
            raise UserError(
                _("Envia Geocodes error (HTTP %(status)s).") % {"status": response.status_code}
            )
        body = response.json()
        if isinstance(body, list):
            return body
        data = body.get("data")
        return data if isinstance(data, list) else []

    @staticmethod
    def resolve_odoo_state(env, country, state_payload):
        if not country or not state_payload:
            return env["res.country.state"]
        codes = []
        iso_code = state_payload.get("iso_code") or ""
        if iso_code and "-" in iso_code:
            codes.append(iso_code.split("-")[-1])
        code_payload = state_payload.get("code") or {}
        for key in ("3digit", "2digit", "1digit"):
            value = code_payload.get(key)
            if value:
                codes.append(value)
        if not codes:
            return env["res.country.state"]
        search_codes = set(codes)
        if country.code == "MX":
            for code in codes:
                envia_code = EnviaOfficialAdapter.envia_state_code("MX", code)
                search_codes.update(EnviaOfficialAdapter.odoo_state_codes("MX", envia_code))
        return env["res.country.state"].search(
            [("country_id", "=", country.id), ("code", "in", list(search_codes))],
            limit=1,
        )

    def resolve_state_from_postal_code(self, env, country, postal_code):
        if not country or not postal_code:
            return env["res.country.state"]
        entries = self.lookup_zipcode(country.code, postal_code.strip())
        if not entries:
            return env["res.country.state"]
        return self.resolve_odoo_state(env, country, entries[0].get("state") or {})
