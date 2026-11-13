"""Origin drop-off branch rules (rate dropOff 1 and 3). No ORM."""

import math
import unicodedata
from difflib import SequenceMatcher

_BLANK_POSTALS = {"", "00000"}
_COORD_COUNTRIES = {"FR", "IT", "BR"}


def needs_origin_branch(carrier, drop_off):
    return bool(carrier) and int(drop_off or 0) in (1, 3)


def is_destination_branch(drop_off):
    return int(drop_off or 0) in (2, 3)


def _code(value):
    if value in (None, False):
        return ""
    text = str(value).strip()
    return text


def branch_codes_for_payload(drop_off, origin_code, destination_code):
    """Codes to send. dropOff 0 never includes a branch code."""
    drop_off = int(drop_off or 0)
    origin = _code(origin_code)
    destination = _code(destination_code)
    payload = {}
    if drop_off in (1, 3) and origin:
        payload["origin"] = origin
    if drop_off in (2, 3) and destination:
        payload["destination"] = destination
    return payload


def label_create_branch_payload(drop_off, origin_code, destination_code):
    """label/create body fragments: ``{"origin": {"branchCode": "…"}}``."""
    return {
        side: {"branchCode": code}
        for side, code in branch_codes_for_payload(
            drop_off, origin_code, destination_code
        ).items()
    }


def missing_branch_side(drop_off, origin_code, destination_code, carrier):
    """Return 'origin' or 'destination' when generate must stop."""
    drop_off = int(drop_off or 0)
    if needs_origin_branch(carrier, drop_off) and not _code(origin_code):
        return "origin"
    if is_destination_branch(drop_off) and not _code(destination_code):
        return "destination"
    return ""


def choose_route(service, carrier_id, branches_by_address):
    if "oxxo" in str(service or "").lower():
        return "oxxo"
    try:
        carrier_num = int(carrier_id or 0)
    except (TypeError, ValueError):
        carrier_num = 0
    if carrier_num > 0 and branches_by_address:
        return "address"
    return "catalog"


def optional_postal(value):
    text = _code(value)
    if text in _BLANK_POSTALS:
        return None
    return text


def numeric_coord(value):
    if isinstance(value, bool) or isinstance(value, (list, dict)):
        return None
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def entry_coords(entry, address=None):
    """Lat/lng from Envia branch payloads (same keys as website pickup)."""
    if not isinstance(entry, dict):
        return None, None
    address = address if isinstance(address, dict) else {}
    location = entry.get("location") if isinstance(entry.get("location"), dict) else {}
    address_location = (
        address.get("location") if isinstance(address.get("location"), dict) else {}
    )
    lat = numeric_coord(
        entry.get("latitude")
        or entry.get("lat")
        or address.get("latitude")
        or address.get("lat")
        or location.get("latitude")
        or location.get("lat")
        or address_location.get("latitude")
        or address_location.get("lat")
    )
    lng = numeric_coord(
        entry.get("longitude")
        or entry.get("lng")
        or address.get("longitude")
        or address.get("lng")
        or location.get("longitude")
        or location.get("lng")
        or address_location.get("longitude")
        or address_location.get("lng")
    )
    return lat, lng


def _branch_code(entry, oxxo):
    if not isinstance(entry, dict):
        return ""
    if oxxo:
        code = _code(entry.get("codigo"))
        if code:
            return code
    for key in ("branch_code", "branchCode", "code", "id", "codigo"):
        code = _code(entry.get(key))
        if code:
            return code
    return ""


def _label(code, name, street, city, postal):
    place = ", ".join(part for part in (street, city, postal) if part)
    title = f"{code} - {name}" if name else code
    return f"{title} — {place}" if place else title


def normalize_branches(raw, *, oxxo=False, reservation_code=None):
    if isinstance(raw, dict):
        reservation_code = reservation_code or raw.get("reservation_code")
        data = raw.get("data") if isinstance(raw.get("data"), list) else raw.get("branches")
        raw = data if isinstance(data, list) else []
    branches = []
    seen = set()
    for entry in raw or []:
        if not isinstance(entry, dict):
            continue
        if oxxo and reservation_code and not entry.get("reservation_code"):
            entry = {**entry, "reservation_code": reservation_code}
        code = _branch_code(entry, oxxo)
        if not code or code in seen:
            continue
        seen.add(code)
        address = entry.get("address") if isinstance(entry.get("address"), dict) else {}
        name = _code(entry.get("name") or entry.get("reference") or address.get("name"))
        street = _code(
            entry.get("street") or address.get("street") or address.get("address")
        )
        city = _code(entry.get("city") or entry.get("locality") or address.get("city"))
        locality = _code(entry.get("locality") or address.get("locality") or city)
        state = _code(entry.get("state") or address.get("state"))
        postal = _code(
            entry.get("postalCode")
            or entry.get("zipcode")
            or address.get("postalCode")
            or address.get("zipcode")
        )
        lat, lng = entry_coords(entry, address)
        label = _label(code, name, street, city, postal)
        search = " ".join(
            part for part in (label, code, name, street, city, postal) if part
        ).lower()
        branches.append(
            {
                "code": code,
                "name": name,
                "label": label,
                "address_line": street,
                "street": street,
                "city": city,
                "locality": locality,
                "state": state,
                "postal_code": postal,
                "distance": numeric_coord(entry.get("distance")),
                "latitude": lat,
                "longitude": lng,
                "search": search,
            }
        )
    return branches


def catalog_package(weight, *, length=10, height=10, width=10, amount=1, package_type=1):
    """Package row for Queries branches catalog / by-address."""
    return {
        "weight": float(weight or 1),
        "length": float(length or 10),
        "height": float(height or 10),
        "width": float(width or 10),
        "amount": int(amount or 1),
        "package_type": int(package_type or 1),
    }


def catalog_body(country, postal, state, locality, branch_type, latitude, longitude, address, packages):
    """POST {QUERIES}/branches/{carrier}/{country} body."""
    body = {"type": 1, "shipmentType": 1, "packages": packages or []}
    filters = False
    if _code(state):
        body["state"] = _code(state)
        filters = True
    if _code(locality):
        body["locality"] = _code(locality)
        filters = True
    if branch_type and int(branch_type) > 0:
        body["branchType"] = int(branch_type)
        filters = True
    if not filters:
        body["allBranch"] = True
    lat = numeric_coord(latitude)
    lng = numeric_coord(longitude)
    if lat is not None and lng is not None:
        body["latitude"] = lat
        body["longitude"] = lng
    # zipcode / address only for FR, IT, BR
    if (country or "").upper() in _COORD_COUNTRIES:
        zipcode = optional_postal(postal)
        if zipcode:
            body["zipcode"] = zipcode
        if _code(address) and (lat is None or lng is None):
            body["address"] = _code(address)
    return body


def address_body(
    carrier_id,
    country,
    service_name,
    packages,
    *,
    zipcode=None,
    street=None,
    city=None,
    state=None,
    branch_type=None,
    latitude=None,
    longitude=None,
    capacity=1,
):
    """POST {APP}/ship/branchesbyaddress body."""
    body = {
        "carrierId": int(carrier_id or 0),
        "countryCodeCoverage": (country or "").upper(),
        "serviceName": _code(service_name) or "ground",
        "capacity": int(capacity or 1),
        "shipmentType": 1,
        "packages": packages or [],
    }
    if optional_postal(zipcode):
        body["zipCodeCoverage"] = optional_postal(zipcode)
    if _code(street):
        body["streetCoverage"] = _code(street)
    if _code(city):
        body["cityCoverage"] = _code(city)
    if _code(state):
        body["stateCoverage"] = _code(state)
    if branch_type and int(branch_type) > 0:
        body["branchType"] = int(branch_type)
    lat = numeric_coord(latitude)
    lng = numeric_coord(longitude)
    if lat is not None and lng is not None:
        body["latitude"] = lat
        body["longitude"] = lng
    return body


def cache_key(carrier, service, drop_off):
    return f"{carrier}|{service}|{int(drop_off or 0)}"


def should_reload(previous_key, key, filters_changed, has_branches):
    if previous_key != key:
        return True
    if filters_changed:
        return True
    return not has_branches


def _edit_distance(left, right):
    return 1 - SequenceMatcher(None, left, right).ratio()


def filter_branches(branches, query):
    query = (query or "").strip().lower()
    if not query:
        return list(branches)
    matched = [
        branch
        for branch in branches
        if query in (branch.get("search") or "")
        or query in (branch.get("code") or "").lower()
    ]
    if matched:
        return matched
    if len(query) >= 4 and query.isdigit():
        ranked = sorted(
            branches,
            key=lambda branch: _edit_distance(query, (branch.get("postal_code") or "")[: len(query)]),
        )
        return ranked[:1] or list(branches)
    ranked = sorted(
        branches,
        key=lambda branch: min(
            _edit_distance(query, (branch.get("code") or "").lower()),
            _edit_distance(query, (branch.get("name") or "").lower()),
        ),
    )
    return ranked[:1] or list(branches)


def pick_branch(branches, forced_code, current_code, preferred_code):
    codes = {branch["code"]: branch for branch in branches}
    for code in (forced_code, current_code, preferred_code):
        code = _code(code)
        if code and code in codes:
            return codes[code]
    return branches[0] if branches else None


def _norm_text(value):
    text = _code(value).lower()
    text = unicodedata.normalize("NFD", text)
    return "".join(ch for ch in text if unicodedata.category(ch) != "Mn")


def _norm_street(value):
    return " ".join(_norm_text(value).replace("'", "").split())


def _street_first(branches, street):
    want = _norm_street(street)
    if not want:
        return list(branches)
    winners = [branch for branch in branches if _norm_street(branch.get("street")) == want]
    if not winners:
        return list(branches)
    seen = {id(branch) for branch in winners}
    return winners + [branch for branch in branches if id(branch) not in seen]


def rank_catalog(branches, origin):
    """Reorder catalog branches by address proximity (not GPS)."""
    items = list(branches or [])
    if not items:
        return items
    origin = origin or {}
    postal = _norm_text(origin.get("postal_code"))
    matched = None
    if postal:
        exact = [
            branch
            for branch in items
            if _norm_text(branch.get("postal_code")) == postal
        ]
        if exact:
            matched = exact
        elif len(postal) >= 3:
            head = postal[:3]
            prefix = [
                branch
                for branch in items
                if _norm_text(branch.get("postal_code")).startswith(head)
            ]
            if prefix:
                matched = prefix
    if matched is None:
        city = _norm_text(origin.get("city"))
        if city:
            by_city = [
                branch
                for branch in items
                if _norm_text(branch.get("city")) == city
            ]
            if by_city:
                matched = by_city
    if matched is None:
        locality = _norm_text(origin.get("locality"))
        if locality:
            by_locality = [
                branch
                for branch in items
                if _norm_text(branch.get("locality") or branch.get("city")) == locality
            ]
            if by_locality:
                matched = by_locality
    if matched is None:
        state = _norm_text(origin.get("state"))
        if state:
            by_state = [
                branch
                for branch in items
                if _norm_text(branch.get("state")) == state
            ]
            if by_state:
                matched = by_state
    if matched is None:
        return items
    winners = _street_first(matched, origin.get("street"))
    seen = {id(branch) for branch in winners}
    return winners + [branch for branch in items if id(branch) not in seen]


def apply_arrival(
    branches,
    *,
    route,
    remembered_code="",
    origin_place=None,
    filters_active=False,
):
    """Pick arrival order + preferred first item for origin map load."""
    items = list(branches or [])
    if not items:
        return items
    remembered = _code(remembered_code)
    by_code = {branch["code"]: branch for branch in items}
    if remembered and remembered in by_code:
        chosen = by_code[remembered]
        return [chosen] + [branch for branch in items if branch["code"] != remembered]
    route_name = _code(route).lower()
    if route_name == "address" or items[0].get("distance") is not None:
        return items
    if route_name == "catalog" and not filters_active:
        return rank_catalog(items, origin_place or {})
    return items
