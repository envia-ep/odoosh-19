import re

from odoo import _
from odoo.exceptions import UserError

from ..services.dto import Contact, QuoteRequest, ShipmentItem
from ..services.envia_client import EnviaClient
from ..services.envia_geocodes_client import EnviaGeocodesClient
from ..services.envia_official_adapter import EnviaOfficialAdapter


class PayloadMapper:
    PACKAGE_CONTENT_MAX_LENGTH = 100
    DEFAULT_PACKAGE_WEIGHT = 1.0
    MIN_PACKAGE_WEIGHT = 0.10

    @staticmethod
    def normalize_package_weight(weight):
        if not weight or weight < PayloadMapper.MIN_PACKAGE_WEIGHT:
            return PayloadMapper.DEFAULT_PACKAGE_WEIGHT
        return weight

    @staticmethod
    def envia_weight_unit(env) -> str | None:
        """Map Odoo product weight UoM to Envia weightUnit (KG/LB)."""
        Product = env["product.template"]
        uom = Product._get_weight_uom_id_from_ir_config_parameter()
        return PayloadMapper._uom_to_envia_weight_unit(uom)

    @staticmethod
    def _uom_to_envia_weight_unit(uom) -> str | None:
        if not uom:
            return None
        xmlids = uom.get_external_id()
        xmlid = (xmlids.get(uom.id) or "").casefold()
        if xmlid.endswith("product_uom_kgm") or xmlid.endswith("uom_kgm"):
            return "KG"
        if xmlid.endswith("product_uom_lb") or xmlid.endswith("uom_lb"):
            return "LB"
        name = (uom.name or "").casefold()
        if "kg" in name or "kilo" in name:
            return "KG"
        if "lb" in name or "pound" in name or "libra" in name:
            return "LB"
        return None

    @staticmethod
    def sale_order_raw_weight(order):
        return sum(
            (line.product_id.weight or 0.0) * line.product_uom_qty
            for line in PayloadMapper.merchandise_order_lines(order)
        )

    @staticmethod
    def sale_order_package_weight(order):
        return PayloadMapper.normalize_package_weight(
            PayloadMapper.sale_order_raw_weight(order)
        )

    @staticmethod
    def products_missing_weight(products):
        return [product for product in products if product and not (product.weight or 0.0)]

    @staticmethod
    def quote_context_products(sale_order=None, picking=None):
        if sale_order:
            return PayloadMapper.merchandise_order_lines(sale_order).mapped("product_id")
        if picking:
            return picking.move_ids.filtered(
                lambda move: move.product_id and move.state != "cancel"
            ).mapped("product_id")
        return []

    @staticmethod
    def missing_weight_warning(products):
        if not PayloadMapper.products_missing_weight(products):
            return False
        return _(
            "One or more products have no weight. You can still get a quote; "
            "Envia will use its default package values because product weight is missing."
        )

    @staticmethod
    def envia_shipping_product(env):
        try:
            return env.ref("envia.product_envia_shipping")
        except ValueError:
            return env["product.product"]

    @staticmethod
    def merchandise_order_lines(order):
        shipping_product = PayloadMapper.envia_shipping_product(order.env)
        shipping_id = shipping_product.id if shipping_product else False
        return order.order_line.filtered(
            lambda line: not line.display_type
            and (not shipping_id or line.product_id.id != shipping_id)
        )

    @staticmethod
    def normalize_package_content(content, max_length=PACKAGE_CONTENT_MAX_LENGTH):
        if not content:
            return "General merchandise"
        normalized = " ".join(str(content).split())
        if len(normalized) <= max_length:
            return normalized
        trimmed = normalized[:max_length].rstrip(" ,.;:-")
        return trimmed or "General merchandise"

    @staticmethod
    def sale_order_package_content(order):
        names = PayloadMapper.merchandise_order_lines(order).mapped("product_id.display_name")
        content = ", ".join(name for name in names if name) or "General merchandise"
        return PayloadMapper.normalize_package_content(content)

    @staticmethod
    def sale_order_declared_value(order):
        return sum(PayloadMapper.merchandise_order_lines(order).mapped("price_subtotal"))

    @staticmethod
    def build_branch_contact(
        branch_name,
        street,
        city,
        state_code,
        postal_code,
        country_code,
        branch_code,
        company,
        phone=None,
        email=None,
        number=None,
    ) -> Contact:
        company_partner = company.partner_id
        return Contact(
            name=branch_name or "Pickup",
            street=street or branch_name or "Branch",
            number=number or None,
            district=None,
            city=city or "",
            state=EnviaOfficialAdapter.envia_state_code(country_code, state_code),
            postal_code=postal_code or "",
            country=country_code or "",
            phone=phone or company_partner.phone or "5555555555",
            email=email or company_partner.email or "shipping@company.com",
            branch_code=branch_code or None,
        )

    @staticmethod
    def _partner_field_value(partner, attr):
        value = getattr(partner, attr, None)
        if not value:
            return ""
        if hasattr(value, "name"):
            return str(value.name).strip()
        return str(value).strip()

    @staticmethod
    def _resolve_district(district=None, state=None, partner=None):
        if district:
            return district
        if partner:
            _, from_partner, _ = PayloadMapper._partner_address_extras(partner)
            if from_partner:
                return from_partner
            if partner.state_id:
                return partner.state_id.name or None
        if state and getattr(state, "name", None):
            return state.name
        return None

    @staticmethod
    def _partner_address_extras(partner):
        """Optional l10n street parts; street2 fallback when no dedicated field."""
        number = ""
        for attr in ("street_number", "l10n_mx_street_number"):
            number = PayloadMapper._partner_field_value(partner, attr)
            if number:
                break
        district = ""
        for attr in ("l10n_mx_edi_colony", "district"):
            district = PayloadMapper._partner_field_value(partner, attr)
            if district:
                break
        interior = ""
        for attr in ("street_number2", "l10n_mx_street_number2", "l10n_mx_edi_interior"):
            interior = PayloadMapper._partner_field_value(partner, attr)
            if interior:
                break
        street2 = (partner.street2 or "").strip()
        if street2:
            # ponytail: digits-first street2 → number; else colonia/district
            if not number and re.match(r"^\d", street2):
                number = street2
            elif not district:
                district = street2
        return number or None, district or None, interior or None

    @staticmethod
    def build_side_contact(
        partner,
        postal_code,
        city,
        country,
        state,
        company,
        street_number=None,
        district=None,
    ) -> Contact:
        contact = PayloadMapper.partner_to_contact(partner)
        contact.postal_code = postal_code or contact.postal_code
        contact.city = city or contact.city
        contact.country = country.code if country else contact.country
        contact.state = state.code if state else contact.state
        if street_number:
            contact.number = street_number
        resolved_district = PayloadMapper._resolve_district(
            district=district, state=state, partner=partner
        )
        if resolved_district:
            contact.district = resolved_district
        company_partner = company.partner_id
        if not contact.phone:
            contact.phone = company_partner.phone or "5555555555"
        if not contact.email:
            contact.email = company_partner.email or "shipping@company.com"
        missing = []
        if not contact.street:
            missing.append(_("street"))
        if not contact.city:
            missing.append(_("city"))
        if not contact.postal_code:
            missing.append(_("postal code"))
        if not contact.phone:
            missing.append(_("phone"))
        if not contact.email:
            missing.append(_("email"))
        if missing:
            raise UserError(
                _("Complete contact %(name)s before shipping: %(fields)s")
                % {"name": partner.name, "fields": ", ".join(missing)}
            )
        return contact

    @staticmethod
    def partner_to_contact(partner) -> Contact:
        if not partner:
            raise UserError(_("A partner is required to build the shipment contact."))
        street = partner.street or ""
        number, district, interior = PayloadMapper._partner_address_extras(partner)
        district = PayloadMapper._resolve_district(district=district, partner=partner)
        return Contact(
            name=partner.name or "",
            company=partner.commercial_company_name or partner.name,
            street=street,
            number=number,
            district=district,
            interior_number=interior,
            city=partner.city or "",
            state=partner.state_id.code if partner.state_id else "",
            postal_code=partner.zip or "",
            country=partner.country_id.code if partner.country_id else "",
            phone=partner.phone or getattr(partner, "mobile", "") or "",
            email=partner.email or "",
            identification_number=partner.vat or None,
        )

    @staticmethod
    def build_quote_request_from_sale_order(order) -> QuoteRequest:
        order.ensure_one()
        company = order.company_id
        warehouse = order.warehouse_id
        origin_partner = (
            warehouse.partner_id if warehouse and warehouse.partner_id else None
        ) or company._envia_get_default_origin_partner()
        destination_partner = order.partner_shipping_id
        if not destination_partner:
            raise UserError(_("The sales order needs a delivery address to quote Envia rates."))
        origin_country = origin_partner.country_id or company.country_id
        destination_country = destination_partner.country_id
        if not origin_country or not destination_country:
            raise UserError(_("Origin and delivery countries are required to quote Envia rates."))
        geocoder = EnviaGeocodesClient()
        origin_state = origin_partner.state_id
        if not origin_state and origin_partner.zip:
            origin_state = geocoder.resolve_state_from_postal_code(
                order.env,
                origin_country,
                origin_partner.zip,
            )
        destination_state = destination_partner.state_id
        if not destination_state and destination_partner.zip:
            destination_state = geocoder.resolve_state_from_postal_code(
                order.env,
                destination_country,
                destination_partner.zip,
            )
        mapper = PayloadMapper()
        origin_contact = mapper.build_side_contact(
            origin_partner,
            origin_partner.zip,
            origin_partner.city,
            origin_country,
            origin_state,
            company,
        )
        if warehouse:
            address_id = warehouse._envia_origin_address_id()
            if address_id:
                origin_contact.address_id = address_id
        destination_contact = mapper.build_side_contact(
            destination_partner,
            destination_partner.zip,
            destination_partner.city,
            destination_country,
            destination_state,
            company,
        )
        return mapper.build_quote_request_from_values(
            {
                "origin_postal_code": origin_contact.postal_code,
                "origin_country": origin_country.code,
                "origin_state": EnviaOfficialAdapter.envia_state_code(
                    origin_country.code,
                    origin_contact.state,
                ),
                "destination_postal_code": destination_contact.postal_code,
                "destination_country": destination_country.code,
                "destination_state": EnviaOfficialAdapter.envia_state_code(
                    destination_country.code,
                    destination_contact.state,
                ),
                "weight": PayloadMapper.sale_order_package_weight(order),
                "weight_unit": PayloadMapper.envia_weight_unit(order.env),
                "content": mapper.sale_order_package_content(order),
                "declared_value": mapper.sale_order_declared_value(order),
                "currency": order.currency_id.name,
                "carriers": company.envia_default_carriers or "all",
                "origin_contact": origin_contact,
                "destination_contact": destination_contact,
                "items": mapper.sale_lines_to_items(order),
            }
        )

    @staticmethod
    def build_quote_request_from_values(values: dict) -> QuoteRequest:
        return QuoteRequest(
            origin_postal_code=values["origin_postal_code"],
            origin_country=values["origin_country"],
            origin_state=values.get("origin_state") or None,
            destination_postal_code=values["destination_postal_code"],
            destination_country=values["destination_country"],
            destination_state=values.get("destination_state") or None,
            weight=float(
                PayloadMapper.normalize_package_weight(values.get("weight") or 0.0)
            ),
            weight_unit=values.get("weight_unit"),
            content=PayloadMapper.normalize_package_content(values["content"]),
            declared_value=(
                float(values["declared_value"])
                if values.get("declared_value")
                else None
            ),
            currency=values.get("currency") or "MXN",
            carriers=values.get("carriers") or "all",
            expected_drop_off=values.get("expected_drop_off"),
            origin_contact=values.get("origin_contact"),
            destination_contact=values.get("destination_contact"),
            items=values.get("items") or [],
            locale=values.get("locale") or "es_MX",
        )

    @staticmethod
    def quote_items_for_context(sale_order=None, picking=None) -> list[ShipmentItem]:
        if sale_order:
            return PayloadMapper.sale_lines_to_items(sale_order)
        if picking:
            return PayloadMapper.picking_moves_to_items(picking)
        return []

    @staticmethod
    def sale_lines_to_items(order) -> list[ShipmentItem]:
        items = []
        for line in PayloadMapper.merchandise_order_lines(order):
            items.append(
                ShipmentItem(
                    description=PayloadMapper.normalize_package_content(line.name),
                    quantity=line.product_uom_qty,
                    price=line.price_unit,
                    currency=order.currency_id.name,
                    weight=line.product_id.weight or None,
                    sku=line.product_id.default_code or None,
                    product_id=line.product_id.id,
                    product_code=getattr(line.product_id, "envia_product_code", None),
                    country_of_manufacture=PayloadMapper._product_country_of_origin(
                        line.product_id
                    ),
                )
            )
        return items

    @staticmethod
    def picking_moves_to_items(picking) -> list[ShipmentItem]:
        items = []
        for move in picking.move_ids.filtered(
            lambda line: line.product_id and line.state != "cancel"
        ):
            product = move.product_id
            items.append(
                ShipmentItem(
                    description=PayloadMapper.normalize_package_content(product.display_name),
                    quantity=move.product_uom_qty,
                    price=product.lst_price or 0.0,
                    currency=picking.company_id.currency_id.name,
                    weight=product.weight or None,
                    sku=product.default_code or None,
                    product_id=product.id,
                    product_code=getattr(product, "envia_product_code", None),
                    country_of_manufacture=PayloadMapper._product_country_of_origin(product),
                )
            )
        return items

    @staticmethod
    def _product_country_of_origin(product) -> str | None:
        country = getattr(product, "country_of_origin", None)
        if country:
            return country.code
        template_country = getattr(product.product_tmpl_id, "country_of_origin", None)
        return template_country.code if template_country else None


def get_envia_adapter(company):
    company.ensure_one()
    token = company._envia_get_shipping_api_token()
    if not token:
        raise UserError(
            _(
                "Paste your Envia shipping API token in Settings > Envia Shipping > "
                "API Connection. Sandbox tokens come from "
                "https://shipping-test.envia.com/settings/developers"
            )
        )
    shop_id = (company.envia_shop_id or "").strip()
    if not shop_id:
        raise UserError(
            _(
                "Envia Shop ID is missing. Open Settings > Envia.com and complete "
                "the integration connection with Envia.com."
            )
        )
    client = EnviaClient(company._envia_get_base_url(), token)
    checkout_action = company.env.ref(
        "envia.action_envia_open_checkout_settings",
        raise_if_not_found=False,
    )
    label_action = company.env.ref(
        "envia.action_envia_open_shipping_rules_settings",
        raise_if_not_found=False,
    )
    return EnviaOfficialAdapter(
        client,
        shop_id=shop_id,
        default_carriers=company.envia_default_carriers or "dhl,fedex,estafeta",
        checkout_settings_action_id=checkout_action.id if checkout_action else None,
        label_settings_action_id=label_action.id if label_action else None,
    )
