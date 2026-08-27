from __future__ import annotations

import json
import logging
import os
import re
from typing import Any
from urllib.parse import urlencode

import requests

from odoo import _
from odoo.exceptions import UserError

from .envia_plugin_setup import get_envia_module_version, normalize_envia_plugin_version

_logger = logging.getLogger(__name__)

OAUTH_INTEGRATION_URL_PARAM = "envia.oauth_integration_url"
OAUTH_POPUP_URL_PARAM = "envia.oauth_popup_url"
OAUTH_USE_SIZED_POPUP_PARAM = "envia.oauth_use_sized_popup"
ESHOP_TEST_URL_PARAM = "envia.eshop_test_url"
ESHOP_ACCESSES_ME_URL_PARAM = "envia.eshop_accesses_me_url"
OAUTH_INTEGRATION_URL_ENV = "ENVIA_OAUTH_INTEGRATION_URL"
OAUTH_POPUP_URL_ENV = "ENVIA_OAUTH_POPUP_URL"
ESHOP_TEST_URL_ENV = "ENVIA_ESHOP_TEST_URL"
ESHOP_ACCESSES_ME_URL_ENV = "ENVIA_ESHOP_ACCESSES_ME_URL"
DEFAULT_OAUTH_INTEGRATION_URL = (
    "https://oauth.ecartapi.com/oauth/NrUAVHsfjTJUE0NacgA7mVSogkfTuWTW/integration/odoo"
)
DEFAULT_OAUTH_POPUP_URL = (
    "https://oauth.ecartapi.com/NrUAVHsfjTJUE0NacgA7mVSogkfTuWTW?ecommerce=odoo"
)
DEFAULT_ESHOP_TEST_URL = "https://eshop-deve.herokuapp.com/api/v2/test"
DEFAULT_ESHOP_ACCESSES_ME_URL = "https://eshop-deve.herokuapp.com/accesses/me"


def get_oauth_integration_url(env) -> str:
    env_value = os.environ.get(OAUTH_INTEGRATION_URL_ENV)
    if env_value:
        return env_value.rstrip("/")
    configured_value = env["ir.config_parameter"].sudo().get_param(OAUTH_INTEGRATION_URL_PARAM)
    return (configured_value or DEFAULT_OAUTH_INTEGRATION_URL).rstrip("/")


def get_oauth_popup_url(env) -> str:
    env_value = os.environ.get(OAUTH_POPUP_URL_ENV)
    if env_value:
        return env_value.rstrip("/")
    configured_value = env["ir.config_parameter"].sudo().get_param(OAUTH_POPUP_URL_PARAM)
    return (configured_value or DEFAULT_OAUTH_POPUP_URL).rstrip("/")


def get_oauth_use_sized_popup(env) -> bool:
    return (
        env["ir.config_parameter"].sudo().get_param(OAUTH_USE_SIZED_POPUP_PARAM, "False") == "True"
    )


def build_integration_popup_url(
    env,
    *,
    url: str,
    database: str,
    email: str,
    api_key: str,
    company_id: int | None = None,
    user_id: int | None = None,
    ecommerce: str = "odoo",
    state: str = "fromPlugin",
    origin: str = "envia_odoo",
) -> str:
    """Build the Envia OAuth popup URL shown during plugin setup."""
    base_url = get_oauth_popup_url(env)
    separator = "&" if "?" in base_url else "?"
    query_params = {
        "url": (url or "").strip(),
        "database": (database or "").strip(),
        "email": (email or "").strip(),
        "apiKey": (api_key or "").strip(),
    }
    if company_id is not None:
        query_params["company"] = str(company_id)
    if user_id is not None:
        query_params["user"] = str(user_id)
    optional_params = {}
    if "ecommerce=" not in base_url.lower():
        optional_params["ecommerce"] = ecommerce
    if "state=" not in base_url.lower():
        optional_params["state"] = state
    if "origin=" not in base_url.lower():
        optional_params["origin"] = origin
    return f"{base_url}{separator}{urlencode({**optional_params, **query_params})}"


def get_eshop_test_url(env) -> str:
    env_value = os.environ.get(ESHOP_TEST_URL_ENV)
    if env_value:
        return env_value.rstrip("/")
    configured_value = env["ir.config_parameter"].sudo().get_param(ESHOP_TEST_URL_PARAM)
    return (configured_value or DEFAULT_ESHOP_TEST_URL).rstrip("/")


def get_eshop_accesses_me_url(env) -> str:
    env_value = os.environ.get(ESHOP_ACCESSES_ME_URL_ENV)
    if env_value:
        return env_value.rstrip("/")
    configured_value = env["ir.config_parameter"].sudo().get_param(ESHOP_ACCESSES_ME_URL_PARAM)
    return (configured_value or DEFAULT_ESHOP_ACCESSES_ME_URL).rstrip("/")


class EnviaOauthClient:
    def __init__(self, env, timeout: int = 60) -> None:
        self.env = env
        self.integration_url = get_oauth_integration_url(env)
        self.test_url = get_eshop_test_url(env)
        self.accesses_me_url = get_eshop_accesses_me_url(env)
        self.timeout = timeout
        self._access_token: str | None = None

    @property
    def access_token(self) -> str | None:
        return self._access_token

    def register_odoo_integration(
        self,
        *,
        url: str,
        database: str,
        email: str,
        api_key: str,
        sandbox: bool = False,
        version: str | None = None,
        callback_url: str | None = None,
    ) -> dict[str, Any]:
        api_key = (api_key or "").strip()
        if not api_key:
            raise UserError(_("Envia OAuth integration requires a valid API key."))

        form_body = self.build_integration_form_body(
            url=url,
            database=database,
            email=email,
            api_key=api_key,
            sandbox=sandbox,
            version=version,
            callback_url=callback_url,
        )
        _logger.info(
            "Envia OAuth integration POST %s (apiKey length=%s)",
            self.integration_url,
            len(api_key),
        )
        try:
            response = requests.post(
                self.integration_url,
                data=form_body,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=self.timeout,
            )
        except requests.RequestException as error:
            raise UserError(_("Envia OAuth connection error: %s") % error) from error

        body = self._parse_json_response(response)
        if response.status_code >= 400:
            message = self._extract_error_message(body, response)
            raise UserError(_("Envia OAuth registration failed (%(status)s): %(message)s") % {
                "status": response.status_code,
                "message": message,
            })
        access_token = self._extract_access_token(body)
        if not access_token:
            raise UserError(_("Envia OAuth registration did not return an access token."))
        self._access_token = access_token
        return body

    def verify_integration(self, access_token: str | None = None) -> bool:
        token = access_token or self._access_token
        if not token:
            raise UserError(_("Missing Envia access token for integration verification."))

        _logger.info("Envia OAuth verification GET %s", self.test_url)
        try:
            response = requests.get(
                self.test_url,
                headers={"Authorization": token},
                timeout=self.timeout,
            )
        except requests.RequestException as error:
            raise UserError(_("Envia integration verification error: %s") % error) from error

        body = self._parse_json_response(response)
        if response.status_code >= 400:
            message = self._extract_error_message(body, response)
            raise UserError(_("Envia integration verification failed (%(status)s): %(message)s") % {
                "status": response.status_code,
                "message": message,
            })
        return body.get("success") is True

    def fetch_store_access(self, access_token: str | None = None) -> dict[str, Any]:
        token = access_token or self._access_token
        if not token:
            raise UserError(_("Missing Envia access token for store access lookup."))

        _logger.info("Envia store access GET %s", self.accesses_me_url)
        try:
            response = requests.get(
                self.accesses_me_url,
                headers={"Authorization": token},
                timeout=self.timeout,
            )
        except requests.RequestException as error:
            raise UserError(_("Envia store access lookup error: %s") % error) from error

        body = self._parse_json_response(response)
        if response.status_code >= 400:
            message = self._extract_error_message(body, response)
            raise UserError(_("Envia store access lookup failed (%(status)s): %(message)s") % {
                "status": response.status_code,
                "message": message,
            })
        if body.get("success") is False:
            raise UserError(_("Envia store access lookup returned an unsuccessful response."))
        return self.extract_store_access_info(body)

    @staticmethod
    def extract_store_access_info(body: dict[str, Any]) -> dict[str, Any]:
        store = body.get("store") if isinstance(body.get("store"), dict) else {}
        if not store and isinstance(body.get("access"), dict) and body.get("_id"):
            store = body
        access = store.get("access") if isinstance(store.get("access"), dict) else {}
        version = normalize_envia_plugin_version(access.get("version") or store.get("version"))
        shipping_api_token = EnviaOauthClient.extract_shipping_api_token(body)
        return {
            "version": version,
            "shipping_api_token": shipping_api_token,
            "store_id": store.get("id") or store.get("_id"),
            "store_url": access.get("url") or store.get("url"),
            "database": access.get("database"),
            "email": access.get("email"),
        }

    @staticmethod
    def extract_shipping_api_token(body: dict[str, Any]) -> str | False:
        oauth_token = EnviaOauthClient._extract_access_token(body)
        store = body.get("store") if isinstance(body.get("store"), dict) else {}
        if not store and isinstance(body.get("access"), dict) and body.get("_id"):
            store = body
        access = store.get("access") if isinstance(store.get("access"), dict) else {}
        containers: list[dict[str, Any]] = [body]
        if store:
            containers.append(store)
        if access:
            containers.append(access)

        shipping_token_keys = (
            "envia_api_token",
            "api_token",
            "apiToken",
            "shipping_api_token",
            "shippingToken",
            "envia_token",
            "token_api",
        )
        for container in containers:
            for key in shipping_token_keys:
                value = container.get(key)
                if not value:
                    continue
                token = str(value).strip()
                if token and token != oauth_token:
                    return token
        return False

    def _parse_json_response(self, response: requests.Response) -> dict[str, Any]:
        response_text = (response.text or "").strip()
        if not response_text:
            return {}
        try:
            body = response.json()
        except json.JSONDecodeError as error:
            if response.status_code >= 400:
                raise UserError(
                    _("Envia OAuth request failed (HTTP %(status)s): %(message)s") % {
                        "status": response.status_code,
                        "message": self._extract_response_message(response, response_text),
                    }
                ) from error
            raise UserError(
                _("Envia OAuth returned invalid JSON (HTTP %s).") % response.status_code
            ) from error
        if not isinstance(body, dict):
            raise UserError(_("Envia OAuth returned an unexpected response format."))
        return body

    @staticmethod
    def build_integration_form_body(
        *,
        url: str,
        database: str,
        email: str,
        api_key: str,
        sandbox: bool = False,
        version: str | None = None,
        callback_url: str | None = None,
    ) -> str:
        fields = [
            ("url", (url or "").strip()),
            ("database", (database or "").strip()),
            ("email", (email or "").strip()),
            ("apiKey", (api_key or "").strip()),
            ("sandbox", str(sandbox).lower()),
        ]
        if callback_url:
            fields.append(("callbackUrl", callback_url.strip()))
        if version:
            fields.append(("version", version.strip()))
        return urlencode(fields)

    @staticmethod
    def _extract_access_token(body: dict[str, Any]) -> str | None:
        token_keys = (
            "access_token",
            "accessToken",
            "token",
            "json_web_token",
            "jsonWebToken",
            "authorization",
        )
        containers: list[dict[str, Any]] = [body]
        for container_key in ("data", "result", "response", "store"):
            container = body.get(container_key)
            if isinstance(container, dict):
                containers.append(container)
                access = container.get("access")
                if isinstance(access, dict):
                    containers.append(access)

        for container in containers:
            for key in token_keys:
                value = container.get(key)
                if value:
                    return EnviaOauthClient._normalize_access_token(str(value))
        return None

    @staticmethod
    def _normalize_access_token(token: str) -> str:
        token = token.strip()
        if token.lower().startswith("bearer "):
            return token[7:].strip()
        return token

    @staticmethod
    def _extract_response_message(response: requests.Response, response_text: str) -> str:
        html_match = re.search(r'class="err-msg"[^>]*>([^<]+)', response_text, re.IGNORECASE)
        if html_match:
            return html_match.group(1).strip()
        for key in ("message", "error", "detail"):
            try:
                body = response.json()
            except json.JSONDecodeError:
                break
            if isinstance(body, dict):
                value = body.get(key)
                if value:
                    return str(value)
        return response_text[:500] or _("Unknown error")

    @staticmethod
    def _extract_error_message(body: dict[str, Any], response: requests.Response) -> str:
        for key in ("message", "error", "detail"):
            value = body.get(key)
            if value:
                return str(value)
        return EnviaOauthClient._extract_response_message(response, response.text or "")
