"""Website checkout helpers for Envia Ship / Pickup rates (no TransientModel)."""

from __future__ import annotations

from typing import Any

from odoo import fields
from odoo.exceptions import UserError
from odoo.tools.translate import _

from .dto import QuoteRequest, QuoteResponse, QuoteService
from .envia_client import EnviaClient
from .envia_config import get_envia_checkout_path
from .envia_official_adapter import EnviaOfficialAdapter
from .payload_mapper import PayloadMapper, get_envia_adapter

BRANCH_LIMIT = 30
ROUTE_SHIP = "ship"
ROUTE_PICKUP = "pickup"
# Checkout often tags destination-ocurre as dropOff 1 when branchCode is set.
PICKUP_DROP_OFFS = (1, 2)


class WebsitePickupService:
    """List and apply Envia delivery options for website_sale checkout."""

    def __init__(self, env):
        self.env = env

    def list_options(self, order, route_type: str) -> list[dict[str, Any]]:
        order.ensure_one()
        if route_type == ROUTE_PICKUP:
            if not order.company_id.envia_checkout_enable_pickup:
                raise UserError(_("Pickup is disabled for website checkout."))
            return self.list_pickup_options(order)
        if route_type == ROUTE_SHIP:
            return self.list_ship_rates(order)
        raise UserError(_("Unknown delivery route type: %s") % route_type)

    def list_ship_rates(self, order) -> list[dict[str, Any]]:
        order.ensure_one()
        request = PayloadMapper.build_quote_request_from_sale_order(order)
        request.carriers = "all"
        request.expected_drop_off = None
        if request.destination_contact:
            request.destination_contact.branch_code = None
        if request.origin_contact:
            request.origin_contact.branch_code = None
        body = self._checkout_body(order.company_id, request)
        services = EnviaOfficialAdapter._parse_checkout_rates(body, request)
        ship_services = [service for service in services if not service.drop_off]
        options = []
        seen: set[str] = set()
        for service in ship_services:
            option_key = str(service.service_id)
            if option_key in seen:
                continue
            seen.add(option_key)
            options.append(
                self._serialize_ship_option(
                    order, service, quote_id=f"checkout_{option_key}"
                )
            )
        # Cheapest first so website checkout can auto-select the first Ship radio.
        options.sort(key=lambda item: (item.get("price") is None, item.get("price") or 0.0))
        return options

    def list_pickup_options(self, order) -> list[dict[str, Any]]:
        order.ensure_one()
        partner = order.partner_shipping_id
        if not partner or not partner.zip or not partner.country_id:
            raise UserError(
                _("A delivery address with country and postal code is required for pickup.")
            )
        options = self._pickup_options_from_checkout(order)
        if options:
            return self._limit_pickup_options(order, options)
        branches = self._load_destination_branches(order)
        if not branches:
            raise UserError(
                _("No pickup points returned near %(zip)s. Try another postal code.")
                % {"zip": partner.zip}
            )
        rates_by_carrier = self._rates_by_carrier_for_branches(order, branches)
        options = []
        for branch in branches:
            service = rates_by_carrier.get(branch["carrier"])
            if not service:
                continue
            options.append(self._serialize_pickup_option(order, branch, service))
        if not options:
            raise UserError(
                _(
                    "No pickup rates available near %(zip)s. "
                    "Try Ship or another postal code."
                )
                % {"zip": partner.zip}
            )
        return self._limit_pickup_options(order, options)

    def _pickup_options_from_checkout(self, order) -> list[dict[str, Any]]:
        request = PayloadMapper.build_quote_request_from_sale_order(order)
        request.carriers = "all"
        request.expected_drop_off = None
        if not request.destination_contact:
            return []
        # Same probe as envia.quote.wizard: unlock ocurre rates + nested branches.
        request.destination_contact.branch_code = "PROBE"
        body = self._checkout_body(order.company_id, request, force_pickup=True)
        rates = EnviaOfficialAdapter._normalize_checkout_rates_body(body)
        options: list[dict[str, Any]] = []
        seen: set[tuple[str, str, str]] = set()
        service_by_carrier: dict[str, QuoteService] = {}
        carriers_needing_branches: set[str] = set()
        for rate in rates:
            if not isinstance(rate, dict):
                continue
            drop_off = rate.get("dropOff")
            try:
                drop_off = int(drop_off) if drop_off is not None else None
            except (TypeError, ValueError):
                drop_off = None
            # Destination pickup only (wizard dropOff 1/2). Ignore door-to-door.
            if drop_off not in PICKUP_DROP_OFFS:
                continue
            nested = rate.get("branches") if isinstance(rate.get("branches"), list) else []
            service = self._quote_service_from_rate(rate, request)
            carrier = service.carrier
            if nested:
                for entry in nested:
                    if not isinstance(entry, dict):
                        continue
                    branch = self._normalize_branch_entry(
                        entry,
                        carrier,
                        request.destination_country,
                    )
                    if not branch["branch_code"]:
                        continue
                    key = (carrier, branch["branch_code"], str(service.service_id))
                    if key in seen:
                        continue
                    seen.add(key)
                    options.append(self._serialize_pickup_option(order, branch, service))
                continue
            carriers_needing_branches.add(carrier)
            existing = service_by_carrier.get(carrier)
            if not existing or (service.price or 0.0) < (existing.price or 0.0):
                service_by_carrier[carrier] = service
        if carriers_needing_branches:
            # One branches round-trip for all ocurre carriers (avoid N+1).
            branches = self._load_destination_branches(
                order,
                carrier_codes=list(carriers_needing_branches),
            )
            for branch in branches:
                service = service_by_carrier.get(branch["carrier"])
                if not service:
                    continue
                key = (branch["carrier"], branch["branch_code"], str(service.service_id))
                if key in seen:
                    continue
                seen.add(key)
                options.append(self._serialize_pickup_option(order, branch, service))
        options.sort(
            key=lambda item: (
                float(item.get("distance") or 9999),
                item.get("price") or 0,
            )
        )
        return options

    def _checkout_body(self, company, request: QuoteRequest, *, force_pickup: bool = False):
        adapter = get_envia_adapter(company)
        if not adapter.shop_id:
            raise UserError(
                _("Envia Shop ID is missing. Reconnect the Envia.com integration.")
            )
        payload = EnviaOfficialAdapter._build_checkout_payload(request)
        if force_pickup or (
            request.destination_contact and request.destination_contact.branch_code
        ):
            extras = EnviaOfficialAdapter._build_additional_services(
                request.origin_contact,
                request.destination_contact,
                request.additional_services,
                expected_drop_off=2,
            )
            if extras:
                payload["additionalServices"] = extras
        return adapter.client._post(get_envia_checkout_path(adapter.shop_id), payload)

    @staticmethod
    def _quote_service_from_rate(rate: dict[str, Any], request: QuoteRequest) -> QuoteService:
        parsed = EnviaOfficialAdapter._parse_checkout_rates([rate], request)
        if not parsed:
            raise UserError(_("Could not parse Envia checkout rate."))
        return parsed[0]

    def apply_selection(self, order, payload: dict[str, Any]) -> dict[str, Any]:
        order.ensure_one()
        route_type = payload.get("route_type") or ROUTE_SHIP
        if route_type == ROUTE_PICKUP:
            if not order.company_id.envia_checkout_enable_pickup:
                raise UserError(_("Pickup is disabled for website checkout."))
            quote = self._apply_pickup_selection(order, payload)
        elif route_type == ROUTE_SHIP:
            quote = self._apply_ship_selection(order, payload)
        else:
            raise UserError(_("Unknown delivery route type: %s") % route_type)
        order._sync_envia_shipping_line(quote)
        service = quote.selected_service_id
        delivery = order.order_line.filtered("is_delivery")[:1]
        return {
            "quote_id": quote.id,
            "price": delivery.price_unit if delivery else order._envia_shipping_unit_price(quote),
            "currency": order.currency_id.name,
            "label": order._envia_delivery_line_description(quote),
            "carrier": service.carrier if service else False,
            "service": service.service_name if service else False,
        }

    def _apply_ship_selection(self, order, payload: dict[str, Any]):
        service_id = str(payload.get("service_id") or "")
        if not service_id:
            raise UserError(_("Select a shipping rate to continue."))
        # Use the listed checkout rate (no second Envia round-trip on click).
        service = self._service_from_listed_option(order, payload, drop_off=0)
        if not service:
            raise UserError(_("The selected shipping rate is no longer available."))
        request = PayloadMapper.build_quote_request_from_sale_order(order)
        request.carriers = "all"
        request.expected_drop_off = None
        if request.destination_contact:
            request.destination_contact.branch_code = None
        response = QuoteResponse(
            quote_id=f"checkout_ship_{service.service_id}",
            services=[service],
        )
        quote = self._create_quote(
            order,
            response,
            request,
            origin_location_type="address",
            destination_location_type="address",
        )
        self._select_service(quote, service)
        return quote

    def _apply_pickup_selection(self, order, payload: dict[str, Any]):
        branch_code = (payload.get("branch_code") or "").strip()
        carrier = (payload.get("carrier") or "").strip()
        if not branch_code or not carrier:
            raise UserError(_("Select a pickup location to continue."))
        drop_off = payload.get("drop_off")
        try:
            drop_off = int(drop_off) if drop_off not in (None, False, "") else 2
        except (TypeError, ValueError):
            drop_off = 2
        if drop_off not in PICKUP_DROP_OFFS:
            drop_off = 2
        service = self._service_from_listed_option(order, payload, drop_off=drop_off)
        if not service:
            raise UserError(_("No pickup rates available for the selected location."))
        branch = self._branch_from_payload(order, payload)
        request = self._build_pickup_quote_request(order, branch)
        request.expected_drop_off = None
        response = QuoteResponse(
            quote_id=f"checkout_pickup_{service.service_id}",
            services=[service],
        )
        quote = self._create_quote(
            order,
            response,
            request,
            origin_location_type="address",
            destination_location_type="branch",
            destination_branch_code=branch["branch_code"],
            destination_branch_name=branch.get("name"),
            destination_branch_street=branch.get("street"),
            destination_branch_number=branch.get("number"),
        )
        self._select_service(quote, service)
        return quote

    @staticmethod
    def _service_from_listed_option(
        order,
        payload: dict[str, Any],
        *,
        drop_off: int | None,
    ) -> QuoteService | None:
        """Build a QuoteService from the option the customer already saw (no API call)."""
        # Prefer raw Envia amount so list margins are not applied twice on select.
        price = payload.get("base_price")
        if price in (None, False, ""):
            price = payload.get("price")
        if price in (None, False, ""):
            return None
        try:
            price = float(price)
        except (TypeError, ValueError):
            return None
        carrier = (payload.get("carrier") or "").strip()
        service_id = str(payload.get("service_id") or "")
        if not service_id and not carrier:
            return None
        raw_envia_service_id = payload.get("envia_service_id")
        envia_service_id = None
        if raw_envia_service_id not in (None, False, ""):
            try:
                envia_service_id = int(raw_envia_service_id)
            except (TypeError, ValueError):
                envia_service_id = None
        return QuoteService(
            service_id=service_id or f"{carrier}:selected",
            carrier=carrier,
            carrier_name=(payload.get("carrier_name") or carrier),
            service_name=(
                payload.get("service")
                or payload.get("name")
                or service_id
                or carrier
            ),
            price=price,
            currency=order.currency_id.name,
            envia_service_id=envia_service_id,
            drop_off=drop_off,
        )

    def _create_quote(
        self,
        order,
        response: QuoteResponse,
        request: QuoteRequest,
        **location_values,
    ):
        company = order.company_id
        warehouse = order.warehouse_id
        origin_partner = (
            warehouse.partner_id if warehouse and warehouse.partner_id else None
        ) or company._envia_get_default_origin_partner()
        values = {
            "sale_order_id": order.id,
            "origin_partner_id": origin_partner.id if origin_partner else False,
            "destination_partner_id": order.partner_shipping_id.id,
            "origin_postal_code": request.origin_postal_code,
            "origin_country": request.origin_country,
            "origin_state": request.origin_state,
            "origin_city": (
                request.origin_contact.city if request.origin_contact else False
            ),
            "destination_postal_code": request.destination_postal_code,
            "destination_country": request.destination_country,
            "destination_state": request.destination_state,
            "destination_city": (
                request.destination_contact.city if request.destination_contact else False
            ),
            "weight": request.weight,
            "content": request.content,
            "declared_value": request.declared_value,
            "currency_id": order.currency_id.id,
            "carriers": request.carriers,
            "company_id": company.id,
            **location_values,
        }
        return self.env["envia.quote"].create_from_api_response(response, values)

    @staticmethod
    def _select_service(quote, service: QuoteService):
        line = quote.service_ids.filtered(
            lambda item: item.service_id == str(service.service_id)
        )[:1]
        if not line:
            line = quote.service_ids.filtered(
                lambda item: item.carrier == service.carrier
            ).sorted(key=lambda item: item.price or 0.0)[:1]
        if not line:
            raise UserError(_("Could not persist the selected Envia rate."))
        line.action_select_service()

    @staticmethod
    def _find_service(
        services: list[QuoteService],
        service_id: str,
        carrier: str | None,
    ) -> QuoteService | None:
        if service_id:
            for service in services:
                if str(service.service_id) == service_id:
                    return service
        if carrier:
            matches = [service for service in services if service.carrier == carrier]
            if matches:
                return min(matches, key=lambda item: item.price or 0.0)
        return None

    def _load_destination_branches(self, order, carrier_codes=None) -> list[dict[str, Any]]:
        company = order.company_id
        token = company._envia_get_shipping_api_token()
        if not token:
            raise UserError(_("Configure your Envia shipping API token in Settings first."))
        partner = order.partner_shipping_id
        country = partner.country_id
        zipcode = (partner.zip or "").strip()
        city = (partner.city or "").strip() or None
        state = partner.state_id
        envia_state = EnviaOfficialAdapter.envia_state_code(
            country.code,
            state.code if state else None,
        ) or None
        if carrier_codes is None:
            carrier_codes = self._branch_carrier_codes(country)
        elif isinstance(carrier_codes, str):
            carrier_codes = [carrier_codes]
        if not carrier_codes:
            raise UserError(_("No active Envia carriers are configured for this country."))
        client = EnviaClient(company._envia_get_base_url(), token)
        queries_base = company._envia_get_queries_base_url()
        merged: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for carrier_code in carrier_codes:
            try:
                entries = client.get_branches(
                    queries_base_url=queries_base,
                    carrier=carrier_code,
                    country_code=country.code,
                    zipcode=zipcode,
                    search_type=2,
                    city=city,
                    state_code=envia_state,
                )
            except UserError:
                continue
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                branch = self._normalize_branch_entry(entry, carrier_code, country.code)
                if not branch["branch_code"]:
                    continue
                key = (branch["carrier"], branch["branch_code"])
                if key in seen:
                    continue
                seen.add(key)
                merged.append(branch)
        merged.sort(
            key=lambda item: (
                float(item.get("distance") or 9999),
                item.get("carrier") or "",
            )
        )
        return merged[:BRANCH_LIMIT]

    def _rates_by_carrier_for_branches(
        self,
        order,
        branches: list[dict[str, Any]],
    ) -> dict[str, QuoteService]:
        proxy_by_carrier: dict[str, dict[str, Any]] = {}
        for branch in branches:
            carrier = branch["carrier"]
            if carrier and carrier not in proxy_by_carrier:
                proxy_by_carrier[carrier] = branch
        rates: dict[str, QuoteService] = {}
        for carrier, proxy in proxy_by_carrier.items():
            try:
                request = self._build_pickup_quote_request(order, proxy)
                request.expected_drop_off = None
                body = self._checkout_body(order.company_id, request, force_pickup=True)
                services = EnviaOfficialAdapter._parse_checkout_rates(body, request)
            except UserError:
                continue
            ocurre = [service for service in services if service.drop_off in PICKUP_DROP_OFFS]
            candidates = ocurre or services
            if not candidates:
                continue
            rates[carrier] = min(candidates, key=lambda item: item.price or 0.0)
        return rates

    def _build_pickup_quote_request(self, order, branch: dict[str, Any]) -> QuoteRequest:
        base = PayloadMapper.build_quote_request_from_sale_order(order)
        company = order.company_id
        partner = order.partner_shipping_id
        city = branch.get("city") or (
            base.destination_contact.city if base.destination_contact else ""
        )
        phone = branch.get("phone") or (
            partner.phone or getattr(partner, "mobile", None) if partner else None
        )
        email = branch.get("email") or (partner.email if partner else None)
        destination = PayloadMapper.build_branch_contact(
            branch.get("name"),
            branch.get("street") or "",
            city,
            branch.get("state_code") or (base.destination_state or ""),
            branch.get("zip") or base.destination_postal_code,
            branch.get("country_code") or base.destination_country,
            branch["branch_code"],
            company,
            phone=phone,
            email=email,
            number=branch.get("number"),
        )
        return PayloadMapper.build_quote_request_from_values(
            {
                "origin_postal_code": base.origin_postal_code,
                "origin_country": base.origin_country,
                "origin_state": base.origin_state,
                "destination_postal_code": destination.postal_code,
                "destination_country": destination.country,
                "destination_state": destination.state,
                "weight": base.weight,
                "weight_unit": base.weight_unit,
                "content": base.content,
                "declared_value": base.declared_value,
                "currency": base.currency,
                "carriers": branch["carrier"],
                "expected_drop_off": None,
                "origin_contact": base.origin_contact,
                "destination_contact": destination,
                "items": base.items,
            }
        )

    def _branch_from_payload(self, order, payload: dict[str, Any]) -> dict[str, Any]:
        branch_code = (payload.get("branch_code") or "").strip()
        carrier = (payload.get("carrier") or "").strip()
        partner = order.partner_shipping_id
        # Prefer explicit checkout payload (avoids a second branches API round-trip).
        if payload.get("name") or payload.get("street") or payload.get("address"):
            return {
                "branch_code": branch_code,
                "carrier": carrier,
                "name": payload.get("name") or branch_code,
                "street": payload.get("street") or payload.get("address") or "",
                "number": payload.get("number") or "",
                "city": payload.get("city") or (partner.city if partner else ""),
                "zip": payload.get("zip") or (partner.zip if partner else ""),
                "state_code": payload.get("state_code")
                or (partner.state_id.code if partner and partner.state_id else ""),
                "country_code": payload.get("country_code")
                or (partner.country_id.code if partner and partner.country_id else ""),
                "phone": payload.get("phone") or "",
                "email": payload.get("email") or "",
                "lat": payload.get("lat"),
                "lng": payload.get("lng"),
                "distance": payload.get("distance"),
            }
        try:
            for branch in self._load_destination_branches(order):
                if branch["branch_code"] == branch_code and branch["carrier"] == carrier:
                    return branch
        except UserError:
            pass
        return {
            "branch_code": branch_code,
            "carrier": carrier,
            "name": payload.get("name") or branch_code,
            "street": payload.get("street") or payload.get("address") or "",
            "number": payload.get("number") or "",
            "city": payload.get("city") or (partner.city if partner else ""),
            "zip": payload.get("zip") or (partner.zip if partner else ""),
            "state_code": payload.get("state_code")
            or (partner.state_id.code if partner and partner.state_id else ""),
            "country_code": payload.get("country_code")
            or (partner.country_id.code if partner and partner.country_id else ""),
            "phone": payload.get("phone") or "",
            "email": payload.get("email") or "",
            "lat": payload.get("lat"),
            "lng": payload.get("lng"),
            "distance": payload.get("distance"),
        }

    def _branch_carrier_codes(self, country) -> list[str]:
        carriers = self.env["envia.carrier"].search([("active", "=", True)])
        if country:
            country_code = country.code
            carriers = carriers.filtered(
                lambda carrier: not carrier.country_codes
                or country_code
                in [code.strip() for code in carrier.country_codes.split(",")]
            )
        return carriers.mapped("code")

    @staticmethod
    def _normalize_branch_entry(
        entry: dict[str, Any],
        carrier_code: str,
        country_code: str,
    ) -> dict[str, Any]:
        address = entry.get("address") if isinstance(entry.get("address"), dict) else {}
        lat = (
            entry.get("lat")
            or entry.get("latitude")
            or address.get("lat")
            or address.get("latitude")
            or (entry.get("location") or {}).get("latitude")
            or (entry.get("location") or {}).get("lat")
            or (address.get("location") or {}).get("latitude")
        )
        lng = (
            entry.get("lng")
            or entry.get("longitude")
            or address.get("lng")
            or address.get("longitude")
            or (entry.get("location") or {}).get("longitude")
            or (entry.get("location") or {}).get("lng")
            or (address.get("location") or {}).get("longitude")
        )
        try:
            lat = float(lat) if lat not in (None, False, "") else None
        except (TypeError, ValueError):
            lat = None
        try:
            lng = float(lng) if lng not in (None, False, "") else None
        except (TypeError, ValueError):
            lng = None
        name = (
            entry.get("reference")
            or entry.get("name")
            or entry.get("description")
            or entry.get("branch_id")
            or carrier_code
        )
        street = (
            address.get("address")
            or address.get("street")
            or entry.get("street")
            or ""
        )
        city = (
            address.get("city")
            or address.get("locality")
            or entry.get("city")
            or entry.get("locality")
            or ""
        )
        zipcode = (
            address.get("postalCode")
            or address.get("zipcode")
            or entry.get("zipcode")
            or entry.get("zip_code")
            or entry.get("postalCode")
            or ""
        )
        return {
            "branch_code": WebsitePickupService._extract_branch_code(entry),
            "carrier": carrier_code,
            "name": name,
            "street": street,
            "number": address.get("number") or entry.get("number") or "",
            "city": city,
            "zip": zipcode,
            "state_code": address.get("state") or entry.get("state") or entry.get("state_code") or "",
            "country_code": address.get("country") or entry.get("country_code") or country_code,
            "phone": entry.get("phone") or "",
            "email": entry.get("email") or "",
            "distance": entry.get("distance"),
            "lat": lat,
            "lng": lng,
            "address": WebsitePickupService._format_address(street, city, zipcode),
        }

    @staticmethod
    def _extract_branch_code(entry: dict[str, Any]) -> str:
        address = entry.get("address") if isinstance(entry.get("address"), dict) else {}
        candidates = (
            entry.get("branch_code"),
            entry.get("branchCode"),
            entry.get("code"),
            entry.get("reference"),
            address.get("branch_code"),
            address.get("branchCode"),
            address.get("code"),
            address.get("reference"),
            entry.get("branch_id"),
            entry.get("branchId"),
            entry.get("id"),
        )
        numeric_fallback = ""
        for candidate in candidates:
            if candidate in (None, False, ""):
                continue
            code = str(candidate).strip()
            if not code:
                continue
            if code.isdigit():
                if not numeric_fallback:
                    numeric_fallback = code
                continue
            return code
        return numeric_fallback

    @staticmethod
    def _format_address(street: str, city: str, zipcode: str) -> str:
        parts = [part for part in (street, city, zipcode) if part]
        return ", ".join(parts)

    @staticmethod
    def _limit_rates_per_carrier(
        options: list[dict[str, Any]],
        limit: int | None,
    ) -> list[dict[str, Any]]:
        """Keep at most ``limit`` unique branches per ``carrier`` code (e.g. paquetexpress)."""
        if not limit or limit <= 0:
            return options
        counts: dict[str, int] = {}
        seen_branches: set[tuple[str, str]] = set()
        limited: list[dict[str, Any]] = []
        for option in options:
            # Group by Envia carrier code from the rate ("carrier": "paquetexpress").
            carrier = (option.get("carrier") or "").strip().lower()
            branch_code = (option.get("branch_code") or "").strip()
            branch_key = (carrier, branch_code)
            if branch_code and branch_key in seen_branches:
                continue
            counts[carrier] = counts.get(carrier, 0) + 1
            if counts[carrier] > limit:
                continue
            if branch_code:
                seen_branches.add(branch_key)
            limited.append(option)
        return limited

    def _limit_pickup_options(self, order, options: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Cap pickup branches per carrier code, then overall BRANCH_LIMIT."""
        limited = self._limit_rates_per_carrier(
            options,
            order.company_id.envia_checkout_rates_per_carrier,
        )
        return limited[:BRANCH_LIMIT]

    def _priced_for_checkout(self, order, amount, currency_name=None) -> float:
        """Currency convert + fiscal + carrier margin/% (same as rate_shipment, no free_over)."""
        price = float(amount or 0.0)
        if currency_name:
            rate_currency = self.env["res.currency"].search(
                [("name", "=", currency_name)],
                limit=1,
            )
            if rate_currency and rate_currency != order.currency_id:
                price = rate_currency._convert(
                    price,
                    order.currency_id,
                    order.company_id,
                    fields.Date.context_today(order),
                )
        carrier = order._get_envia_delivery_carrier()
        if not carrier:
            return price
        company = carrier.company_id or order.company_id
        price = carrier.product_id._get_tax_included_unit_price(
            company,
            company.currency_id,
            order.date_order,
            "sale",
            fiscal_position=order.fiscal_position_id,
            product_price_unit=price,
            product_currency=order.currency_id,
        )
        return carrier._apply_margins(price, order)

    def _serialize_ship_option(
        self, order, service: QuoteService, *, quote_id: str
    ) -> dict[str, Any]:
        option_id = f"ship:{service.carrier}:{service.service_id}"
        eta = None
        if service.estimated_delivery_days:
            eta = f"{service.estimated_delivery_days} day(s)"
        base_price = float(service.price or 0.0)
        return {
            "id": option_id,
            "route_type": ROUTE_SHIP,
            "carrier": service.carrier,
            "carrier_name": service.carrier_name or service.carrier,
            "service": service.service_name,
            "service_id": str(service.service_id),
            "envia_service_id": service.envia_service_id,
            "name": f"{service.carrier_name or service.carrier} - {service.service_name}",
            "address": "",
            "base_price": base_price,
            "price": self._priced_for_checkout(order, base_price, service.currency),
            "currency": service.currency or order.currency_id.name,
            "eta": eta,
            "lat": None,
            "lng": None,
            "branch_code": "",
            "quote_id": quote_id,
            "drop_off": service.drop_off or 0,
        }

    def _serialize_pickup_option(
        self,
        order,
        branch: dict[str, Any],
        service: QuoteService,
    ) -> dict[str, Any]:
        option_id = f"pickup:{branch['carrier']}:{branch['branch_code']}:{service.service_id}"
        eta = None
        if service.estimated_delivery_days:
            eta = f"{service.estimated_delivery_days} day(s)"
        name = branch.get("name") or branch["branch_code"]
        if eta:
            name = f"{name} ({eta})"
        base_price = float(service.price or 0.0)
        return {
            "id": option_id,
            "route_type": ROUTE_PICKUP,
            "carrier": branch["carrier"],
            "carrier_name": service.carrier_name or branch["carrier"],
            "service": service.service_name,
            "service_id": str(service.service_id),
            "envia_service_id": service.envia_service_id,
            "name": name,
            "address": branch.get("address")
            or WebsitePickupService._format_address(
                branch.get("street") or "",
                branch.get("city") or "",
                branch.get("zip") or "",
            ),
            "street": branch.get("street") or "",
            "number": branch.get("number") or "",
            "city": branch.get("city") or "",
            "zip": branch.get("zip") or "",
            "state_code": branch.get("state_code") or "",
            "country_code": branch.get("country_code") or "",
            "base_price": base_price,
            "price": self._priced_for_checkout(order, base_price, service.currency),
            "currency": service.currency or order.currency_id.name,
            "eta": eta,
            "lat": branch.get("lat"),
            "lng": branch.get("lng"),
            "branch_code": branch["branch_code"],
            "distance": branch.get("distance"),
            "drop_off": service.drop_off or 2,
        }
