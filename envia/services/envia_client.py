import json
import logging
from typing import Any

import requests

from odoo import _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class EnviaApiError(UserError):
    """Raised when Envia API returns an error response."""


class EnviaClient:
    def __init__(
        self,
        base_url: str,
        token: str,
        timeout: int = 60,
        *,
        use_bearer_auth: bool = True,
    ) -> None:
        self.base_url = base_url.rstrip("/") + "/"
        self.token = token
        self.timeout = timeout
        self.use_bearer_auth = use_bearer_auth

    def _headers(self) -> dict[str, str]:
        authorization = self.token
        if self.use_bearer_auth and not authorization.lower().startswith("bearer "):
            authorization = f"Bearer {authorization}"
        return {
            "Authorization": authorization,
            "Content-Type": "application/json",
        }

    def _post(
        self,
        path: str,
        payload: dict[str, Any],
        *,
        base_url: str | None = None,
    ) -> dict[str, Any] | list[Any]:
        """Private HTTP POST to Envia (do not call from models/wizards)."""
        url_base = (base_url or self.base_url).rstrip("/") + "/"
        url = f"{url_base}{path.lstrip('/')}"
        _logger.info("Envia API POST %s", url)
        _logger.debug("Envia API POST %s payload=%s", url, payload)
        try:
            response = requests.post(
                url,
                headers=self._headers(),
                json=payload,
                timeout=self.timeout,
            )
        except requests.RequestException as error:
            raise UserError(_("Envia API connection error: %s") % error) from error

        self._log_response("POST", url, response)

        if response.status_code in (401, 403):
            if not self.use_bearer_auth:
                raise UserError(
                    _(
                        "Envia OAuth session is invalid or expired. "
                        "Open Settings > Envia Shipping and click Refresh token."
                    )
                )
            raise UserError(_("Invalid Envia API token. Check Settings > Envia Shipping."))

        if response.status_code == 404 and not self.use_bearer_auth:
            raise UserError(
                _(
                    "Envia eshop quote endpoint was not found (HTTP 404). "
                    "Refresh the OAuth connection or contact Envia support."
                )
            )

        if response.status_code == 402:
            raise UserError(EnviaClient.humanize_api_message("Not Enough money"))

        try:
            body = response.json()
        except json.JSONDecodeError as error:
            raise UserError(
                _("Envia API returned invalid JSON (HTTP %s).") % response.status_code
            ) from error

        if response.status_code >= 400:
            message = self._response_error_message(body, response)
            error_code = body.get("error", "") if isinstance(body, dict) else ""
            if error_code == "INVALID_POSTAL_CODE":
                raise UserError(_("Invalid postal code: %s") % message)
            if error_code == "NO_RATES_AVAILABLE":
                raise UserError(_("No shipping services available for this route."))
            if error_code == "WEIGHT_EXCEEDS_LIMIT":
                raise UserError(_("Weight exceeds limit: %s") % message)
            raise EnviaApiError(_("Envia API error (%(status)s): %(message)s") % {
                "status": response.status_code,
                "message": message,
            })

        return body

    @staticmethod
    def _log_response(method: str, url: str, response) -> None:
        _logger.info(
            "Envia API %s %s status=%s",
            method,
            url,
            response.status_code,
        )
        _logger.debug(
            "Envia API %s %s response=%s",
            method,
            url,
            response.text,
        )

    @staticmethod
    def humanize_api_message(message) -> str:
        text = str(message or "").strip()
        folded = text.casefold()
        if "feature not enabled" in folded:
            return _(
                'Turn on "Label generation from the store" in Envia for this shop, then try again.'
            )
        if (
            "not enough money" in folded
            or "saldo insuficiente" in folded
            or ("insufficient" in folded and "balance" in folded)
        ):
            return _(
                "Your Envia account does not have enough balance to create this "
                "label. Add funds at Envia.com and try again."
            )
        return text

    @staticmethod
    def _response_error_message(body, response) -> str:
        raw = response.text
        if isinstance(body, dict):
            raw = body.get("message") or body.get("error") or response.text
        elif isinstance(body, list) and body:
            first = body[0]
            if isinstance(first, dict):
                raw = first.get("message") or first.get("error") or response.text
        return EnviaClient.humanize_api_message(raw)

    def _get(
        self,
        path: str,
        *,
        base_url: str | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Private HTTP GET to Envia (do not call from models/wizards)."""
        url_base = (base_url or self.base_url).rstrip("/") + "/"
        url = f"{url_base}{path.lstrip('/')}"
        _logger.info("Envia API GET %s", url)
        _logger.debug("Envia API GET %s params=%s", url, params)
        try:
            response = requests.get(
                url,
                headers=self._headers(),
                params=params,
                timeout=self.timeout,
            )
        except requests.RequestException as error:
            raise UserError(_("Envia API connection error: %s") % error) from error

        self._log_response("GET", url, response)

        if response.status_code in (401, 403):
            if not self.use_bearer_auth:
                raise UserError(
                    _(
                        "Envia OAuth session is invalid or expired. "
                        "Open Settings > Envia Shipping and click Refresh token."
                    )
                )
            raise UserError(_("Invalid Envia API token. Check Settings > Envia Shipping."))

        try:
            body = response.json()
        except json.JSONDecodeError as error:
            raise UserError(
                _("Envia API returned invalid JSON (HTTP %s).") % response.status_code
            ) from error

        if response.status_code >= 400:
            message = self._response_error_message(body, response)
            raise EnviaApiError(_("Envia API error (%(status)s): %(message)s") % {
                "status": response.status_code,
                "message": message,
            })

        return body

    def _delete(
        self,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        base_url: str | None = None,
    ) -> dict[str, Any] | list[Any]:
        """Private HTTP DELETE to Envia (do not call from models/wizards)."""
        url_base = (base_url or self.base_url).rstrip("/") + "/"
        url = f"{url_base}{path.lstrip('/')}"
        _logger.info("Envia API DELETE %s", url)
        _logger.debug("Envia API DELETE %s payload=%s", url, payload)
        try:
            response = requests.delete(
                url,
                headers=self._headers(),
                json=payload if payload is not None else None,
                timeout=self.timeout,
            )
        except requests.RequestException as error:
            raise UserError(_("Envia API connection error: %s") % error) from error

        self._log_response("DELETE", url, response)

        body: dict[str, Any] | list[Any] | None = None
        if response.content:
            try:
                body = response.json()
            except json.JSONDecodeError:
                body = None

        if response.status_code in (401, 403):
            api_message = ""
            if isinstance(body, dict):
                api_message = (
                    body.get("message") or body.get("error") or body.get("description") or ""
                )
            # Business 403 (e.g. order not found) is not an invalid token.
            if api_message and str(api_message).lower() not in (
                "unauthorized",
                "forbidden",
            ):
                raise EnviaApiError(
                    _("Envia API error (%(status)s): %(message)s")
                    % {"status": response.status_code, "message": api_message}
                )
            if not self.use_bearer_auth:
                raise UserError(
                    _(
                        "Envia OAuth session is invalid or expired. "
                        "Open Settings > Envia Shipping and click Refresh token."
                    )
                )
            raise UserError(_("Invalid Envia API token. Check Settings > Envia Shipping."))

        if not response.content:
            if response.status_code >= 400:
                raise EnviaApiError(
                    _("Envia API error (%(status)s): %(message)s")
                    % {
                        "status": response.status_code,
                        "message": response.reason or response.text,
                    }
                )
            return {}

        if body is None:
            raise UserError(
                _("Envia API returned invalid JSON (HTTP %s).") % response.status_code
            )

        if response.status_code >= 400:
            message = self._response_error_message(body, response)
            raise EnviaApiError(_("Envia API error (%(status)s): %(message)s") % {
                "status": response.status_code,
                "message": message,
            })

        return body

    def get_branches(
        self,
        *,
        queries_base_url: str,
        carrier: str,
        country_code: str,
        zipcode: str,
        search_type: int = 1,
        city: str | None = None,
        state_code: str | None = None,
    ) -> list[dict[str, Any]]:
        path = f"branches/{carrier}/{country_code}"
        params: dict[str, Any] = {
            "type": search_type,
            "zipcode": zipcode,
            "allBranch": False,
        }
        if city:
            params["locality"] = city
        if state_code:
            params["state"] = state_code
        body = self._get(path, base_url=queries_base_url, params=params)
        if isinstance(body, list):
            branches = body
        else:
            data = body.get("data")
            branches = data if isinstance(data, list) else []
        return self.refine_branches_near_zip(branches, zipcode)

    @staticmethod
    def refine_branches_near_zip(
        branches: list[dict[str, Any]],
        zipcode: str,
        *,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        zipcode = (zipcode or "").strip()
        if not zipcode or not branches:
            return branches

        def branch_zip(entry: dict[str, Any]) -> str:
            address = entry.get("address") or {}
            return (address.get("postalCode") or address.get("zipcode") or "").strip()

        def distance_value(entry: dict[str, Any]) -> float:
            try:
                return float(entry.get("distance") or 9999)
            except (TypeError, ValueError):
                return 9999.0

        exact = [entry for entry in branches if branch_zip(entry) == zipcode]
        if exact:
            return sorted(exact, key=distance_value)[:limit]

        for prefix_len in (5, 3):
            if len(zipcode) < prefix_len:
                continue
            prefix = zipcode[:prefix_len]
            by_prefix = [entry for entry in branches if branch_zip(entry).startswith(prefix)]
            if by_prefix:
                return sorted(by_prefix, key=distance_value)[:limit]

        nearby = sorted(branches, key=distance_value)
        if nearby and nearby[0].get("distance") is not None:
            within = [entry for entry in nearby if distance_value(entry) <= 15]
            return (within or nearby)[:limit]
        return nearby[:limit]

    def test_connection(self, *, queries_base_url: str, country_code: str = "MX") -> dict[str, Any]:
        """Validate the shipping token against Envia Queries API."""
        body = self._get(
            "carrier",
            base_url=queries_base_url,
            params={"country_code": country_code},
        )
        carriers = body.get("data")
        if not isinstance(carriers, list):
            raise UserError(_("Envia API returned an unexpected response format."))
        return body

    @staticmethod
    def _public_get_json(
        url: str,
        *,
        params: dict[str, Any] | None = None,
        timeout: int = 60,
    ) -> Any:
        _logger.info("Envia API GET %s", url)
        _logger.debug("Envia API GET %s params=%s", url, params)
        try:
            response = requests.get(
                url,
                headers={"Accept": "application/json"},
                params=params,
                timeout=timeout,
            )
        except requests.RequestException as error:
            raise UserError(_("Envia API connection error: %s") % error) from error

        EnviaClient._log_response("GET", url, response)

        try:
            body = response.json()
        except json.JSONDecodeError as error:
            raise UserError(
                _("Envia API returned invalid JSON (HTTP %s).") % response.status_code
            ) from error

        if response.status_code >= 400:
            message = EnviaClient._response_error_message(body, response)
            raise EnviaApiError(
                _("Envia API error (%(status)s): %(message)s")
                % {"status": response.status_code, "message": message}
            )
        return body

    @staticmethod
    def get_generic_form(
        queries_base_url: str,
        country_code: str,
        *,
        form: str = "address_info",
        timeout: int = 60,
    ) -> list[dict[str, Any]]:
        """Fetch Envia Queries ``/generic-form`` (docs: country-address-structure)."""
        url_base = queries_base_url.rstrip("/") + "/"
        body = EnviaClient._public_get_json(
            f"{url_base}generic-form",
            params={"country_code": country_code, "form": form},
            timeout=timeout,
        )
        if not isinstance(body, list):
            raise UserError(_("Envia API returned an unexpected response format."))
        return body

    @staticmethod
    def get_address_structure(
        queries_base_url: str,
        country_code: str,
        *,
        timeout: int = 60,
    ) -> list[dict[str, Any]]:
        """Address field schema for a country (``GET /generic-form?form=address_info``)."""
        return EnviaClient.get_generic_form(
            queries_base_url,
            country_code,
            form="address_info",
            timeout=timeout,
        )

    @staticmethod
    def get_states(
        queries_base_url: str,
        country_code: str,
        *,
        timeout: int = 60,
    ) -> list[dict[str, Any]]:
        """Fetch Envia 2-letter state codes (``GET /state?country_code=XX``)."""
        url_base = queries_base_url.rstrip("/") + "/"
        body = EnviaClient._public_get_json(
            f"{url_base}state",
            params={"country_code": country_code},
            timeout=timeout,
        )
        if isinstance(body, list):
            return body
        data = body.get("data") if isinstance(body, dict) else None
        if not isinstance(data, list):
            raise UserError(_("Envia API returned an unexpected response format."))
        return data

    @staticmethod
    def get_provinces(
        queries_base_url: str,
        state_code: str,
        *,
        timeout: int = 60,
    ) -> list[dict[str, Any]]:
        """Fetch cities/provinces for a state code (geocode URL provinces/$state)."""
        state_code = (state_code or "").strip()
        if not state_code:
            return []
        url_base = queries_base_url.rstrip("/") + "/"
        body = EnviaClient._public_get_json(
            f"{url_base}provinces/{state_code}",
            timeout=timeout,
        )
        if isinstance(body, list):
            return body
        data = body.get("data") if isinstance(body, dict) else None
        if not isinstance(data, list):
            raise UserError(_("Envia API returned an unexpected response format."))
        return data

    def create_user_address(self, payload: dict[str, Any]) -> dict[str, Any] | list[Any]:
        """Create a user address (``POST /user-address`` on Queries)."""
        return self._post("user-address", payload)

    def get_shop_default_addresses(
        self,
        shop_id: str,
        *,
        base_url: str | None = None,
    ) -> list[dict[str, Any]]:
        """List shop origin addresses (``GET /shop-default-address/{shop_id}``)."""
        shop_id = (shop_id or "").strip()
        if not shop_id:
            raise UserError(_("Shop id is required to load origin addresses."))
        body = self._get(f"shop-default-address/{shop_id}", base_url=base_url)
        return self.normalize_shop_addresses(body)

    def set_shop_default_address(
        self,
        shop_id: str,
        address_id: str,
        *,
        base_url: str | None = None,
    ) -> dict[str, Any] | list[Any]:
        """Link shop origin address (``POST /shop-default-address/{shop_id}``)."""
        shop_id = (shop_id or "").strip()
        address_id = (address_id or "").strip()
        if not shop_id or not address_id:
            raise UserError(_("Shop id and address id are required to set the default address."))
        return self._post(
            f"shop-default-address/{shop_id}",
            {"address_id": address_id},
            base_url=base_url,
        )

    @staticmethod
    def extract_address_id(body: Any) -> str:
        """Pull address id from ``POST /user-address`` response shapes."""
        candidates: list[Any] = []
        if isinstance(body, dict):
            candidates.extend([body.get("address_id"), body.get("id")])
            data = body.get("data")
            if isinstance(data, dict):
                candidates.extend([data.get("address_id"), data.get("id")])
            elif isinstance(data, list) and data and isinstance(data[0], dict):
                candidates.extend([data[0].get("address_id"), data[0].get("id")])
        for value in candidates:
            if value is None or value is False:
                continue
            text = str(value).strip()
            if text:
                return text
        raise UserError(_("Envia did not return an address id after creating the address."))

    @staticmethod
    def normalize_shop_addresses(body: Any) -> list[dict[str, Any]]:
        """Normalize ``GET /shop-default-address`` payloads into option dicts."""
        items: list[Any] = []
        if isinstance(body, list):
            items = body
        elif isinstance(body, dict):
            data = body.get("data", body.get("addresses", body))
            if isinstance(data, list):
                items = data
            elif isinstance(data, dict):
                items = [data]
        result: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            nested = item.get("address") if isinstance(item.get("address"), dict) else {}
            merged = {**nested, **item}
            address_id = (
                merged.get("address_id")
                or merged.get("id")
                or nested.get("address_id")
                or nested.get("id")
            )
            if address_id is None or address_id is False:
                continue
            address_id = str(address_id).strip()
            if not address_id:
                continue
            street = (
                merged.get("street")
                or merged.get("address")
                or merged.get("street1")
                or ""
            )
            if isinstance(street, dict):
                street = street.get("street") or ""
            number = merged.get("number") or merged.get("street_number") or ""
            if number and not isinstance(number, dict):
                street = f"{street} {number}".strip() if street else str(number).strip()
            city = merged.get("city") or merged.get("locality") or ""
            postal = (
                merged.get("postal_code")
                or merged.get("postalCode")
                or merged.get("zip")
                or merged.get("zipcode")
                or ""
            )
            name = merged.get("name") or merged.get("company") or ""
            label_parts = [
                str(part).strip()
                for part in (name, street, city, postal)
                if part and str(part).strip()
            ]
            label = " · ".join(label_parts) if label_parts else _("Address %s") % address_id
            result.append(
                {
                    "id": address_id,
                    "label": label,
                    "name": str(name).strip() if name else "",
                    "street": str(street).strip() if street else "",
                    "city": str(city).strip() if city else "",
                    "zip": str(postal).strip() if postal else "",
                    "phone": str(
                        merged.get("phone") or merged.get("phone_number") or ""
                    ).strip(),
                    "email": str(merged.get("email") or "").strip(),
                    "country_code": str(
                        merged.get("country_code")
                        or merged.get("country")
                        or ""
                    ).strip().upper(),
                    "state_code": str(
                        merged.get("state_code")
                        or merged.get("state")
                        or ""
                    ).strip().upper(),
                }
            )
        return result

    def get_binary(self, url: str) -> bytes:
        try:
            response = requests.get(url, timeout=self.timeout)
            response.raise_for_status()
        except requests.RequestException as error:
            raise UserError(_("Failed to download label: %s") % error) from error
        return response.content
