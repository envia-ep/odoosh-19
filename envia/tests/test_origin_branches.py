from odoo.tests import TransactionCase

from odoo.addons.envia.services.origin_branches import (
    address_body,
    apply_arrival,
    branch_codes_for_payload,
    cache_key,
    catalog_body,
    catalog_package,
    choose_route,
    entry_coords,
    filter_branches,
    label_create_branch_payload,
    missing_branch_side,
    needs_origin_branch,
    normalize_branches,
    numeric_coord,
    pick_branch,
    rank_catalog,
    should_reload,
)


class TestOriginBranches(TransactionCase):
    def test_drop_off_rules(self):
        self.assertFalse(needs_origin_branch("estafeta", 0))
        self.assertTrue(needs_origin_branch("estafeta", 1))
        self.assertFalse(needs_origin_branch("", 3))
        self.assertEqual(branch_codes_for_payload(0, "A", "B"), {})
        self.assertEqual(branch_codes_for_payload(1, "A", ""), {"origin": "A"})
        self.assertEqual(
            label_create_branch_payload(1, "SUL", ""),
            {"origin": {"branchCode": "SUL"}},
        )
        self.assertEqual(
            label_create_branch_payload(3, "SUL", "MEX05"),
            {
                "origin": {"branchCode": "SUL"},
                "destination": {"branchCode": "MEX05"},
            },
        )
        self.assertEqual(missing_branch_side(1, "", "", "estafeta"), "origin")
        self.assertEqual(missing_branch_side(3, "A", "", "estafeta"), "destination")
        self.assertEqual(missing_branch_side(0, "", "", "estafeta"), "")

    def test_route(self):
        self.assertEqual(choose_route("OXXO", 0, False), "oxxo")
        self.assertEqual(choose_route("ground", 4, True), "address")
        self.assertEqual(choose_route("ground", 4, False), "catalog")
        self.assertEqual(choose_route("ground", 0, True), "catalog")

    def test_normalize_and_pick(self):
        self.assertIsNone(numeric_coord(True))
        self.assertIsNone(numeric_coord(float("nan")))
        branches = normalize_branches(
            [
                {"code": "A", "name": "One", "latitude": "19.4", "longitude": False},
                {"id": "A", "name": "dup"},
                {"name": "no-code"},
                {"codigo": "OX1", "name": "Shop"},
            ],
            oxxo=True,
        )
        codes = [branch["code"] for branch in branches]
        self.assertEqual(codes, ["A", "OX1"])
        self.assertIsNone(branches[0]["longitude"])
        self.assertEqual(pick_branch(branches, "", "A", "OX1")["code"], "A")
        self.assertEqual(pick_branch(branches, "OX1", "A", "A")["code"], "OX1")
        fuzzy = filter_branches(branches, "zzzz")
        self.assertEqual(len(fuzzy), 1)

    def test_normalize_reads_nested_location_coords(self):
        lat, lng = entry_coords(
            {"location": {"lat": "25.67", "lng": "-100.31"}},
            {},
        )
        self.assertEqual(lat, 25.67)
        self.assertEqual(lng, -100.31)
        branches = normalize_branches(
            [
                {
                    "code": "MTY01",
                    "name": "Centro",
                    "address": {"location": {"latitude": 19.43, "longitude": -99.13}},
                }
            ]
        )
        self.assertEqual(branches[0]["latitude"], 19.43)
        self.assertEqual(branches[0]["longitude"], -99.13)

    def test_cache_key(self):
        key = cache_key("estafeta", "ground", 1)
        self.assertEqual(key, "estafeta|ground|1")
        self.assertFalse(should_reload(key, key, False, True))
        self.assertTrue(should_reload(key, key, True, True))
        self.assertTrue(should_reload(key, "other|ground|1", False, True))

    def test_catalog_package_and_body_contract(self):
        pkg = catalog_package(1.5)
        self.assertEqual(
            pkg,
            {
                "weight": 1.5,
                "length": 10.0,
                "height": 10.0,
                "width": 10.0,
                "amount": 1,
                "package_type": 1,
            },
        )
        mx = catalog_body("MX", "64000", "NL", "Monterrey", None, 25.6, -100.3, "Padre Mier", [pkg])
        self.assertNotIn("allBranch", mx)
        self.assertNotIn("zipcode", mx)
        self.assertNotIn("address", mx)
        self.assertEqual(mx["state"], "NL")
        self.assertEqual(mx["locality"], "Monterrey")
        self.assertEqual(mx["latitude"], 25.6)
        bare = catalog_body("MX", "64000", None, None, None, None, None, None, [pkg])
        self.assertTrue(bare["allBranch"])
        fr = catalog_body("FR", "75001", "IDF", "Paris", 1, None, None, "Rue", [pkg])
        self.assertEqual(fr["zipcode"], "75001")
        self.assertEqual(fr["address"], "Rue")
        self.assertEqual(fr["branchType"], 1)

    def test_address_body_contract(self):
        pkg = catalog_package(1)
        body = address_body(
            123,
            "mx",
            "ground",
            [pkg],
            zipcode="01000",
            street="Insurgentes",
            city="CDMX",
            state="CX",
            branch_type=1,
            latitude=19.43,
            longitude=-99.13,
        )
        self.assertEqual(body["carrierId"], 123)
        self.assertEqual(body["countryCodeCoverage"], "MX")
        self.assertEqual(body["zipCodeCoverage"], "01000")
        self.assertEqual(body["branchType"], 1)
        self.assertEqual(body["packages"], [pkg])

    def test_rank_catalog_and_apply_arrival(self):
        branches = [
            {
                "code": "FAR",
                "street": "Otra",
                "city": "Apodaca",
                "locality": "Apodaca",
                "state": "NL",
                "postal_code": "66600",
            },
            {
                "code": "NEAR",
                "street": "Padre Mier",
                "city": "Monterrey",
                "locality": "Monterrey",
                "state": "NL",
                "postal_code": "64000",
            },
            {
                "code": "PREF",
                "street": "Hidalgo",
                "city": "Monterrey",
                "locality": "Monterrey",
                "state": "NL",
                "postal_code": "64010",
            },
        ]
        origin = {
            "postal_code": "64000",
            "city": "Monterrey",
            "locality": "Monterrey",
            "state": "NL",
            "street": "Padre Mier",
        }
        ranked = rank_catalog(branches, origin)
        self.assertEqual(ranked[0]["code"], "NEAR")
        prefix_origin = {**origin, "postal_code": "64099"}
        ranked_prefix = rank_catalog(branches, prefix_origin)
        self.assertEqual(
            [branch["code"] for branch in ranked_prefix[:2]],
            ["NEAR", "PREF"],
        )
        remembered = apply_arrival(
            branches,
            route="catalog",
            remembered_code="PREF",
            origin_place=origin,
        )
        self.assertEqual(remembered[0]["code"], "PREF")
        with_distance = [
            {**branches[0], "distance": 2.0},
            {**branches[1], "distance": 1.0},
        ]
        ordered = apply_arrival(
            with_distance,
            route="catalog",
            origin_place=origin,
        )
        self.assertEqual(ordered[0]["code"], "FAR")
        address_route = apply_arrival(
            list(reversed(branches)),
            route="address",
            origin_place=origin,
        )
        self.assertEqual(address_route[0]["code"], "PREF")
