"""International shipping rules for Envia (customs / tax ids / export reason)."""

from __future__ import annotations

import re
from typing import Any, Iterable

from odoo.exceptions import UserError

from .i18n import _

EXPORT_REASON_FALLBACK: tuple[str, ...] = (
    "sale",
    "gift",
    "sample",
    "return",
    "personal effects",
    "donation",
    "temporary export",
    "repair",
    "company transfer",
)

# Plain str labels only — Odoo 19 Selection rejects LazyTranslate / non-str.
_EXPORT_REASON_LABELS: dict[str, str] = {
    "sale": "Sale",
    "gift": "Gift",
    "sample": "Sample",
    "return": "Return",
    "personal effects": "Personal effects",
    "donation": "Donation",
    "temporary export": "Temporary export",
    "repair": "Repair",
    "company transfer": "Company transfer",
}

DUTIES_PAYMENT_SELECTION: list[tuple[str, str]] = [
    ("sender", "Sender"),
    ("recipient", "Recipient"),
]


def is_international(origin_country: str | None, destination_country: str | None) -> bool:
    """True iff both ISO-2 codes are present and differ (case/trim insensitive)."""
    origin = (origin_country or "").strip().upper()
    destination = (destination_country or "").strip().upper()
    return bool(origin and destination and origin != destination)


def export_reason_label(name: str) -> str:
    text = (name or "").strip()
    if not text:
        return ""
    known = _EXPORT_REASON_LABELS.get(text.casefold())
    if known is not None:
        return known
    return text[:1].upper() + text[1:]


def export_reason_selection(_model=None) -> list[tuple[str, str]]:
    """Odoo Selection callable (receives model) or plain list via ``()``."""
    return [(name, export_reason_label(name)) for name in EXPORT_REASON_FALLBACK]


def normalize_export_reason(value: str | None, *, is_return: bool = False) -> str:
    text = (value or "").strip()
    if text:
        return text
    return "return" if is_return else "sale"


def parse_export_reasons_body(body: Any) -> list[dict[str, str]]:
    """Normalize Queries ``bol-export-reasons`` payload to ``{name, label}``."""
    rows: list[Any]
    if isinstance(body, list):
        rows = body
    elif isinstance(body, dict):
        data = body.get("data")
        rows = data if isinstance(data, list) else []
    else:
        rows = []
    reasons: list[dict[str, str]] = []
    for entry in rows:
        if isinstance(entry, str):
            name = entry.strip()
        elif isinstance(entry, dict):
            name = str(entry.get("name") or entry.get("value") or "").strip()
        else:
            continue
        if not name:
            continue
        reasons.append({"name": name, "label": str(export_reason_label(name))})
    return reasons


def fallback_export_reasons() -> list[dict[str, str]]:
    return [
        {"name": name, "label": str(export_reason_label(name))}
        for name in EXPORT_REASON_FALLBACK
    ]


def omit_empty(mapping: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in mapping.items() if value not in (None, False, "")}


def customs_item_from_shipment_item(
    item: Any,
    *,
    default_country: str | None,
) -> dict[str, Any]:
    description = (getattr(item, "description", None) or "").strip() or "Product"
    quantity = int(getattr(item, "quantity", 0) or 0)
    price = float(getattr(item, "price", 0) or 0)
    currency = (getattr(item, "currency", None) or "").strip()
    sku = (getattr(item, "sku", None) or "").strip()
    weight = getattr(item, "weight", None)
    product_code = (getattr(item, "product_code", None) or "").strip()
    country = (
        (getattr(item, "country_of_manufacture", None) or "").strip().upper()
        or (default_country or "").strip().upper()
    )
    payload = {
        "description": description,
        "quantity": max(quantity, 1),
        "price": price,
        "currency": currency,
        "sku": sku,
        "weight": float(weight) if weight not in (None, False, "") else None,
        "productCode": product_code,
        "countryOfManufacture": country,
    }
    return omit_empty(payload)


def validate_customs_items(items: Iterable[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    rows = list(items)
    if not rows:
        errors.append(_("International shipments require at least one customs item."))
        return errors
    for index, item in enumerate(rows, start=1):
        description = str(item.get("description") or "").strip()
        quantity = item.get("quantity") or 0
        price = float(item.get("price") or 0)
        product_code = str(item.get("productCode") or "").strip()
        country = str(item.get("countryOfManufacture") or "").strip()
        product_name = description or _("Product")
        if not description:
            errors.append(
                _("Product %(name)s needs a description.") % {"name": product_name}
            )
        try:
            qty = int(quantity)
        except (TypeError, ValueError):
            qty = 0
        if qty < 1:
            errors.append(
                _("Product %(name)s quantity must be at least 1.")
                % {"name": product_name}
            )
        if price <= 0:
            errors.append(
                _("Product %(name)s price must be greater than zero.")
                % {"name": product_name}
            )
        if not product_code:
            errors.append(
                _("Product %(name)s needs an Envia HS code (product code).")
                % {"name": product_name}
            )
        if not country:
            errors.append(
                _("Product %(name)s needs a country of manufacture.")
                % {"name": product_name}
            )
    return errors


def is_missing_product_code_error(text: str | None) -> bool:
    """True when Envia/Odoo messaging means HS ``productCode`` is missing."""
    folded = (text or "").casefold()
    compact = folded.replace(" ", "")
    if "productcode" in compact and "missing" in folded:
        return True
    if "hs code" in folded and "missing" in folded:
        return True
    if "needs an envia hs code" in folded:
        return True
    return False


def missing_product_code_names(text: str | None) -> list[str]:
    """Product names from Envia ``Missing for "…"`` fragments."""
    return [
        name.strip()
        for name in re.findall(r'Missing for\s+"([^"]+)"', text or "", flags=re.I)
        if name.strip()
    ]


def missing_product_code_user_message(product_names: Iterable[str] | None = None) -> str:
    """Client-facing copy when international HS codes are missing."""
    names = [str(name).strip() for name in (product_names or []) if str(name).strip()]
    if len(names) == 1:
        detail = _("Missing for: %(name)s") % {"name": names[0]}
    elif names:
        detail = _("Missing for: %(name)s") % {"name": ", ".join(names)}
    else:
        detail = _("One or more products are missing an HS code.")
    return _(
        "International shipments need an HS code (product code) on each product.\n"
        "%(detail)s\n\n"
        "Add the HS codes in Envia Products (bulk edit), then generate the label again."
    ) % {"detail": detail}


def validate_international_generate(
    *,
    origin_tax_id: str | None,
    destination_tax_id: str | None,
    export_reason: str | None,
    customs_items: Iterable[dict[str, Any]],
    declared_value: float | None,
) -> None:
    """Raise UserError when international generate prerequisites are missing."""
    errors: list[str] = []
    if not (origin_tax_id or "").strip():
        errors.append(_("Origin tax ID is required for international shipments."))
    if not (destination_tax_id or "").strip():
        errors.append(_("Destination tax ID is required for international shipments."))
    if not (export_reason or "").strip():
        errors.append(_("Export reason is required for international shipments."))
    if declared_value is None or float(declared_value) <= 0:
        errors.append(_("Declared value must be greater than zero for international shipments."))
    errors.extend(validate_customs_items(customs_items))
    if errors:
        raise UserError("\n".join(errors))


def build_label_create_customs_payload(
    *,
    origin_tax_id: str | None,
    destination_tax_id: str | None,
    export_reason: str | None,
    duties_payment_entity: str | None = None,
) -> dict[str, Any]:
    """Extra keys for ecommerce ``label/create`` (omit empty tax ids).

    ``duties_payment_entity`` is accepted for call-site compat but never sent —
    Envia resolves duties payment per carrier.
    """
    del duties_payment_entity  # ponytail: Envia picks duties; sending SENDER 422s.
    payload: dict[str, Any] = omit_empty(
        {
            "identification_number_origin": (origin_tax_id or "").strip(),
            "identification_number_destination": (destination_tax_id or "").strip(),
        }
    )
    reason = (export_reason or "").strip()
    if reason:
        payload["customs_settings"] = {"exportReason": reason}
    return payload
