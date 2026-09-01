from odoo.addons.envia.services.dto import Contact, QuoteRequest, ShipmentItem
from odoo.addons.envia.services.envia_official_adapter import EnviaOfficialAdapter
from odoo.addons.envia.services.payload_mapper import PayloadMapper


def test_build_quote_request_from_values():
    request = PayloadMapper.build_quote_request_from_values(
        {
            "origin_postal_code": "06500",
            "origin_country": "MX",
            "destination_postal_code": "28001",
            "destination_country": "ES",
            "weight": 2.5,
            "content": "Electronics",
            "declared_value": 1500,
            "currency": "MXN",
        }
    )
    assert request.origin_postal_code == "06500"
    assert request.weight == 2.5
    assert request.declared_value == 1500


def test_normalize_package_content_truncates_to_envia_limit():
    long_content = "Classic Brown Jacket " * 10
    normalized = PayloadMapper.normalize_package_content(long_content)
    assert len(normalized) <= PayloadMapper.PACKAGE_CONTENT_MAX_LENGTH


def test_normalize_package_weight():
    assert PayloadMapper.normalize_package_weight(0.0) == 1.0
    assert PayloadMapper.normalize_package_weight(0.05) == 1.0
    assert PayloadMapper.normalize_package_weight(0.10) == 0.10
    assert PayloadMapper.normalize_package_weight(2.5) == 2.5


def test_missing_weight_warning_for_zero_or_empty_product_weight():
    missing = type("Product", (), {"weight": 0.0})()
    present = type("Product", (), {"weight": 1.25})()
    assert PayloadMapper.products_missing_weight([missing, present]) == [missing]
    assert PayloadMapper.missing_weight_warning([present]) is False
    warning = str(PayloadMapper.missing_weight_warning([missing]))
    assert "no weight" in warning
    assert "default package values" in warning


def test_uom_to_envia_weight_unit_maps_known_uoms():
    kg = type(
        "Uom",
        (),
        {"id": 1, "name": "kg", "get_external_id": lambda self: {1: "uom.product_uom_kgm"}},
    )()
    lb = type(
        "Uom",
        (),
        {"id": 2, "name": "lb", "get_external_id": lambda self: {2: "uom.product_uom_lb"}},
    )()
    unknown = type(
        "Uom",
        (),
        {"id": 3, "name": "stone", "get_external_id": lambda self: {3: "uom.custom_stone"}},
    )()
    assert PayloadMapper._uom_to_envia_weight_unit(kg) == "KG"
    assert PayloadMapper._uom_to_envia_weight_unit(lb) == "LB"
    assert PayloadMapper._uom_to_envia_weight_unit(unknown) is None
    assert PayloadMapper._uom_to_envia_weight_unit(None) is None


def test_build_checkout_payload_uses_request_weight_unit():
    request = QuoteRequest(
        origin_postal_code="67192",
        origin_country="MX",
        destination_postal_code="03100",
        destination_country="MX",
        weight=1.0,
        content="Package",
        weight_unit="LB",
    )
    payload = EnviaOfficialAdapter._build_checkout_payload(request)
    assert payload["package"]["weightUnit"] == "LB"


def test_build_quote_request_from_values_normalizes_low_weight():
    request = PayloadMapper.build_quote_request_from_values(
        {
            "origin_postal_code": "06500",
            "origin_country": "MX",
            "destination_postal_code": "03100",
            "destination_country": "MX",
            "weight": 0.05,
            "content": "Electronics",
            "declared_value": 1500,
            "currency": "MXN",
        }
    )
    assert request.weight == 1.0


def test_checkout_item_product_id_uses_product_product_id():
    request = QuoteRequest(
        origin_postal_code="67192",
        origin_country="MX",
        destination_postal_code="03100",
        destination_country="MX",
        weight=1.0,
        content="Package",
    )
    item = ShipmentItem(
        description="Jacket",
        quantity=1.0,
        price=100.0,
        currency="MXN",
        sku="JKT-001",
        product_id=52,
    )
    checkout_item = EnviaOfficialAdapter._checkout_item_from_shipment_item(
        item, request, 0
    )
    assert checkout_item["productId"] == "52"
    assert checkout_item["width"] is None
    assert checkout_item["height"] is None
    assert checkout_item["length"] is None


def test_build_package_dimensions_payload_from_items():
    items = [
        ShipmentItem(
            description="Classic Leather Belt",
            quantity=1,
            price=100.0,
            currency="MXN",
            weight=0.5,
            product_id=82,
        ),
        ShipmentItem(
            description="Australian healing clay ",
            quantity=2.0,
            price=50.0,
            currency="MXN",
            weight=1.0,
            product_id=463,
        ),
    ]
    payload = EnviaOfficialAdapter.build_package_dimensions_payload(items, "MXN")
    assert payload == {
        "items": [
            {
                "productId": "82",
                "variantId": None,
                "name": "Classic Leather Belt",
                "quantity": 1,
            },
            {
                "productId": "463",
                "variantId": None,
                "name": "Australian healing clay ",
                "quantity": 2,
            },
        ],
        "currency": "MXN",
    }


def test_format_package_dimensions_preview():
    body = {
        "success": True,
        "packages": [
            {
                "name": "Package",
                "height": 17,
                "width": 17,
                "length": 17,
                "weight": 1,
                "content": "Classic Leather Belt",
                "length_unit": "CM",
                "weight_unit": "KG",
            }
        ],
        "message": "Package Default",
        "package_automatic": True,
    }
    preview = EnviaOfficialAdapter.format_package_dimensions_preview(body)
    assert "17x17x17 CM" in preview
    assert "1 KG" in preview
    assert "Classic Leather Belt" in preview


def test_format_package_dimensions_preview_lists_odoo_items():
    body = {
        "packages": [
            {
                "name": "Package",
                "height": 29,
                "width": 29,
                "length": 29,
                "weight": 5,
                "content": "Multiple products",
                "length_unit": "CM",
                "weight_unit": "KG",
            }
        ],
    }
    items = [
        ShipmentItem(
            description="Classic Leather Belt",
            quantity=1,
            price=10,
            currency="MXN",
            weight=0.5,
            product_id=82,
        ),
        ShipmentItem(
            description="Australian healing clay",
            quantity=2,
            price=20,
            currency="MXN",
            weight=1.0,
            product_id=463,
        ),
    ]
    preview = EnviaOfficialAdapter.format_package_dimensions_preview(body, items=items)
    assert preview == (
        "Package: 29x29x29 CM, 5 KG\n"
        "• Classic Leather Belt — 0.5 KG (Odoo)\n"
        "• Australian healing clay ×2 — 2 KG (Odoo)"
    )
    assert "Multiple products" not in preview


def test_package_dimensions_sync_hint_only_on_weight_mismatch():
    body_automatic = {
        "packages": [{"weight": 1}],
        "package_automatic": True,
        "message": "Package Default",
    }
    # Envia default flags alone must not warn when weights match.
    assert (
        EnviaOfficialAdapter.package_dimensions_sync_hint(
            body_automatic,
            odoo_weight=1.0,
        )
        == ""
    )

    body_mismatch = {
        "packages": [{"weight": 1}],
        "package_automatic": False,
        "message": "Custom",
    }
    items = [
        ShipmentItem(
            description="Belt",
            quantity=1,
            price=10,
            currency="MXN",
            weight=2.5,
            product_id=1,
        )
    ]
    hint_mismatch = EnviaOfficialAdapter.package_dimensions_sync_hint(
        body_mismatch,
        items=items,
    )
    assert "sync package dimensions" in hint_mismatch.lower()
    assert "2.5" in hint_mismatch

    body_ok = {
        "packages": [{"weight": 2.5}],
        "package_automatic": True,
        "message": "Package Default",
    }
    assert (
        EnviaOfficialAdapter.package_dimensions_sync_hint(
            body_ok,
            items=items,
        )
        == ""
    )


def test_build_checkout_payload_uses_null_dimensions_when_missing():
    request = QuoteRequest(
        origin_postal_code="67192",
        origin_country="MX",
        destination_postal_code="03100",
        destination_country="MX",
        weight=1.0,
        content="Package",
    )
    payload = EnviaOfficialAdapter._build_checkout_payload(request)
    assert payload["package"]["dimensions"] == {
        "length": None,
        "width": None,
        "height": None,
    }
    assert payload["package"]["lengthUnit"] is None
    assert payload["package"]["weightUnit"] is None
    assert payload["items"][0]["width"] is None
    assert payload["items"][0]["height"] is None
    assert payload["items"][0]["length"] is None


def test_partner_address_extras_from_street2_number():
    partner = type("Partner", (), {"street2": "123", "country_id": False})()
    number, district, interior = PayloadMapper._partner_address_extras(partner)
    assert number == "123"
    assert district is None
    assert interior is None


def test_partner_address_extras_from_street2_district():
    partner = type("Partner", (), {"street2": "Centro", "country_id": False})()
    number, district, interior = PayloadMapper._partner_address_extras(partner)
    assert number is None
    assert district == "Centro"
    assert interior is None


def test_checkout_address_includes_district():
    contact = EnviaOfficialAdapter._contact_to_checkout_address(
        Contact(
            name="Shipper",
            street="Av Reforma",
            number="123",
            district="Juarez",
            city="Ciudad de Mexico",
            state="CX",
            postal_code="06600",
            country="MX",
            phone="5555555555",
            email="ship@example.com",
        )
    )
    assert contact["number"] == "123"
    assert contact["district"] == "Juarez"


def test_checkout_origin_uses_address_id_when_present():
    contact = Contact(
        name="Shipper",
        street="Av Reforma",
        city="Ciudad de Mexico",
        state="CX",
        postal_code="06600",
        country="MX",
        phone="5555555555",
        email="ship@example.com",
        address_id="7295564",
    )
    assert EnviaOfficialAdapter._contact_to_checkout_address(contact) == {
        "address_id": "7295564"
    }
    assert EnviaOfficialAdapter._contact_to_official_address(contact) == {
        "address_id": "7295564"
    }


def test_official_address_includes_district_fallback():
    address = EnviaOfficialAdapter._contact_to_official_address(
        Contact(
            name="Shipper",
            street="Av Reforma",
            number="123",
            district="Nuevo León",
            city="Guadalupe",
            state="NL",
            postal_code="67192",
            country="MX",
            phone="5555555555",
            email="ship@example.com",
        )
    )
    assert address["district"] == "Nuevo León"


def test_resolve_district_falls_back_to_state_name():
    state = type("State", (), {"name": "Ciudad de México"})()
    assert PayloadMapper._resolve_district(state=state) == "Ciudad de México"
    assert PayloadMapper._resolve_district(district="Centro", state=state) == "Centro"


def test_build_checkout_payload_normalizes_package_content():
    request = QuoteRequest(
        origin_postal_code="67192",
        origin_country="MX",
        destination_postal_code="03100",
        destination_country="MX",
        weight=1.0,
        content="Classic Brown Jacket " * 10,
    )
    payload = EnviaOfficialAdapter._build_checkout_payload(request)
    assert len(payload["package"]["content"]) <= PayloadMapper.PACKAGE_CONTENT_MAX_LENGTH
