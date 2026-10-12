from unittest.mock import patch

from odoo.addons.envia.services.dto import Contact, QuoteRequest, QuoteService
from odoo.addons.envia.services.website_pickup import WebsitePickupService
from odoo.tests import tagged
from odoo.tests.common import TransactionCase
from odoo.exceptions import ValidationError


@tagged("post_install", "-at_install")
class TestWebsitePickupService(TransactionCase):
    def setUp(self):
        super().setUp()
        self.env.company.envia_enable_branches = True
        shipping_product = self.env.ref(
            "envia.product_envia_shipping",
            raise_if_not_found=False,
        )
        product_domain = [("sale_ok", "=", True)]
        if shipping_product:
            product_domain.append(("id", "!=", shipping_product.id))
        self.product = self.env["product.product"].search(product_domain, limit=1)
        if not self.product:
            self.product = self.env["product.product"].create(
                {
                    "name": "QA Merchandise",
                    "sale_ok": True,
                    "list_price": 10.0,
                    "weight": 1.0,
                }
            )
        self.carrier = self.env.ref("envia.delivery_carrier_envia")
        self.carrier.margin = 0.0
        self.carrier.fixed_margin = 0.0
        country = self.env.ref("base.mx", raise_if_not_found=False) or self.env[
            "res.country"
        ].search([("code", "=", "MX")], limit=1)
        state = self.env["res.country.state"].search(
            [("country_id", "=", country.id)],
            limit=1,
        )
        self.partner = self.env["res.partner"].create(
            {
                "name": "Website Shopper",
                "email": "shopper@example.com",
                "phone": "8180000000",
                "street": "Av Test 1",
                "city": "Monterrey",
                "zip": "64000",
                "country_id": country.id,
                "state_id": state.id if state else False,
            }
        )
        company_partner = self.env.company.partner_id
        company_partner.write(
            {
                "country_id": country.id,
                "state_id": state.id if state else False,
                "zip": company_partner.zip or "64000",
                "city": company_partner.city or "Monterrey",
                "phone": company_partner.phone or "8181111111",
                "email": company_partner.email or "warehouse@example.com",
                "street": company_partner.street or "Warehouse 1",
            }
        )
        self.order = self.env["sale.order"].create(
            {
                "partner_id": self.partner.id,
                "partner_invoice_id": self.partner.id,
                "partner_shipping_id": self.partner.id,
                "order_line": [
                    (0, 0, {"product_id": self.product.id, "product_uom_qty": 1.0})
                ],
            }
        )
        self.service = WebsitePickupService(self.env)

    def _base_request(self, *, destination_branch=None):
        origin = Contact(
            name="Warehouse",
            street="Warehouse 1",
            city="Monterrey",
            state="NL",
            postal_code="64000",
            country="MX",
            phone="8181111111",
            email="warehouse@example.com",
        )
        destination = Contact(
            name="Shopper",
            street="Av Test 1",
            city="Monterrey",
            state="NL",
            postal_code="64000",
            country="MX",
            phone="8180000000",
            email="shopper@example.com",
            branch_code=destination_branch,
        )
        return QuoteRequest(
            origin_postal_code="64000",
            origin_country="MX",
            origin_state="NL",
            destination_postal_code="64000",
            destination_country="MX",
            destination_state="NL",
            weight=1.0,
            content="QA Merchandise",
            currency=self.order.currency_id.name,
            origin_contact=origin,
            destination_contact=destination,
            carriers="paquetexpress" if destination_branch else "all",
            expected_drop_off=None,
        )

    def _ship_checkout_body(self):
        return {
            "data": [
                {
                    "carrier": "dhl",
                    "carrierDescription": "DHL",
                    "service": "express",
                    "serviceId": 101,
                    "serviceDescription": "Express",
                    "totalPrice": 120.0,
                    "currency": self.order.currency_id.name,
                    "dropOff": 0,
                    "deliveryEstimate": "2",
                },
                {
                    "carrier": "fedex",
                    "carrierDescription": "FedEx",
                    "service": "ground",
                    "serviceId": 202,
                    "serviceDescription": "Ground",
                    "totalPrice": 95.0,
                    "currency": self.order.currency_id.name,
                    "dropOff": 0,
                    "deliveryEstimate": "3",
                },
                {
                    "carrier": "paquetexpress",
                    "carrierDescription": "Paquetexpress",
                    "service": "ocurre",
                    "serviceId": 303,
                    "serviceDescription": "Ocurre",
                    "totalPrice": 90.0,
                    "currency": self.order.currency_id.name,
                    "dropOff": 1,
                    "deliveryEstimate": "1",
                },
            ]
        }

    def _pickup_checkout_body(self):
        return {
            "data": [
                {
                    "carrier": "paquetexpress",
                    "carrierDescription": "Paquetexpress",
                    "service": "std",
                    "serviceDescription": "Standard",
                    "totalPrice": 190.0,
                    "currency": self.order.currency_id.name,
                    "dropOff": 1,
                    "deliveryEstimate": "1",
                    "branches": [
                        {
                            "reference": "Paquetexpress - Av. Stiva",
                            "branchCode": "MTY01",
                            "distance": 1.2,
                            "lat": 25.74,
                            "lng": -100.28,
                            "address": {
                                "address": "Av. STIVA 400",
                                "city": "San Nicolas",
                                "postalCode": "66425",
                                "state": "NL",
                                "country": "MX",
                            },
                        }
                    ],
                },
            ]
        }

    def test_list_ship_rates_keeps_door_to_door_only(self):
        with (
            patch.object(
                self.service,
                "_checkout_body",
                return_value=self._ship_checkout_body(),
            ),
            patch(
                "odoo.addons.envia.services.website_pickup.PayloadMapper"
                ".build_quote_request_from_sale_order",
                return_value=self._base_request(),
            ),
        ):
            options = self.service.list_ship_rates(self.order)
        self.assertEqual(len(options), 2)
        carriers = {option["carrier"] for option in options}
        self.assertEqual(carriers, {"dhl", "fedex"})
        self.assertTrue(all(option["route_type"] == "ship" for option in options))
        self.assertTrue(all(not option["branch_code"] for option in options))
        # Cheapest Ship first (FedEx 95 < DHL 120) for checkout auto-select.
        self.assertEqual(options[0]["carrier"], "fedex")
        self.assertEqual(options[0]["price"], 95.0)
        self.assertEqual(options[0]["base_price"], 95.0)
        self.assertEqual(options[0]["envia_service_id"], 202)
        self.assertEqual(options[1]["carrier"], "dhl")
        self.assertEqual(options[1]["envia_service_id"], 101)

    def test_list_ship_rates_includes_carrier_fixed_margin(self):
        self.carrier.fixed_margin = 0.99
        with (
            patch.object(
                self.service,
                "_checkout_body",
                return_value=self._ship_checkout_body(),
            ),
            patch(
                "odoo.addons.envia.services.website_pickup.PayloadMapper"
                ".build_quote_request_from_sale_order",
                return_value=self._base_request(),
            ),
        ):
            options = self.service.list_ship_rates(self.order)
        self.assertEqual(options[0]["carrier"], "fedex")
        self.assertEqual(options[0]["base_price"], 95.0)
        self.assertAlmostEqual(options[0]["price"], 95.99)
        self.assertEqual(options[1]["base_price"], 120.0)
        self.assertAlmostEqual(options[1]["price"], 120.99)

    def test_list_ship_rates_does_not_apply_pickup_branch_limit(self):
        body = {
            "data": [
                {
                    "carrier": "dhl",
                    "carrierDescription": "DHL",
                    "service": "express",
                    "serviceDescription": "Express",
                    "totalPrice": 120.0,
                    "currency": self.order.currency_id.name,
                    "dropOff": 0,
                },
                {
                    "carrier": "dhl",
                    "carrierDescription": "DHL",
                    "service": "economy",
                    "serviceDescription": "Economy",
                    "totalPrice": 80.0,
                    "currency": self.order.currency_id.name,
                    "dropOff": 0,
                },
                {
                    "carrier": "dhl",
                    "carrierDescription": "DHL",
                    "service": "nextday",
                    "serviceDescription": "Next Day",
                    "totalPrice": 200.0,
                    "currency": self.order.currency_id.name,
                    "dropOff": 0,
                },
            ]
        }
        self.env.company.envia_checkout_rates_per_carrier = 1
        with (
            patch.object(self.service, "_checkout_body", return_value=body),
            patch(
                "odoo.addons.envia.services.website_pickup.PayloadMapper"
                ".build_quote_request_from_sale_order",
                return_value=self._base_request(),
            ),
        ):
            options = self.service.list_ship_rates(self.order)
        self.assertEqual(len(options), 3)

    def test_list_pickup_options_limits_branches_per_carrier(self):
        body = {
            "data": [
                {
                    "carrier": "paquetexpress",
                    "carrierDescription": "Paquetexpress",
                    "service": "std",
                    "serviceDescription": "Standard",
                    "totalPrice": 190.0,
                    "currency": self.order.currency_id.name,
                    "dropOff": 1,
                    "deliveryEstimate": "1",
                    "branches": [
                        {
                            "reference": "Branch A",
                            "branchCode": "MTY01",
                            "distance": 1.0,
                            "lat": 25.74,
                            "lng": -100.28,
                            "address": {
                                "address": "St 1",
                                "city": "Monterrey",
                                "postalCode": "64000",
                                "state": "NL",
                                "country": "MX",
                            },
                        },
                        {
                            "reference": "Branch B",
                            "branchCode": "MTY02",
                            "distance": 2.0,
                            "lat": 25.75,
                            "lng": -100.29,
                            "address": {
                                "address": "St 2",
                                "city": "Monterrey",
                                "postalCode": "64000",
                                "state": "NL",
                                "country": "MX",
                            },
                        },
                        {
                            "reference": "Branch C",
                            "branchCode": "MTY03",
                            "distance": 3.0,
                            "lat": 25.76,
                            "lng": -100.30,
                            "address": {
                                "address": "St 3",
                                "city": "Monterrey",
                                "postalCode": "64000",
                                "state": "NL",
                                "country": "MX",
                            },
                        },
                    ],
                },
            ]
        }
        self.env.company.envia_checkout_rates_per_carrier = 2
        with (
            patch.object(self.service, "_checkout_body", return_value=body),
            patch(
                "odoo.addons.envia.services.website_pickup.PayloadMapper"
                ".build_quote_request_from_sale_order",
                return_value=self._base_request(),
            ),
        ):
            options = self.service.list_pickup_options(self.order)
        self.assertEqual(len(options), 2)
        self.assertEqual([option["branch_code"] for option in options], ["MTY01", "MTY02"])

    def test_list_pickup_options_limits_unique_branches_not_duplicate_services(self):
        """Same branch under two services of paquetexpress counts once toward the cap."""
        currency = self.order.currency_id.name
        branch = {
            "reference": "Branch A",
            "branchCode": "MTY01",
            "distance": 1.0,
            "lat": 25.74,
            "lng": -100.28,
            "address": {
                "address": "St 1",
                "city": "Monterrey",
                "postalCode": "64000",
                "state": "NL",
                "country": "MX",
            },
        }
        body = {
            "data": [
                {
                    "carrier": "paquetexpress",
                    "carrierDescription": "Paquetexpress",
                    "service": "std",
                    "serviceDescription": "Standard",
                    "totalPrice": 190.0,
                    "currency": currency,
                    "dropOff": 1,
                    "branches": [branch],
                },
                {
                    "carrier": "paquetexpress",
                    "carrierDescription": "Paquetexpress",
                    "service": "express",
                    "serviceDescription": "Express",
                    "totalPrice": 220.0,
                    "currency": currency,
                    "dropOff": 1,
                    "branches": [
                        branch,
                        {
                            "reference": "Branch B",
                            "branchCode": "MTY02",
                            "distance": 2.0,
                            "lat": 25.75,
                            "lng": -100.29,
                            "address": {
                                "address": "St 2",
                                "city": "Monterrey",
                                "postalCode": "64000",
                                "state": "NL",
                                "country": "MX",
                            },
                        },
                    ],
                },
            ]
        }
        self.env.company.envia_checkout_rates_per_carrier = 1
        with (
            patch.object(self.service, "_checkout_body", return_value=body),
            patch(
                "odoo.addons.envia.services.website_pickup.PayloadMapper"
                ".build_quote_request_from_sale_order",
                return_value=self._base_request(),
            ),
        ):
            options = self.service.list_pickup_options(self.order)
        self.assertEqual(len(options), 1)
        self.assertEqual(options[0]["carrier"], "paquetexpress")
        self.assertEqual(options[0]["branch_code"], "MTY01")

    def test_list_pickup_disabled_raises_user_error(self):
        from odoo.exceptions import UserError

        self.env.company.envia_checkout_enable_pickup = False
        with self.assertRaises(UserError) as error:
            self.service.list_options(self.order, "pickup")
        self.assertIn("Pickup is disabled", error.exception.args[0])

    def test_list_pickup_options_from_nested_branches(self):
        with (
            patch.object(
                self.service,
                "_checkout_body",
                return_value=self._pickup_checkout_body(),
            ),
            patch(
                "odoo.addons.envia.services.website_pickup.PayloadMapper"
                ".build_quote_request_from_sale_order",
                return_value=self._base_request(),
            ),
        ):
            options = self.service.list_pickup_options(self.order)
        self.assertEqual(len(options), 1)
        option = options[0]
        self.assertEqual(option["route_type"], "pickup")
        self.assertEqual(option["branch_code"], "MTY01")
        self.assertEqual(option["price"], 190.0)
        self.assertEqual(option["lat"], 25.74)
        self.assertEqual(option["lng"], -100.28)
        self.assertIn("STIVA", option["address"])

    def test_pickup_checkout_loads_branches_once_per_carriers(self):
        """Pickup rates without nested branches must not N+1 get_branches calls."""
        body = {
            "data": [
                {
                    "carrier": "paquetexpress",
                    "carrierDescription": "Paquetexpress",
                    "service": "std",
                    "serviceDescription": "Standard",
                    "totalPrice": 190.0,
                    "currency": self.order.currency_id.name,
                    "dropOff": 1,
                    "deliveryEstimate": "1",
                },
                {
                    "carrier": "dhl",
                    "carrierDescription": "DHL",
                    "service": "ocurre",
                    "serviceDescription": "Ocurre",
                    "totalPrice": 210.0,
                    "currency": self.order.currency_id.name,
                    "dropOff": 2,
                    "deliveryEstimate": "2",
                },
                {
                    "carrier": "fedex",
                    "carrierDescription": "FedEx",
                    "service": "ground",
                    "serviceDescription": "Ground",
                    "totalPrice": 95.0,
                    "currency": self.order.currency_id.name,
                    "dropOff": 0,
                    "deliveryEstimate": "3",
                },
            ]
        }
        branches = [
            {
                "branchCode": "MTY01",
                "reference": "PX MTY",
                "distance": 1.0,
                "lat": 25.7,
                "lng": -100.3,
                "address": {
                    "address": "Stiva 1",
                    "city": "MTY",
                    "postalCode": "64000",
                    "state": "NL",
                    "country": "MX",
                },
            },
            {
                "branchCode": "MTY02",
                "reference": "DHL MTY",
                "distance": 2.0,
                "lat": 25.71,
                "lng": -100.31,
                "address": {
                    "address": "Ave 2",
                    "city": "MTY",
                    "postalCode": "64000",
                    "state": "NL",
                    "country": "MX",
                },
            },
        ]

        def _fake_load(order, carrier_codes=None):
            result = []
            for code in carrier_codes or ["paquetexpress"]:
                entry = dict(branches[0] if code == "paquetexpress" else branches[1])
                result.append(self.service._normalize_branch_entry(entry, code, "MX"))
            return result

        with (
            patch.object(self.service, "_checkout_body", return_value=body),
            patch(
                "odoo.addons.envia.services.website_pickup.PayloadMapper"
                ".build_quote_request_from_sale_order",
                return_value=self._base_request(),
            ),
            patch.object(
                self.service,
                "_load_destination_branches",
                side_effect=_fake_load,
            ) as load_branches,
        ):
            options = self.service.list_pickup_options(self.order)
        self.assertEqual(load_branches.call_count, 1)
        called_carriers = set(load_branches.call_args.kwargs.get("carrier_codes") or [])
        self.assertEqual(called_carriers, {"paquetexpress", "dhl"})
        self.assertEqual(len(options), 2)
        self.assertTrue(all(option["route_type"] == "pickup" for option in options))
        self.assertNotIn("fedex", {option["carrier"] for option in options})

    def test_list_pickup_options_fallback_queries_branches(self):
        branches = [
            {
                "id": "MTY01",
                "reference": "Paquetexpress - Av. Stiva",
                "branchCode": "MTY01",
                "distance": 1.2,
                "lat": 25.74,
                "lng": -100.28,
                "address": {
                    "address": "Av. STIVA 400",
                    "city": "San Nicolas",
                    "postalCode": "66425",
                    "state": "NL",
                    "country": "MX",
                },
            }
        ]
        pickup_service = QuoteService(
            service_id="paquetexpress:std",
            carrier="paquetexpress",
            carrier_name="Paquetexpress",
            service_name="Standard",
            price=190.0,
            currency=self.order.currency_id.name,
            drop_off=1,
            estimated_delivery_days=1,
        )
        with (
            patch.object(self.service, "_pickup_options_from_checkout", return_value=[]),
            patch(
                "odoo.addons.envia.services.website_pickup.EnviaClient.get_branches",
                return_value=branches,
            ),
            patch.object(
                self.service,
                "_branch_carrier_codes",
                return_value=["paquetexpress"],
            ),
            patch(
                "odoo.addons.envia.models.res_company.ResCompany._envia_get_shipping_api_token",
                return_value="token",
            ),
            patch.object(
                self.service,
                "_rates_by_carrier_for_branches",
                return_value={"paquetexpress": pickup_service},
            ),
        ):
            options = self.service.list_pickup_options(self.order)
        self.assertEqual(len(options), 1)
        self.assertEqual(options[0]["branch_code"], "MTY01")
        self.assertEqual(options[0]["price"], 190.0)

    def test_apply_selection_sets_delivery_line_and_quote_branch(self):
        payload = {
            "route_type": "pickup",
            "carrier": "paquetexpress",
            "carrier_name": "Paquetexpress",
            "branch_code": "MTY01",
            "service_id": "paquetexpress:std",
            "service": "Standard",
            "name": "Paquetexpress - Av. Stiva",
            "street": "Av. STIVA 400",
            "city": "San Nicolas",
            "zip": "66425",
            "state_code": "NL",
            "country_code": "MX",
            "price": 190.0,
            "drop_off": 1,
        }
        with patch(
            "odoo.addons.envia.services.website_pickup.PayloadMapper"
            ".build_quote_request_from_sale_order",
            return_value=self._base_request(destination_branch="MTY01"),
        ):
            result = self.service.apply_selection(self.order, payload)
        quote = self.order.envia_quote_ids[:1]
        self.assertTrue(quote)
        self.assertEqual(quote.destination_location_type, "branch")
        self.assertEqual(quote.destination_branch_code, "MTY01")
        self.assertTrue(quote.selected_service_id)
        self.assertEqual(quote.state, "quoted")
        self.assertEqual(result["price"], 190.0)
        delivery = self.order.order_line.filtered("is_delivery")
        self.assertTrue(delivery)
        self.assertAlmostEqual(delivery.price_unit, 190.0)

    def test_apply_selection_applies_carrier_fixed_margin(self):
        """Delivery line must include delivery.carrier fixed_margin."""
        self.carrier.margin = 0.0
        self.carrier.fixed_margin = 0.99
        payload = {
            "route_type": "ship",
            "carrier": "dhl",
            "carrier_name": "DHL",
            "service_id": "dhl:express",
            "service": "Express",
            "name": "DHL - Express",
            "price": 120.0,
            "drop_off": 0,
        }
        with patch(
            "odoo.addons.envia.services.website_pickup.PayloadMapper"
            ".build_quote_request_from_sale_order",
            return_value=self._base_request(),
        ):
            result = self.service.apply_selection(self.order, payload)
        delivery = self.order.order_line.filtered("is_delivery")
        self.assertAlmostEqual(delivery.price_unit, 120.99)
        self.assertAlmostEqual(result["price"], 120.99)

    def test_apply_selection_uses_base_price_not_display_price(self):
        """Selecting a listed (margined) option must not double-apply margins."""
        self.carrier.margin = 0.0
        self.carrier.fixed_margin = 0.99
        payload = {
            "route_type": "ship",
            "carrier": "dhl",
            "carrier_name": "DHL",
            "service_id": "dhl:express",
            "service": "Express",
            "name": "DHL - Express",
            "base_price": 120.0,
            "price": 120.99,
            "drop_off": 0,
        }
        with patch(
            "odoo.addons.envia.services.website_pickup.PayloadMapper"
            ".build_quote_request_from_sale_order",
            return_value=self._base_request(),
        ):
            result = self.service.apply_selection(self.order, payload)
        delivery = self.order.order_line.filtered("is_delivery")
        self.assertAlmostEqual(delivery.price_unit, 120.99)
        self.assertAlmostEqual(result["price"], 120.99)

    def test_apply_ship_selection_uses_new_quote_not_stale(self):
        """Selecting a ship rate must sync that price, not an older quote."""
        stale = self.env["envia.quote"].create_from_api_response(
            type("Resp", (), {"quote_id": "stale", "valid_until": False, "services": [
                QuoteService(
                    service_id="old:svc",
                    carrier="old",
                    carrier_name="Old",
                    service_name="Stale",
                    price=13.62,
                    currency=self.order.currency_id.name,
                    drop_off=None,
                )
            ]})(),
            {
                "sale_order_id": self.order.id,
                "destination_partner_id": self.partner.id,
                "origin_postal_code": "64000",
                "origin_country": "MX",
                "destination_postal_code": "64000",
                "destination_country": "MX",
                "weight": 1.0,
                "content": "QA",
                "currency_id": self.order.currency_id.id,
                "company_id": self.order.company_id.id,
                "origin_location_type": "address",
                "destination_location_type": "address",
            },
        )
        stale.service_ids[:1].action_select_service()
        self.order._sync_envia_shipping_line(stale)
        self.assertAlmostEqual(
            self.order.order_line.filtered("is_delivery").price_unit,
            13.62,
        )
        payload = {
            "route_type": "ship",
            "carrier": "dhl",
            "carrier_name": "DHL",
            "service_id": "dhl:express",
            "service": "Express",
            "price": 120.0,
        }
        with patch(
            "odoo.addons.envia.services.website_pickup.PayloadMapper"
            ".build_quote_request_from_sale_order",
            return_value=self._base_request(),
        ):
            result = self.service.apply_selection(self.order, payload)
        self.assertEqual(result["price"], 120.0)
        delivery = self.order.order_line.filtered("is_delivery")
        self.assertAlmostEqual(delivery.price_unit, 120.0)
        self.assertEqual(self.order._get_active_envia_quote().selected_service_id.service_id, "dhl:express")

    def test_apply_ship_selection_persists_envia_service_id(self):
        """Checkout select must store Envia numeric serviceId on SO/quote."""
        payload = {
            "route_type": "ship",
            "carrier": "dhl",
            "carrier_name": "DHL",
            "service_id": "dhl:express",
            "envia_service_id": 101,
            "service": "Express",
            "base_price": 120.0,
            "price": 120.0,
            "drop_off": 0,
        }
        with patch(
            "odoo.addons.envia.services.website_pickup.PayloadMapper"
            ".build_quote_request_from_sale_order",
            return_value=self._base_request(),
        ):
            self.service.apply_selection(self.order, payload)
        quote = self.order._get_active_envia_quote()
        self.assertEqual(quote.selected_service_id.envia_service_id, 101)
        self.assertEqual(self.order.envia_service_id, 101)

    def test_payment_guard_requires_envia_selection(self):
        self.order.carrier_id = self.carrier
        with patch.object(
            type(self.order),
            "_get_delivery_methods",
            return_value=self.carrier,
        ):
            with self.assertRaises(ValidationError) as error:
                self.order._check_cart_is_ready_to_be_paid()
            self.assertIn("Envia", str(error.exception))
            payload = {
                "route_type": "pickup",
                "carrier": "paquetexpress",
                "carrier_name": "Paquetexpress",
                "branch_code": "MTY01",
                "service_id": "paquetexpress:std",
                "service": "Standard",
                "name": "Paquetexpress - Av. Stiva",
                "street": "Av. STIVA 400",
                "city": "San Nicolas",
                "zip": "66425",
                "state_code": "NL",
                "country_code": "MX",
                "price": 190.0,
                "drop_off": 1,
            }
            with patch(
                "odoo.addons.envia.services.website_pickup.PayloadMapper"
                ".build_quote_request_from_sale_order",
                return_value=self._base_request(destination_branch="MTY01"),
            ):
                self.service.apply_selection(self.order, payload)
            self.order._check_cart_is_ready_to_be_paid()
