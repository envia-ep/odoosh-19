from __future__ import annotations

from typing import Any
import logging
import re

from odoo import _
from odoo.exceptions import UserError

from .dto import (
    AdditionalService,
    Contact,
    CreateShipmentRequest,
    CreateShipmentResponse,
    QuoteRequest,
    QuoteResponse,
    QuoteService,
    ShipmentItem,
)
from .envia_adapter_base import EnviaAdapterBase
from .envia_client import EnviaApiError, EnviaClient
from .envia_config import (
    get_envia_checkout_path,
    get_envia_ecommerce_private_base_url,
    get_envia_label_create_path,
    get_envia_order_shipments_unlink_path,
    get_envia_package_dimensions_path,
)

_logger = logging.getLogger(__name__)


class EnviaOfficialAdapter(EnviaAdapterBase):
    _MX_STATE_TO_ENVIA = {"CMX": "CX", "DIF": "CX", "DF": "CX", "NLE": "NL", "NUE": "NL"}
    _MX_ENVIA_TO_ODOO = ("CX", "CMX", "DIF", "DF")
    # ponytail: no product dims/UoM wired; never invent values — send null.
    @staticmethod
    def _package_dimensions() -> dict[str, float | None]:
        return {"length": None, "width": None, "height": None}

    @staticmethod
    def _package_length_unit(dimensions: dict[str, float | None]) -> str | None:
        if all(value is None for value in dimensions.values()):
            return None
        # No dimensional UoM source yet; do not invent "CM".
        return None

    @classmethod
    def envia_state_code(cls, country_code: str | None, state_code: str | None) -> str:
        if not state_code:
            return ""
        if country_code == "MX":
            return cls._MX_STATE_TO_ENVIA.get(state_code.upper(), state_code)
        return state_code

    @classmethod
    def odoo_state_codes(cls, country_code: str | None, envia_state: str | None) -> tuple[str, ...]:
        if not envia_state:
            return ()
        if country_code == "MX" and envia_state.upper() == "CX":
            return cls._MX_ENVIA_TO_ODOO
        return (envia_state,)

    def __init__(
        self,
        client: EnviaClient,
        *,
        shop_id: str,
        default_carriers: str = "dhl,fedex,estafeta",
    ) -> None:
        self.client = client
        self.shop_id = (shop_id or "").strip()
        self.default_carriers = [
            carrier.strip()
            for carrier in default_carriers.split(",")
            if carrier.strip()
        ]

    def quote(self, request: QuoteRequest) -> QuoteResponse:
        if not self.shop_id:
            raise UserError(
                _("Envia Shop ID is missing. Reconnect the Envia.com integration.")
            )
        payload = self._build_checkout_payload(request)
        carrier_errors: list[str] = []
        try:
            body = self.client._post(get_envia_checkout_path(self.shop_id), payload)
        except (UserError, EnviaApiError):
            raise
        checkout_error = EnviaOfficialAdapter._checkout_error_message(body)
        if checkout_error:
            _logger.warning("Envia checkout meta error: %s", checkout_error)
            raise UserError(_(
                "To get shipping quotes, enable Checkout in Envia.com "
                "and select the carriers you want to quote."
            ))

        services = self._parse_checkout_rates(body, request)
        carriers = self._resolve_carriers(request.carriers)
        if carriers and (request.carriers or "").strip().lower() != "all":
            services = [
                service for service in services if service.carrier in carriers
            ]

        expected_drop_off = EnviaOfficialAdapter._resolve_expected_drop_off(
            request
        )
        services = self._prefer_services_for_route(services, expected_drop_off)

        if not services:
            raise UserError(self._build_no_rates_message(request, carrier_errors))

        quote_id = (
            f"checkout_{self.shop_id}_{request.origin_postal_code}_"
            f"{request.destination_postal_code}_{len(services)}"
        )
        return QuoteResponse(
            quote_id=quote_id,
            services=services,
            raw={"response": body, "carrier_errors": carrier_errors},
        )

    def fetch_package_dimensions(
        self,
        items: list[ShipmentItem],
        currency: str,
        *,
        odoo_weight: float | None = None,
        auth_token: str | None = None,
    ) -> tuple[str, str]:
        """Preview packages Envia will use. Soft-fails to (preview_or_empty, hint).

        Bearer = envia_api_token against ENVIA_ECOMMERCE_PRIVATE_BASE_URL package/dimensions.
        """
        if not self.shop_id:
            return "", _(
                "Envia Shop ID is missing. Reconnect the Envia.com integration."
            )
        token = (auth_token or self.client.token or "").strip()
        if not token:
            return "", _(
                "Envia API token is missing. Check Settings > Envia Shipping."
            )
        ecommerce_base = get_envia_ecommerce_private_base_url()
        payload = EnviaOfficialAdapter.build_package_dimensions_payload(items, currency)
        client = EnviaClient(ecommerce_base, token)
        try:
            body = client._post(
                get_envia_package_dimensions_path(self.shop_id),
                payload,
            )
        except (UserError, EnviaApiError) as error:
            _logger.warning("Envia package dimensions preview failed: %s", error)
            return "", _(
                "Could not load Envia package dimensions preview: %s"
            ) % error
        if not isinstance(body, dict):
            return "", _("Could not load Envia package dimensions preview.")
        preview = EnviaOfficialAdapter.format_package_dimensions_preview(
            body, items=items
        )
        hint = EnviaOfficialAdapter.package_dimensions_sync_hint(
            body,
            items=items,
            odoo_weight=odoo_weight,
        )
        return preview, hint

    @staticmethod
    def build_package_dimensions_payload(
        items: list[ShipmentItem],
        currency: str,
    ) -> dict[str, Any]:
        payload_items = []
        for item in items:
            if item.product_id is None:
                continue
            quantity = item.quantity
            if float(quantity).is_integer():
                quantity = int(quantity)
            payload_items.append(
                {
                    "productId": str(item.product_id),
                    "variantId": None,
                    "name": item.description or "",
                    "quantity": quantity,
                }
            )
        return {"items": payload_items, "currency": currency or ""}

    @staticmethod
    def _format_preview_item_lines(
        items: list[ShipmentItem] | None,
        weight_unit: str = "",
    ) -> list[str]:
        """One bullet per Odoo item; Envia often collapses these to 'Multiple products'."""
        if not items:
            return []
        lines = []
        for item in items:
            name = (item.description or "").strip()
            if not name:
                continue
            quantity = item.quantity
            if float(quantity).is_integer():
                quantity = int(quantity)
            label = name if quantity == 1 else f"{name} ×{quantity}"
            if item.weight not in (None, False):
                line_weight = float(item.weight) * float(item.quantity)
                weight_part = f"{line_weight:g}"
                if weight_unit:
                    weight_part = f"{weight_part} {weight_unit}"
                label = f"{label} — {weight_part} (Odoo)"
            lines.append(f"• {label}")
        return lines

    @staticmethod
    def format_package_dimensions_preview(
        body: dict[str, Any],
        items: list[ShipmentItem] | None = None,
    ) -> str:
        packages = body.get("packages")
        if not isinstance(packages, list) or not packages:
            message = body.get("message")
            return str(message) if message else ""
        first_weight_unit = ""
        for package in packages:
            if isinstance(package, dict) and package.get("weight_unit"):
                first_weight_unit = str(package.get("weight_unit"))
                break
        item_lines = EnviaOfficialAdapter._format_preview_item_lines(
            items, weight_unit=first_weight_unit
        )
        lines = []
        for package in packages:
            if not isinstance(package, dict):
                continue
            name = package.get("name") or "Package"
            length = package.get("length")
            width = package.get("width")
            height = package.get("height")
            weight = package.get("weight")
            length_unit = package.get("length_unit") or ""
            weight_unit = package.get("weight_unit") or ""
            content = package.get("content") or ""
            dims = f"{length}x{width}x{height}"
            if length_unit:
                dims = f"{dims} {length_unit}"
            weight_part = f"{weight}"
            if weight_unit:
                weight_part = f"{weight_part} {weight_unit}"
            line = f"{name}: {dims}, {weight_part}"
            if item_lines:
                lines.append(line)
                lines.extend(item_lines)
                # Same item list applies to all Envia packages in this preview.
                item_lines = []
            elif content:
                lines.append(f"{line} — {content}")
            else:
                lines.append(line)
        return "\n".join(lines)

    @staticmethod
    def package_dimensions_sync_hint(
        body: dict[str, Any],
        *,
        items: list[ShipmentItem] | None = None,
        odoo_weight: float | None = None,
    ) -> str:
        """Warn only when Odoo weight disagrees with Envia package weight.

        package_automatic / "Package Default" alone is not enough: Envia often
        returns those flags even when Odoo products already have weight.
        """
        packages = body.get("packages") if isinstance(body.get("packages"), list) else []
        envia_weight = 0.0
        for package in packages:
            if not isinstance(package, dict):
                continue
            try:
                envia_weight += float(package.get("weight") or 0)
            except (TypeError, ValueError):
                continue
        odoo_total = None
        if items:
            item_weights = [
                (item.weight or 0.0) * item.quantity
                for item in items
                if item.weight not in (None, False)
            ]
            if item_weights:
                odoo_total = sum(item_weights)
        if odoo_total is None and odoo_weight is not None:
            try:
                odoo_total = float(odoo_weight)
            except (TypeError, ValueError):
                odoo_total = None
        if odoo_total is None:
            return ""
        if abs(float(odoo_total) - envia_weight) <= 0.01:
            return ""
        return _(
            "Envia package weight (%(envia)s) differs from Odoo (%(odoo)s). "
            "If you changed product weight/dimensions in Odoo, open Envia and "
            "sync package dimensions."
        ) % {
            "envia": envia_weight,
            "odoo": odoo_total,
        }

    @staticmethod
    def _build_no_rates_message(request: QuoteRequest, carrier_errors: list[str]) -> str:
        lines = [
            _("No shipping services available for this route."),
            _(
                "Route: %(origin_postal)s %(origin_state)s, %(origin_country)s -> "
                "%(destination_postal)s %(destination_state)s, %(destination_country)s"
            )
            % {
                "origin_postal": request.origin_postal_code,
                "origin_state": request.origin_state or "?",
                "origin_country": request.origin_country,
                "destination_postal": request.destination_postal_code,
                "destination_state": request.destination_state or "?",
                "destination_country": request.destination_country,
            },
        ]
        if request.origin_country != request.destination_country:
            lines.append(
                _(
                    "International routes often fail in Envia sandbox. "
                    "Try a domestic route first, for example MX 06500 to MX 03100."
                )
            )
        lines.extend(
            [
                _(
                    "Check that both contacts have street, city, postal code, "
                    "phone, and email."
                ),
                _(
                    "Verify that state/province matches the postal code on both sides."
                ),
            ]
        )
        if carrier_errors:
            lines.append(_("Carrier responses:"))
            lines.extend(f"- {error}" for error in carrier_errors[:5])
        return "\n".join(lines)

    def create_label_for_odoo_order(
        self,
        order_id: int,
        service_id: int | str | None = None,
    ) -> CreateShipmentResponse:
        """Create label via ecommerce ``POST label/create/{shop_id}``.

        Body uses the Odoo ``sale.order`` database id (string) and the selected
        Envia numeric ``service_id``.
        """
        if not self.shop_id:
            raise UserError(
                _("Envia Shop ID is missing. Reconnect the Envia.com integration.")
            )
        if not order_id:
            raise UserError(_("A sale order is required to create an Envia label."))
        token = (self.client.token or "").strip()
        if not token:
            raise UserError(
                _("Envia API token is missing. Check Settings > Envia Shipping.")
            )
        payload: dict[str, Any] = {"id": str(order_id)}
        if sid := EnviaOfficialAdapter._label_create_service_id(service_id):
            payload["service_id"] = sid
        ecommerce_base = get_envia_ecommerce_private_base_url()
        client = EnviaClient(ecommerce_base, token)
        body = client._post(
            get_envia_label_create_path(self.shop_id),
            payload,
        )
        return self._parse_label_create_response(body)

    @staticmethod
    def _label_create_service_id(service_id: int | str | None) -> int | None:
        if service_id in (None, False, ""):
            return None
        try:
            value = int(service_id)
        except (TypeError, ValueError):
            return None
        return value or None

    @staticmethod
    def _parse_label_create_response(body: dict[str, Any]) -> CreateShipmentResponse:
        if not isinstance(body, dict):
            raise UserError(_("Envia label/create returned an invalid response."))
        if not body.get("status"):
            raw = body.get("message") or body.get("error") or body
            message = EnviaClient.humanize_api_message(raw)
            # Known API phrases become a full UX sentence — do not prefix.
            if message != str(raw or "").strip():
                raise UserError(message)
            raise UserError(_("Envia did not generate a label: %s") % message)
        data = body.get("data") or {}
        labels = data.get("labels") if isinstance(data, dict) else None
        if not isinstance(labels, list) or not labels:
            raise UserError(_("Envia label/create returned no labels."))
        # ponytail: store first PDF URL only; multi-package trackings joined.
        first = labels[0] if isinstance(labels[0], dict) else {}
        trackings = [
            (entry.get("trackingNumber") or "").strip()
            for entry in labels
            if isinstance(entry, dict) and (entry.get("trackingNumber") or "").strip()
        ]
        tracking_number = ",".join(trackings)
        label_url = (first.get("label") or first.get("labelUrl") or "").strip() or None
        carrier = (first.get("carrier") or "").strip()
        shipment_id = first.get("shipmentId") or first.get("folio") or ""
        order_id = first.get("orderId") or first.get("order_id") or ""
        total_price = first.get("totalPrice")
        if total_price in (None, ""):
            total_price = first.get("price")
        pricing_total = float(total_price) if total_price not in (None, "") else None
        if not tracking_number and not label_url:
            raise UserError(
                _("Envia returned an incomplete label/create response.")
            )
        return CreateShipmentResponse(
            shipment_id=shipment_id,
            tracking_number=tracking_number,
            carrier=carrier,
            carrier_name=(
                first.get("carrierDescription") or first.get("carrierName") or carrier
            ),
            service=(
                first.get("serviceDescription")
                or first.get("serviceName")
                or first.get("service")
                or ""
            ),
            status=first.get("status") or "created",
            status_description=first.get("statusDescription") or _("Label created"),
            label_url=label_url,
            pricing_total=pricing_total,
            pricing_currency=first.get("currency"),
            order_id=order_id or None,
            raw=body if isinstance(body, dict) else {},
        )

    def create_shipment(self, request: CreateShipmentRequest) -> CreateShipmentResponse:
        carrier, service = self._parse_service_id(
            request.service_id,
            request.carrier,
            request.service_name,
        )
        declared_value = sum(item.price * item.quantity for item in request.items)
        dimensions = EnviaOfficialAdapter._package_dimensions()
        payload = {
            "origin": self._contact_to_official_address(request.origin_contact),
            "destination": self._contact_to_official_address(
                request.destination_contact
            ),
            "packages": [
                {
                    "type": "box",
                    "content": EnviaOfficialAdapter._normalize_package_content(
                        request.package_content
                    ),
                    "amount": 1,
                    "declaredValue": declared_value or 0,
                    "lengthUnit": EnviaOfficialAdapter._package_length_unit(
                        dimensions
                    ),
                    "weightUnit": request.weight_unit,
                    "weight": request.package_weight or 1.0,
                    "dimensions": dimensions,
                }
            ],
            "shipment": {
                "type": 1,
                "carrier": carrier,
                "service": service,
            },
            "settings": {
                "currency": (
                    request.items[0].currency if request.items else "MXN"
                ),
                "comments": request.order_reference or "",
                "printFormat": request.print_format,
                "printSize": request.print_size,
            },
        }
        additional_services = self._build_additional_services(
            request.origin_contact,
            request.destination_contact,
            request.additional_services,
        )
        if additional_services:
            payload["additionalServices"] = additional_services
        _logger.info(
            "Envia ship/generate payload destination branchCode=%s origin branchCode=%s",
            payload["destination"].get("branchCode"),
            payload["origin"].get("branchCode"),
        )
        body = self.client._post("ship/generate/", payload)
        return self._parse_generate_response(body, carrier, service)

    @staticmethod
    def _parse_generate_response(
        body: dict[str, Any],
        carrier: str,
        service: str,
    ) -> CreateShipmentResponse:
        data_list = body.get("data")
        if not isinstance(data_list, list) or not data_list:
            raw = body.get("message") or body.get("error") or body
            message = EnviaClient.humanize_api_message(raw)
            _logger.error("Envia ship/generate empty data: %s", body)
            if message != str(raw or "").strip():
                raise UserError(message)
            raise UserError(_("Envia did not generate a label: %s") % message)

        data = data_list[0]
        packages = data.get("packages") or []
        package = packages[0] if packages else {}
        shipment_id = data.get("shipmentId") or data.get("folio") or ""
        tracking_number = data.get("trackingNumber") or package.get("trackingNumber") or ""
        label_url = data.get("label") or data.get("labelUrl") or package.get("label") or ""
        total_price = data.get("totalPrice")
        if total_price in (None, ""):
            total_price = data.get("price")
        pricing_total = float(total_price) if total_price not in (None, "") else None

        if not shipment_id and not tracking_number:
            _logger.error("Envia ship/generate incomplete response: %s", body)
            raise UserError(
                _("Envia returned an incomplete label response. Check Odoo logs for ship/generate.")
            )

        status_description = data.get("statusDescription") or _("Label created")
        return CreateShipmentResponse(
            shipment_id=shipment_id,
            tracking_number=tracking_number,
            carrier=data.get("carrier", carrier),
            carrier_name=data.get("carrierDescription") or data.get("carrier", carrier),
            service=data.get("serviceDescription") or data.get("service", service),
            status=data.get("status") or "created",
            status_description=status_description,
            label_url=label_url,
            pricing_total=pricing_total,
            pricing_currency=data.get("currency"),
            raw=body,
        )

    def cancel_shipments(
        self,
        shipment_ids: list[int],
        *,
        queries_base_url: str,
    ) -> dict[str, Any] | list[Any]:
        """Cancel Envia shipments via queries ``shipments/bulk/cancel``."""
        if not shipment_ids:
            raise UserError(_("No Envia shipment IDs provided for cancellation."))
        body = self.client._post(
            "shipments/bulk/cancel",
            {"shipments": shipment_ids},
            base_url=queries_base_url,
        )
        return body if isinstance(body, (dict, list)) else {}

    def unlink_order_shipment(
        self,
        order_id: int,
        shipment_id: int,
        *,
        queries_base_url: str,
    ) -> dict[str, Any] | list[Any]:
        """Detach a label from an order (queries DELETE order-shipments)."""
        if not self.shop_id:
            raise UserError(
                _("Envia Shop ID is missing. Reconnect the Envia.com integration.")
            )
        if not order_id:
            raise UserError(_("A sale order is required to unlink an Envia label."))
        if not shipment_id:
            raise UserError(_("No Envia shipment ID provided to unlink."))
        body = self.client._delete(
            get_envia_order_shipments_unlink_path(self.shop_id, order_id),
            {"shipment_id": shipment_id},
            base_url=queries_base_url,
        )
        return body if isinstance(body, (dict, list)) else {}

    def _resolve_carriers(self, carriers: str) -> list[str]:
        if not carriers or carriers.strip().lower() == "all":
            return self.default_carriers
        return [carrier.strip() for carrier in carriers.split(",") if carrier.strip()]

    @staticmethod
    def _normalize_package_content(content: str | None, max_length: int = 100) -> str:
        from .payload_mapper import PayloadMapper

        return PayloadMapper.normalize_package_content(content or "Shipment", max_length=max_length)

    @staticmethod
    def _build_checkout_payload(request: QuoteRequest) -> dict[str, Any]:
        origin = EnviaOfficialAdapter._checkout_address_from_request(
            request.origin_contact,
            request.origin_postal_code,
            request.origin_country,
            request.origin_state,
        )
        destination = EnviaOfficialAdapter._checkout_address_from_request(
            request.destination_contact,
            request.destination_postal_code,
            request.destination_country,
            request.destination_state,
        )
        declared_value = request.declared_value or 0
        dimensions = EnviaOfficialAdapter._package_dimensions()
        return {
            "origin": origin,
            "destination": destination,
            "items": EnviaOfficialAdapter._checkout_items(request),
            "package": {
                "content": EnviaOfficialAdapter._normalize_package_content(request.content),
                "amount": "1",
                "type": "box",
                "dimensions": dimensions,
                "weight": str(request.weight),
                "lengthUnit": EnviaOfficialAdapter._package_length_unit(dimensions),
                "weightUnit": request.weight_unit,
                "insurance": "0",
                "declaredValue": f"{declared_value:.2f}",
            },
            "currency": request.currency,
            "locale": request.locale,
        }

    @staticmethod
    def _checkout_items(request: QuoteRequest) -> list[dict[str, Any]]:
        if request.items:
            return [
                EnviaOfficialAdapter._checkout_item_from_shipment_item(
                    item,
                    request,
                    index,
                )
                for index, item in enumerate(request.items)
            ]
        return [EnviaOfficialAdapter._default_checkout_item(request)]

    @staticmethod
    def _checkout_item_from_shipment_item(
        item: ShipmentItem,
        request: QuoteRequest,
        index: int,
    ) -> dict[str, Any]:
        odoo_product_id = item.product_id
        if odoo_product_id is None:
            raise UserError(
                _(
                    "Missing product on checkout item %(index)s. "
                    "Quote from a sales order or delivery with products."
                )
                % {"index": index + 1}
            )
        product_id = str(odoo_product_id)
        dimensions = EnviaOfficialAdapter._package_dimensions()
        return {
            "quantity": EnviaOfficialAdapter._checkout_str(item.quantity, money=False),
            "width": dimensions["width"],
            "height": dimensions["height"],
            "length": dimensions["length"],
            "weight": EnviaOfficialAdapter._checkout_str(
                item.weight or request.weight,
                money=True,
            ),
            "price": EnviaOfficialAdapter._checkout_str(item.price * item.quantity, money=True),
            "requiresShipping": "true",
            "productId": product_id,
            "variantId": None,
        }

    @staticmethod
    def _default_checkout_item(request: QuoteRequest) -> dict[str, Any]:
        dimensions = EnviaOfficialAdapter._package_dimensions()
        return {
            "quantity": "1",
            "width": dimensions["width"],
            "height": dimensions["height"],
            "length": dimensions["length"],
            "weight": EnviaOfficialAdapter._checkout_str(request.weight, money=True),
            "price": EnviaOfficialAdapter._checkout_str(request.declared_value or 0, money=True),
            "requiresShipping": "true",
            "productId": "package",
            "variantId": None,
        }

    @staticmethod
    def _checkout_str(value, *, money: bool) -> str:
        if value in (None, False, ""):
            return "0.00" if money else "0"
        if money:
            return f"{float(value):.2f}"
        number = float(value)
        return str(int(number)) if number == int(number) else str(number)

    @staticmethod
    def _origin_address_id_payload(contact: Contact | None) -> dict[str, Any] | None:
        address_id = (contact.address_id or "").strip() if contact else ""
        if not address_id:
            return None
        return {"address_id": address_id}

    @staticmethod
    def _checkout_address_from_request(
        contact: Contact | None,
        postal_code: str,
        country: str,
        state: str | None,
    ) -> dict[str, Any]:
        by_id = EnviaOfficialAdapter._origin_address_id_payload(contact)
        if by_id:
            return by_id
        if contact:
            return EnviaOfficialAdapter._contact_to_checkout_address(contact)
        return {
            "name": "Contact",
            "company": "Contact",
            "email": "shipping@company.com",
            "phone": "0000000000",
            "street": "Street",
            "number": "S/N",
            "district": "",
            "city": "City",
            "state": state or "",
            "country": country,
            "postalCode": postal_code,
        }

    @staticmethod
    def _contact_to_checkout_address(contact: Contact) -> dict[str, Any]:
        by_id = EnviaOfficialAdapter._origin_address_id_payload(contact)
        if by_id:
            return by_id
        street, number = EnviaOfficialAdapter._split_street_and_number(
            contact.street,
            contact.number,
        )
        payload = {
            "name": contact.name,
            "company": contact.company or contact.name,
            "email": contact.email,
            "phone": contact.phone,
            "street": street,
            "number": number,
            "district": contact.district or "",
            "city": contact.city,
            "state": EnviaOfficialAdapter.envia_state_code(contact.country, contact.state),
            "country": contact.country,
            "postalCode": contact.postal_code,
        }
        if contact.branch_code:
            payload["branchCode"] = contact.branch_code
        return payload

    @staticmethod
    def _checkout_error_message(body: Any) -> str:
        if not isinstance(body, dict):
            return ""
        if str(body.get("meta") or "").lower() != "error":
            return ""
        error = body.get("error")
        if isinstance(error, dict):
            return str(
                error.get("message") or error.get("description") or error.get("code") or ""
            ).strip()
        return str(body.get("message") or "").strip()

    @staticmethod
    def _normalize_checkout_rates_body(body: Any) -> list[dict[str, Any]]:
        if body is None:
            return []
        if isinstance(body, list):
            if not body:
                return []
            if all(isinstance(entry, dict) for entry in body):
                for key in ("data", "rates", "shippingMethods", "services"):
                    nested = body[0].get(key)
                    if isinstance(nested, list):
                        return [entry for entry in nested if isinstance(entry, dict)]
                return [entry for entry in body if isinstance(entry, dict)]
            return []
        if isinstance(body, dict):
            for key in ("data", "rates", "shippingMethods", "services"):
                candidate = body.get(key)
                if isinstance(candidate, list):
                    return [entry for entry in candidate if isinstance(entry, dict)]
        return []

    @staticmethod
    def _checkout_rate_value(rate: dict[str, Any], *keys: str):
        for key in keys:
            value = rate.get(key)
            if value not in (None, False, ""):
                return value
        return None

    @staticmethod
    def _parse_checkout_rates(
        body: dict[str, Any] | list[Any],
        request: QuoteRequest,
    ) -> list[QuoteService]:
        rates = EnviaOfficialAdapter._normalize_checkout_rates_body(body)
        services: list[QuoteService] = []
        for index, rate in enumerate(rates):
            rate_carrier = EnviaOfficialAdapter._checkout_rate_value(
                rate,
                "carrier",
                "carrierCode",
                "carrier_id",
            ) or ""
            service_code = EnviaOfficialAdapter._checkout_rate_value(
                rate,
                "service",
                "serviceCode",
                "service_id",
            ) or index
            drop_off = EnviaOfficialAdapter._checkout_rate_value(rate, "dropOff", "drop_off")
            price_value = EnviaOfficialAdapter._checkout_rate_value(
                rate,
                "totalPrice",
                "price",
                "total",
                "cost",
                "amount",
            )
            services.append(
                QuoteService(
                    service_id=f"{rate_carrier}:{service_code}",
                    envia_service_id=EnviaOfficialAdapter._checkout_rate_value(
                        rate,
                        "serviceId",
                        "service_id",
                    ),
                    carrier=str(rate_carrier),
                    carrier_name=EnviaOfficialAdapter._checkout_rate_value(
                        rate,
                        "carrierDescription",
                        "carrierName",
                        "carrier_name",
                    )
                    or str(rate_carrier),
                    service_name=EnviaOfficialAdapter._checkout_rate_value(
                        rate,
                        "serviceDescription",
                        "serviceName",
                        "service_name",
                        "name",
                        "description",
                    )
                    or str(service_code),
                    price=float(price_value or 0),
                    currency=EnviaOfficialAdapter._checkout_rate_value(
                        rate,
                        "currency",
                    )
                    or request.currency,
                    estimated_delivery_days=EnviaOfficialAdapter._parse_delivery_days(
                        EnviaOfficialAdapter._checkout_rate_value(
                            rate,
                            "deliveryEstimate",
                            "delivery_estimate",
                            "estimatedDelivery",
                        )
                    ),
                    drop_off=int(drop_off) if drop_off is not None else None,
                )
            )
        return services

    @staticmethod
    def _build_rate_payload(request: QuoteRequest, carrier: str) -> dict[str, Any]:
        origin = EnviaOfficialAdapter._address_from_request(
            request.origin_contact,
            request.origin_postal_code,
            request.origin_country,
            request.origin_state,
        )
        destination = EnviaOfficialAdapter._address_from_request(
            request.destination_contact,
            request.destination_postal_code,
            request.destination_country,
            request.destination_state,
        )
        dimensions = EnviaOfficialAdapter._package_dimensions()
        payload = {
            "origin": origin,
            "destination": destination,
            "packages": [
                {
                    "type": "box",
                    "content": EnviaOfficialAdapter._normalize_package_content(request.content),
                    "amount": 1,
                    "declaredValue": request.declared_value or 0,
                    "lengthUnit": EnviaOfficialAdapter._package_length_unit(dimensions),
                    "weightUnit": request.weight_unit,
                    "weight": request.weight,
                    "dimensions": dimensions,
                }
            ],
            "shipment": {"type": 1, "carrier": carrier},
        }
        additional_services = EnviaOfficialAdapter._build_additional_services(
            request.origin_contact,
            request.destination_contact,
            request.additional_services,
            expected_drop_off=request.expected_drop_off,
        )
        if additional_services:
            payload["additionalServices"] = additional_services
        return payload

    @staticmethod
    def _address_from_request(
        contact: Contact | None,
        postal_code: str,
        country: str,
        state: str | None,
    ) -> dict[str, Any]:
        by_id = EnviaOfficialAdapter._origin_address_id_payload(contact)
        if by_id:
            return by_id
        if contact:
            return EnviaOfficialAdapter._contact_to_official_address(contact)
        return {
            "name": "Contact",
            "phone": "0000000000",
            "street": "Street",
            "city": "City",
            "state": state or "",
            "country": country,
            "postalCode": postal_code,
        }

    @staticmethod
    def _split_street_and_number(street: str, number: str | None = None) -> tuple[str, str]:
        street = (street or "").strip()
        number = (number or "").strip() if number else ""
        if number:
            return street, number
        if not street:
            return street, "S/N"
        match = re.search(r"^(.*?)[,\s]+(\d+[A-Za-z0-9\-]*)$", street)
        if match and match.group(1).strip():
            return match.group(1).strip(), match.group(2)
        match = re.search(r"(\d+[A-Za-z0-9\-]*)$", street)
        if match:
            name = street[: match.start()].strip(" ,")
            if name:
                return name, match.group(1)
        return street, "S/N"

    @staticmethod
    def _contact_to_official_address(contact: Contact) -> dict[str, Any]:
        by_id = EnviaOfficialAdapter._origin_address_id_payload(contact)
        if by_id:
            return by_id
        street, number = EnviaOfficialAdapter._split_street_and_number(
            contact.street,
            contact.number,
        )
        payload = {
            "name": contact.name,
            "company": contact.company or contact.name,
            "phone": contact.phone,
            "email": contact.email,
            "street": street,
            "number": number,
            "district": contact.district or "",
            "city": contact.city,
            "state": EnviaOfficialAdapter.envia_state_code(contact.country, contact.state),
            "country": contact.country,
            "postalCode": contact.postal_code,
        }
        if contact.interior_number:
            payload["interiorNumber"] = contact.interior_number
        if contact.branch_code:
            payload["branchCode"] = contact.branch_code
        return payload

    @staticmethod
    def _resolve_expected_drop_off(request: QuoteRequest) -> int | None:
        if request.expected_drop_off is not None:
            return request.expected_drop_off
        return EnviaOfficialAdapter._expected_drop_off(
            request.origin_contact,
            request.destination_contact,
        )

    @staticmethod
    def _pickup_point_services_for_drop_off(
        expected_drop_off: int | None,
    ) -> list[dict[str, str]]:
        services: list[dict[str, str]] = []
        if expected_drop_off in (1, 3):
            services.append({"service": "pickup_point_pickup"})
        if expected_drop_off in (2, 3):
            services.append({"service": "pickup_point_delivery"})
        return services

    @staticmethod
    def _pickup_point_additional_services(
        origin_contact: Contact | None,
        destination_contact: Contact | None,
    ) -> list[dict[str, str]]:
        services: list[dict[str, str]] = []
        if origin_contact and origin_contact.branch_code:
            services.append({"service": "pickup_point_pickup"})
        if destination_contact and destination_contact.branch_code:
            services.append({"service": "pickup_point_delivery"})
        return services

    @staticmethod
    def _build_additional_services(
        origin_contact: Contact | None,
        destination_contact: Contact | None,
        extra_services: list[AdditionalService] | None = None,
        expected_drop_off: int | None = None,
    ) -> list[dict[str, Any]]:
        if expected_drop_off is not None:
            services: list[dict[str, Any]] = list(
                EnviaOfficialAdapter._pickup_point_services_for_drop_off(expected_drop_off)
            )
        else:
            services = list(
                EnviaOfficialAdapter._pickup_point_additional_services(
                    origin_contact,
                    destination_contact,
                )
            )
        for entry in extra_services or []:
            payload: dict[str, Any] = {"service": entry.service}
            if entry.amount is not None:
                payload["data"] = {"amount": str(entry.amount)}
            services.append(payload)
        return services

    @staticmethod
    def _expected_drop_off(
        origin_contact: Contact | None,
        destination_contact: Contact | None,
    ) -> int | None:
        origin_branch = bool(origin_contact and origin_contact.branch_code)
        destination_branch = bool(destination_contact and destination_contact.branch_code)
        if not origin_branch and not destination_branch:
            return None
        if origin_branch and destination_branch:
            return 3
        if origin_branch:
            return 1
        return 2

    @staticmethod
    def _prefer_services_for_route(
        services: list[QuoteService],
        expected_drop_off: int | None,
    ) -> list[QuoteService]:
        if not services:
            return services
        # Strict match on Envia dropOff so Pickup routes never list Ship-Ship rates
        # (and Ship-Ship never lists branch-only rates).
        if expected_drop_off is None:
            return [service for service in services if not service.drop_off]
        return [
            service
            for service in services
            if service.drop_off == expected_drop_off
        ]

    @staticmethod
    def pick_cheapest_service(
        services: list[QuoteService],
        expected_drop_off: int | None = None,
    ) -> QuoteService | None:
        """Public rate helper: cheapest service (optionally filtered by route)."""
        if not services:
            return None
        candidates = EnviaOfficialAdapter._prefer_services_for_route(
            services, expected_drop_off
        )
        return min(candidates, key=lambda service: service.price or 0.0)

    @staticmethod
    def _parse_service_id(
        service_id: int | str,
        carrier: str | None,
        service_name: str | None,
    ) -> tuple[str, str]:
        service_text = str(service_id)
        if ":" in service_text:
            parsed_carrier, parsed_service = service_text.split(":", 1)
            return parsed_carrier, parsed_service
        if carrier and service_name:
            return carrier, service_name
        raise UserError(_("Selected service is missing carrier information."))

    @staticmethod
    def _parse_delivery_days(delivery_estimate: str | None) -> int | None:
        if not delivery_estimate:
            return None
        digits = "".join(char for char in delivery_estimate if char.isdigit())
        return int(digits[:1]) if digits else None
