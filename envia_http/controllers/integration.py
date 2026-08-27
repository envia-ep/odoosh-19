import logging

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)


def _log_envia_inbound(tag: str) -> None:
    # ponytail: temporary dump so we can see Envia payloads; remove after debug
    body = (request.httprequest.data or b"")[:8000].decode("utf-8", errors="replace")
    auth = request.httprequest.headers.get("Authorization", "")
    _logger.warning(
        "ENVIA INBOUND %s path=%s qs=%s Authorization=%s body=%s",
        tag,
        request.httprequest.path,
        request.httprequest.query_string.decode("utf-8", errors="replace"),
        auth,
        body,
    )


class EnviaIntegrationHttpController(http.Controller):
    """Nodb route so Envia callbacks work without loading envia server-wide."""

    @http.route(
        "/envia/integration/callback",
        type="http",
        auth="none",
        methods=["POST"],
        csrf=False,
        save_session=False,
    )
    def integration_callback(self, **kwargs):
        _log_envia_inbound("callback")
        try:
            from odoo.addons.envia.controllers.envia_integration import (
                EnviaIntegrationController,
            )
        except ImportError:
            _logger.error("Envia module is not on the addons path.")
            return request.make_json_response(
                {
                    "ok": False,
                    "error": "envia_not_available",
                    "message": "Envia module must be installed on the server.",
                },
                status=503,
            )
        return EnviaIntegrationController().integration_callback(**kwargs)

    @http.route(
        "/envia/integration/connect",
        type="http",
        auth="none",
        methods=["POST"],
        csrf=False,
        save_session=False,
    )
    def integration_connect(self, **kwargs):
        _log_envia_inbound("connect")
        try:
            from odoo.addons.envia.controllers.envia_integration import (
                EnviaIntegrationController,
            )
        except ImportError:
            _logger.error("Envia module is not on the addons path.")
            return request.make_json_response(
                {
                    "ok": False,
                    "error": "envia_not_available",
                    "message": "Envia module must be installed on the server.",
                },
                status=503,
            )
        return EnviaIntegrationController().integration_connect(**kwargs)

    @http.route(
        "/jsonrpc",
        type="http",
        auth="none",
        methods=["POST"],
        csrf=False,
        save_session=False,
    )
    def jsonrpc_integration_validate(self, **kwargs):
        """Envia.com validates Odoo stores via common.authenticate on /jsonrpc."""
        _log_envia_inbound("jsonrpc")
        try:
            from odoo.addons.envia.services.envia_integration_callback import (
                handle_envia_jsonrpc_request,
            )
        except ImportError:
            _logger.error("Envia module is not on the addons path.")
            return request.make_json_response(
                {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": 503, "message": "Envia module not available"},
                },
                status=503,
            )
        raw_body = request.httprequest.data or b""
        status_code, payload = handle_envia_jsonrpc_request(
            raw_body,
            kwargs.get("db") or kwargs.get("database"),
        )
        return request.make_json_response(payload, status=status_code)

    @http.route(
        "/xmlrpc/2/common",
        type="http",
        auth="none",
        methods=["POST"],
        csrf=False,
        save_session=False,
    )
    def xmlrpc_common_integration_validate(self, **kwargs):
        """Envia.com validates Odoo stores via XML-RPC common.version/authenticate."""
        _log_envia_inbound("xmlrpc/common")
        try:
            from odoo.addons.envia.services.envia_integration_callback import (
                handle_envia_xmlrpc_common_request,
            )
        except ImportError:
            _logger.error("Envia module is not on the addons path.")
            return request.make_response(
                "Envia module not available",
                status=503,
                headers=[("Content-Type", "text/plain")],
            )
        raw_body = request.httprequest.data or b""
        status_code, payload = handle_envia_xmlrpc_common_request(
            raw_body,
            kwargs.get("db") or kwargs.get("database"),
        )
        return request.make_response(
            payload,
            status=status_code,
            headers=[("Content-Type", "text/xml; charset=utf-8")],
        )

    @http.route(
        "/xmlrpc/2/object",
        type="http",
        auth="none",
        methods=["POST"],
        csrf=False,
        save_session=False,
    )
    def xmlrpc_object_integration_proxy(self, **kwargs):
        """Envia.com calls execute_kw on /xmlrpc/2/object without a selected database."""
        _log_envia_inbound("xmlrpc/object")
        try:
            from odoo.addons.envia.services.envia_integration_callback import (
                handle_envia_xmlrpc_object_request,
            )
        except ImportError:
            _logger.error("Envia module is not on the addons path.")
            return request.make_response(
                "Envia module not available",
                status=503,
                headers=[("Content-Type", "text/plain")],
            )
        raw_body = request.httprequest.data or b""
        status_code, payload = handle_envia_xmlrpc_object_request(raw_body)
        return request.make_response(
            payload,
            status=status_code,
            headers=[("Content-Type", "text/xml; charset=utf-8")],
        )
