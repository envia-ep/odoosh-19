from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.addons.envia.services.dto import (
    CreateShipmentResponse,
    QuoteResponse,
    QuoteService,
)
from odoo.addons.envia.services.envia_official_adapter import EnviaOfficialAdapter
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged("post_install", "-at_install")
class TestEnviaDeliveryCarrier(TransactionCase):
    def setUp(self):
        super().setUp()
        self.env.company.envia_enable_branches = True
        shipping_product = self.env.ref("envia.product_envia_shipping", raise_if_not_found=False)
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
                }
            )
        self.carrier = self.env.ref("envia.delivery_carrier_envia")
        self.partner = self.env.company.partner_id
        self.order = self.env["sale.order"].create(
            {
                "partner_id": self.partner.id,
                "partner_invoice_id": self.partner.id,
                "partner_shipping_id": self.partner.id,
                "order_line": [(0, 0, {"product_id": self.product.id, "product_uom_qty": 1.0})],
            }
        )

    def test_delivery_carrier_envia_is_registered(self):
        self.assertEqual(self.carrier.delivery_type, "envia")
        self.assertEqual(self.carrier.name, "Envia.com")
        self.assertEqual(self.carrier.integration_level, "rate")
        self.assertTrue(hasattr(self.carrier, "envia_rate_shipment"))
        self.assertTrue(hasattr(self.carrier, "envia_send_shipping"))
        self.assertTrue(hasattr(self.carrier, "envia_cancel_shipment"))
        self.assertTrue(hasattr(self.carrier, "envia_get_tracking_link"))

    def _mock_rate_quote(self, response=None, *, quote_side_effect=None):
        """Isolate envia_rate_shipment from address/geocode/HTTP."""
        build = patch(
            "odoo.addons.envia.models.delivery_carrier.PayloadMapper"
            ".build_quote_request_from_sale_order"
        )
        adapter = patch(
            "odoo.addons.envia.models.delivery_carrier.get_envia_adapter"
        )
        build_cm = build.start()
        adapter_cm = adapter.start()
        self.addCleanup(build.stop)
        self.addCleanup(adapter.stop)
        build_cm.return_value = object()
        if quote_side_effect is not None:
            adapter_cm.side_effect = quote_side_effect
        else:
            mock_adapter = adapter_cm.return_value
            mock_adapter.quote.return_value = response
            mock_adapter.pick_cheapest_service.side_effect = (
                EnviaOfficialAdapter.pick_cheapest_service
            )
        return adapter_cm

    def test_envia_rate_shipment_returns_cheapest_rate(self):
        response = QuoteResponse(
            quote_id="test",
            services=[
                QuoteService(
                    service_id="fedex:1",
                    carrier="fedex",
                    carrier_name="FedEx",
                    service_name="Economy",
                    price=150.0,
                    currency=self.order.currency_id.name,
                ),
                QuoteService(
                    service_id="dhl:1",
                    carrier="dhl",
                    carrier_name="DHL",
                    service_name="Express",
                    price=99.0,
                    currency=self.order.currency_id.name,
                ),
            ],
        )
        self._mock_rate_quote(response)
        result = self.carrier.envia_rate_shipment(self.order)
        self.assertTrue(result["success"])
        self.assertEqual(result["price"], 99.0)
        self.assertFalse(result["error_message"])
        warning = str(result["warning_message"])
        self.assertIn("DHL", warning)
        self.assertIn("Express", warning)

    def test_rate_shipment_dispatcher_uses_envia_method(self):
        """Core calls rate_shipment → envia_rate_shipment via delivery_type."""
        response = QuoteResponse(
            quote_id="dispatcher",
            services=[
                QuoteService(
                    service_id="estafeta:1",
                    carrier="estafeta",
                    carrier_name="Estafeta",
                    service_name="Terrestre",
                    price=80.0,
                    currency=self.order.currency_id.name,
                ),
            ],
        )
        self._mock_rate_quote(response)
        self.carrier.margin = 0.0
        result = self.carrier.rate_shipment(self.order)
        self.assertTrue(result["success"])
        self.assertFalse(result["error_message"])
        self.assertIn("carrier_price", result)
        self.assertGreater(result["price"], 0.0)

    def test_envia_rate_shipment_no_services(self):
        response = QuoteResponse(quote_id="empty", services=[])
        self._mock_rate_quote(response)
        result = self.carrier.envia_rate_shipment(self.order)
        self.assertFalse(result["success"])
        self.assertEqual(result["price"], 0.0)
        self.assertTrue(result["error_message"])
        self.assertFalse(result["warning_message"])

    def test_envia_rate_shipment_surfaces_user_error(self):
        self._mock_rate_quote(
            quote_side_effect=UserError("Missing API token"),
        )
        result = self.carrier.envia_rate_shipment(self.order)
        self.assertFalse(result["success"])
        self.assertEqual(result["price"], 0.0)
        self.assertIn("Missing API token", result["error_message"])
        self.assertFalse(result["warning_message"])

    def test_choose_delivery_carrier_create_standard_delivery(self):
        standard_carrier = self.env["delivery.carrier"].create(
            {
                "name": "Standard Delivery",
                "delivery_type": "fixed",
                "product_id": self.env.ref("delivery.product_product_delivery").id,
                "fixed_price": 10.0,
            }
        )
        wizard = self.env["choose.delivery.carrier"].create(
            {
                "order_id": self.order.id,
                "carrier_id": standard_carrier.id,
            }
        )
        self.assertFalse(wizard.envia_wizard_id)

    def test_choose_delivery_carrier_creates_envia_wizard_on_open(self):
        wizard = self.env["choose.delivery.carrier"].create(
            {
                "order_id": self.order.id,
                "carrier_id": self.carrier.id,
            }
        )
        self.assertTrue(wizard.envia_wizard_id)
        self.assertEqual(wizard.envia_wizard_id.sale_order_id, self.order)
        self.assertEqual(
            wizard.envia_wizard_id.destination_partner_id,
            self.order.partner_shipping_id,
        )

    def test_choose_delivery_carrier_syncs_package_weight_to_envia_wizard(self):
        self.product.weight = 0.10
        wizard = self.env["choose.delivery.carrier"].create(
            {
                "order_id": self.order.id,
                "carrier_id": self.carrier.id,
            }
        )
        self.assertEqual(wizard.envia_wizard_id.weight, 0.10)

    def test_choose_delivery_carrier_normalizes_weight_below_minimum(self):
        self.product.weight = 0.05
        wizard = self.env["choose.delivery.carrier"].create(
            {
                "order_id": self.order.id,
                "carrier_id": self.carrier.id,
            }
        )
        self.assertEqual(wizard.envia_wizard_id.weight, 1.0)

    def test_choose_delivery_carrier_update_restores_order_weight_not_saved_quote(self):
        self.product.weight = 0.10
        quote = self.env["envia.quote"].create(
            {
                "origin_postal_code": "06600",
                "origin_country": "MX",
                "destination_postal_code": "64000",
                "destination_country": "MX",
                "origin_partner_id": self.partner.id,
                "destination_partner_id": self.partner.id,
                "sale_order_id": self.order.id,
                "weight": 1.0,
                "content": "General merchandise",
                "declared_value": 100.0,
                "currency_id": self.order.currency_id.id,
            }
        )
        service = self.env["envia.quote.service"].create(
            {
                "quote_id": quote.id,
                "service_id": "dhl:1",
                "carrier": "dhl",
                "carrier_name": "DHL",
                "service_name": "Economy",
                "price": 150.0,
                "currency_name": self.order.currency_id.name,
            }
        )
        quote.selected_service_id = service
        self.order.set_delivery_line(self.carrier, 150.0)
        wizard = self.env["choose.delivery.carrier"].create(
            {
                "order_id": self.order.id,
                "carrier_id": self.carrier.id,
            }
        )
        self.assertEqual(wizard.envia_wizard_id.weight, 0.10)

    def test_choose_delivery_carrier_onchange_without_order_does_not_crash(self):
        # Selecting Envia.com in the modal fires onchange before order_id is bound.
        wizard = self.env["choose.delivery.carrier"].new({"carrier_id": self.carrier.id})
        wizard._onchange_carrier_id()
        self.assertTrue(wizard.envia_wizard_id)
        self.assertFalse(wizard.envia_wizard_id.sale_order_id)

    def test_choose_delivery_carrier_get_rate_keeps_user_location_types(self):
        # Update restores Pickup; user switches to Ship/Ship and Get rate must
        # not re-apply the saved quote on create.
        quote = self.env["envia.quote"].create(
            {
                "origin_postal_code": "06600",
                "origin_country": "MX",
                "destination_postal_code": "64000",
                "destination_country": "MX",
                "destination_location_type": "branch",
                "destination_branch_code": "MTY01",
                "origin_partner_id": self.partner.id,
                "destination_partner_id": self.partner.id,
                "sale_order_id": self.order.id,
                "weight": 1.0,
                "content": "Test",
                "state": "quoted",
            }
        )
        service = self.env["envia.quote.service"].create(
            {
                "quote_id": quote.id,
                "service_id": "dhl:ocurre",
                "carrier": "dhl",
                "carrier_name": "DHL",
                "service_name": "Economy Ocurre",
                "price": 281.16,
                "currency_name": self.order.currency_id.name,
            }
        )
        quote.selected_service_id = service
        self.order.set_delivery_line(self.carrier, 281.16)
        mx, state = self._mx_state()

        def fake_load(quote_wizard, side, carrier_codes=None):
            self.env["envia.quote.wizard.branch"].create(
                {
                    "wizard_id": quote_wizard.id,
                    "side": side,
                    "name": "DHL MTY01",
                    "branch_code": "MTY01",
                    "carrier": "dhl",
                    "zip": "64000",
                    "city": "Monterrey",
                    "country_code": "MX",
                    "state_code": state.code,
                }
            )

        response = QuoteResponse(
            quote_id="test",
            services=[
                QuoteService(
                    service_id="fedex:1",
                    carrier="fedex",
                    carrier_name="FedEx",
                    service_name="Economy",
                    price=120.0,
                    currency=self.order.currency_id.name,
                ),
            ],
        )
        with patch(
            "odoo.addons.envia.wizards.envia_quote_wizard.EnviaQuoteWizard._load_branches",
            autospec=True,
            side_effect=fake_load,
        ), patch(
            "odoo.addons.envia.wizards.envia_quote_wizard.EnviaGeocodesClient"
        ) as geocodes, patch(
            "odoo.addons.envia.wizards.envia_quote_wizard.get_envia_adapter"
        ) as get_adapter:
            geocodes.return_value.lookup_zipcode.return_value = []
            get_adapter.return_value.quote.return_value = response
            pending = self.env["choose.delivery.carrier"].new(
                {"order_id": self.order.id, "carrier_id": self.carrier.id}
            )
            pending._onchange_carrier_id()
            quote_wizard = pending.envia_wizard_id
            self.assertEqual(quote_wizard.destination_location_type, "branch")
            self.assertTrue(quote_wizard.is_seeded_from_order)
            # User switches to Ship / Ship before Get rate (keep a real record ref).
            quote_wizard.write(
                {
                    "origin_location_type": "address",
                    "destination_location_type": "address",
                    "origin_partner_id": self.partner.id,
                    "destination_partner_id": self.partner.id,
                    "origin_country_id": mx.id,
                    "destination_country_id": mx.id,
                    "origin_state_id": state.id,
                    "destination_state_id": state.id,
                    "origin_street": "Origin St",
                    "destination_street": "Dest St",
                    "origin_city": "CDMX",
                    "destination_city": "GDL",
                    "origin_postal_code": "06600",
                    "destination_postal_code": "44100",
                }
            )
            quote_wizard.destination_branch_line_ids.unlink()
            wizard = self.env["choose.delivery.carrier"].create(
                {
                    "order_id": self.order.id,
                    "carrier_id": self.carrier.id,
                    "envia_wizard_id": quote_wizard.id,
                }
            )
            self.assertEqual(wizard.envia_wizard_id.destination_location_type, "address")
            self.assertEqual(wizard.envia_wizard_id._get_quote_carriers(), "all")
            wizard.update_price()
        self.assertEqual(wizard.envia_wizard_id.destination_location_type, "address")
        self.assertTrue(wizard.envia_service_line_ids)
        self.assertEqual(wizard.envia_service_line_ids[:1].carrier, "fedex")

    def test_expected_route_drop_off_from_location_types(self):
        skip = {
            "envia_skip_route_carrier_refresh": True,
            "envia_skip_auto_quote": True,
            "envia_skip_branch_autoload": True,
        }
        wizard = self.env["envia.quote.wizard"].with_context(**skip).create({})
        self.assertFalse(wizard._expected_route_drop_off())
        wizard.with_context(**skip).write({"destination_location_type": "branch"})
        self.assertEqual(wizard._expected_route_drop_off(), 2)
        wizard.with_context(**skip).write({"origin_location_type": "branch"})
        self.assertEqual(wizard._expected_route_drop_off(), 3)
        wizard.with_context(**skip).write({"destination_location_type": "address"})
        self.assertEqual(wizard._expected_route_drop_off(), 1)

    def test_branch_carrier_codes_use_probed_available_list(self):
        mx = self.env.ref("base.mx")
        wizard = self.env["envia.quote.wizard"].create(
            {
                "branch_carriers_probed": True,
                "available_branch_carriers": "dhl,estafeta",
                "failed_branch_carriers": "estafeta",
            }
        )
        codes = wizard._get_branch_carrier_codes(mx)
        self.assertIn("dhl", codes)
        self.assertNotIn("estafeta", codes)
        self.assertNotIn("ups", codes)

    def test_no_branch_rates_removes_failed_carrier_branches(self):
        mx, state = self._mx_state()
        wizard = self.env["envia.quote.wizard"].with_context(
            envia_skip_branch_autoload=True
        ).create(
            {
                "origin_location_type": "branch",
                "destination_location_type": "branch",
                "origin_country_id": mx.id,
                "destination_country_id": mx.id,
                "origin_postal_code": "67175",
                "destination_postal_code": "03100",
                "origin_state_id": state.id,
                "destination_state_id": state.id,
            }
        )
        self.env["envia.quote.wizard.branch"].create(
            [
                {
                    "wizard_id": wizard.id,
                    "side": "origin",
                    "name": "UPS Origin",
                    "branch_code": "UPS1",
                    "carrier": "ups",
                    "zip": "67175",
                    "city": "Monterrey",
                    "country_code": "MX",
                    "is_selected": True,
                },
                {
                    "wizard_id": wizard.id,
                    "side": "destination",
                    "name": "UPS Dest",
                    "branch_code": "UPS2",
                    "carrier": "ups",
                    "zip": "03100",
                    "city": "CDMX",
                    "country_code": "MX",
                    "is_selected": True,
                },
                {
                    "wizard_id": wizard.id,
                    "side": "origin",
                    "name": "DHL Origin",
                    "branch_code": "DHL1",
                    "carrier": "dhl",
                    "zip": "67175",
                    "city": "Monterrey",
                    "country_code": "MX",
                },
            ]
        )
        wizard._handle_no_branch_rates()
        self.assertFalse(wizard.origin_branch_line_ids.filtered(lambda b: b.carrier == "ups"))
        self.assertFalse(
            wizard.destination_branch_line_ids.filtered(lambda b: b.carrier == "ups")
        )
        self.assertTrue(wizard.origin_branch_line_ids.filtered(lambda b: b.carrier == "dhl"))
        self.assertIn("ups", wizard.failed_branch_carriers)
        self.assertTrue(wizard.rates_feedback)
        self.assertNotIn("ups", wizard._get_branch_carrier_codes(mx))

    def test_select_destination_branch_after_rate_persists_branch_code(self):
        mx, state = self._mx_state()
        response = QuoteResponse(
            quote_id="bb",
            services=[
                QuoteService(
                    service_id="paquetexpress:bb",
                    envia_service_id=567,
                    carrier="paquetexpress",
                    carrier_name="Paquetexpress",
                    service_name="Branch Branch",
                    price=13.92,
                    currency=self.order.currency_id.name,
                    drop_off=3,
                ),
            ],
        )
        wizard = self.env["envia.quote.wizard"].with_context(
            envia_skip_branch_autoload=True,
            envia_skip_auto_quote=True,
        ).create(
            {
                "sale_order_id": self.order.id,
                "origin_location_type": "branch",
                "destination_location_type": "branch",
                "origin_partner_id": self.partner.id,
                "destination_partner_id": self.partner.id,
                "origin_street": "Padre Mier",
                "origin_city": "Monterrey",
                "origin_postal_code": "64000",
                "origin_country_id": mx.id,
                "origin_state_id": state.id,
                "destination_street": "Insurgentes",
                "destination_city": "CDMX",
                "destination_postal_code": "01000",
                "destination_country_id": mx.id,
                "destination_state_id": state.id,
                "weight": 1.0,
                "content": "Test",
            }
        )
        self.assertFalse(wizard.show_origin_branch_picker)
        self.assertFalse(wizard.show_destination_branch_picker)
        with patch(
            "odoo.addons.envia.wizards.envia_quote_wizard.get_envia_adapter"
        ) as get_adapter:
            get_adapter.return_value.quote.return_value = response
            with patch.object(type(wizard), "_try_load_branch_side", return_value=False):
                wizard.action_get_quote(clear_branch_lines=False)
                wizard.action_select_service_rate(service_id="paquetexpress:bb")
        self.assertTrue(wizard.show_destination_branch_picker)
        self.assertFalse(wizard.show_origin_branch_picker)
        destination = self.env["envia.quote.wizard.branch"].create(
            {
                "wizard_id": wizard.id,
                "side": "destination",
                "name": "MEXICO 5",
                "branch_code": "MEX05",
                "carrier": "paquetexpress",
                "zip": "01000",
                "city": "CDMX",
                "country_code": "MX",
                "state_code": state.code,
            }
        )
        destination.action_select_branch()
        self.assertEqual(wizard.quote_id.destination_branch_code, "MEX05")
        self.assertFalse(wizard.quote_id.origin_branch_code)
        module = self.order.read(["envia_module"])[0]["envia_module"]
        self.assertEqual(module["branch_code"], "MEX05")

    def test_route_type_label_from_drop_off(self):
        Service = self.env["envia.quote.wizard.service"]
        line = Service.new({})
        self.assertEqual(line._route_type_label_for(0), "Domicile - Domicile")
        self.assertEqual(line._route_type_label_for(1), "Branch - Domicile")
        self.assertEqual(line._route_type_label_for(2), "Domicile - Branch")
        self.assertEqual(line._route_type_label_for(3), "Branch - Branch")
        self.assertEqual(line._route_type_label_for(False), "Domicile - Domicile")

    def test_choose_delivery_carrier_create_recovers_missing_order_id(self):
        # Web client may omit invisible order_id after onchange; recover from context.
        quote_wizard = self.env["envia.quote.wizard"].create(
            {
                "sale_order_id": self.order.id,
                "destination_partner_id": self.order.partner_shipping_id.id,
            }
        )
        wizard = self.env["choose.delivery.carrier"].with_context(
            default_order_id=self.order.id,
            default_carrier_id=self.carrier.id,
        ).create(
            {
                "carrier_id": self.carrier.id,
                "envia_wizard_id": quote_wizard.id,
            }
        )
        self.assertEqual(wizard.order_id, self.order)

    def test_choose_delivery_carrier_get_rate_lists_services(self):
        response = QuoteResponse(
            quote_id="test",
            services=[
                QuoteService(
                    service_id="fedex:1",
                    carrier="fedex",
                    carrier_name="FedEx",
                    service_name="Economy",
                    price=120.0,
                    currency=self.order.currency_id.name,
                ),
            ],
        )
        wizard = self.env["choose.delivery.carrier"].create(
            {
                "order_id": self.order.id,
                "carrier_id": self.carrier.id,
            }
        )
        with patch(
            "odoo.addons.envia.wizards.envia_quote_wizard.get_envia_adapter"
        ) as get_adapter:
            get_adapter.return_value.quote.return_value = response
            wizard.update_price()
        self.assertEqual(len(wizard.envia_service_line_ids), 1)
        self.assertEqual(wizard.envia_service_line_ids.price, 120.0)

    def test_choose_delivery_carrier_select_service_updates_cost(self):
        response = QuoteResponse(
            quote_id="test",
            services=[
                QuoteService(
                    service_id="fedex:1",
                    carrier="fedex",
                    carrier_name="FedEx",
                    service_name="Economy",
                    price=120.0,
                    currency=self.order.currency_id.name,
                ),
            ],
        )
        wizard = self.env["choose.delivery.carrier"].create(
            {
                "order_id": self.order.id,
                "carrier_id": self.carrier.id,
            }
        )
        with patch(
            "odoo.addons.envia.wizards.envia_quote_wizard.get_envia_adapter"
        ) as get_adapter:
            get_adapter.return_value.quote.return_value = response
            wizard.update_price()
        action = wizard.with_context(service_id="fedex:1").action_envia_select_service()
        self.assertFalse(action)
        self.assertEqual(wizard.display_price, 120.0)
        self.assertEqual(wizard.delivery_price, 120.0)
        self.assertTrue(wizard.envia_has_selected_rate)

    def _mx_state(self):
        mx = self.env.ref("base.mx")
        return mx, self.env["res.country.state"].search(
            [("country_id", "=", mx.id)], limit=1
        )

    def test_choose_delivery_carrier_branch_first_quotes_branch_carrier(self):
        mx, state = self._mx_state()
        response = QuoteResponse(
            quote_id="test",
            services=[
                QuoteService(
                    service_id="dhl:ocurre",
                    carrier="dhl",
                    carrier_name="DHL",
                    service_name="Economy Ocurre",
                    price=281.16,
                    currency=self.order.currency_id.name,
                    drop_off=2,
                ),
            ],
        )
        wizard = self.env["choose.delivery.carrier"].create(
            {"order_id": self.order.id, "carrier_id": self.carrier.id}
        )
        quote_wizard = wizard.envia_wizard_id
        quote_wizard.write(
            {
                "destination_location_type": "branch",
                "destination_partner_id": self.order.partner_shipping_id.id,
                "destination_street": "Pino Suarez",
                "destination_country_id": mx.id,
                "destination_postal_code": "64000",
                "destination_city": "Monterrey",
                "destination_state_id": state.id,
            }
        )
        self.assertEqual(quote_wizard._expected_route_drop_off(), 2)
        with patch(
            "odoo.addons.envia.wizards.envia_quote_wizard.get_envia_adapter"
        ) as get_adapter:
            get_adapter.return_value.quote.return_value = response
            wizard.update_price()
        self.assertEqual(quote_wizard._get_quote_carriers(), "all")
        wizard.with_context(service_id="dhl:ocurre").action_envia_select_service()
        selected = quote_wizard.service_line_ids.filtered("is_selected")
        self.assertEqual(selected.carrier, "dhl")

    def test_choose_delivery_carrier_update_restores_saved_branch_and_service(self):
        mx, state = self._mx_state()
        quote = self.env["envia.quote"].create(
            {
                "origin_postal_code": "06600",
                "origin_country": "MX",
                "destination_postal_code": "64000",
                "destination_country": "MX",
                "destination_location_type": "branch",
                "destination_branch_code": "MTY01",
                "destination_branch_name": "DHL MTY01",
                "origin_partner_id": self.partner.id,
                "destination_partner_id": self.partner.id,
                "sale_order_id": self.order.id,
                "weight": 1.0,
                "content": "Test",
                "state": "quoted",
            }
        )
        service = self.env["envia.quote.service"].create(
            {
                "quote_id": quote.id,
                "service_id": "dhl:ocurre",
                "envia_service_id": 123,
                "carrier": "dhl",
                "carrier_name": "DHL",
                "service_name": "Economy Ocurre",
                "price": 281.16,
                "currency_name": self.order.currency_id.name,
            }
        )
        quote.selected_service_id = service
        self.order.set_delivery_line(self.carrier, 281.16)

        def fake_load(quote_wizard, side, carrier_codes=None):
            raise AssertionError("Branch API must not run on Update open")

        with patch(
            "odoo.addons.envia.wizards.envia_quote_wizard.EnviaQuoteWizard._load_branches",
            autospec=True,
            side_effect=fake_load,
        ), patch(
            "odoo.addons.envia.wizards.envia_quote_wizard.EnviaGeocodesClient"
        ) as geocodes, patch(
            "odoo.addons.envia.wizards.envia_quote_wizard.get_envia_adapter"
        ) as get_adapter:
            geocodes.return_value.lookup_zipcode.return_value = []
            # UI paints from onchange: restore must happen there, not only on create.
            pending = self.env["choose.delivery.carrier"].new(
                {"order_id": self.order.id, "carrier_id": self.carrier.id}
            )
            pending._onchange_carrier_id()
            self.assertEqual(
                pending.envia_wizard_id.destination_location_type, "branch"
            )
            self.assertEqual(
                pending.envia_destination_location_type, "branch"
            )
            self.assertEqual(
                pending.envia_wizard_id._get_selected_branch("destination").branch_code,
                "MTY01",
            )
            get_adapter.return_value.quote.assert_not_called()
            wizard = self.env["choose.delivery.carrier"].with_context(
                carrier_recompute=True
            ).create(
                {
                    "order_id": self.order.id,
                    "carrier_id": self.carrier.id,
                    "envia_wizard_id": pending.envia_wizard_id.id,
                }
            )
            get_adapter.return_value.quote.assert_not_called()
        quote_wizard = wizard.envia_wizard_id
        self.assertEqual(quote_wizard.destination_location_type, "branch")
        self.assertEqual(
            quote_wizard._get_selected_branch("destination").branch_code, "MTY01"
        )
        selected = quote_wizard.service_line_ids.filtered("is_selected")
        self.assertEqual(selected.service_id, "dhl:ocurre")

    def test_choose_delivery_carrier_update_restores_draft_quote_with_service(self):
        # Applied shipping can leave the quote in draft (e.g. drop_off mismatch);
        # Update must still restore Ship/Pickup from that quote.
        quote = self.env["envia.quote"].create(
            {
                "origin_postal_code": "06600",
                "origin_country": "MX",
                "destination_postal_code": "44100",
                "destination_country": "MX",
                "origin_location_type": "address",
                "destination_location_type": "address",
                "origin_partner_id": self.partner.id,
                "destination_partner_id": self.partner.id,
                "sale_order_id": self.order.id,
                "weight": 1.0,
                "content": "Test",
                "state": "draft",
            }
        )
        service = self.env["envia.quote.service"].create(
            {
                "quote_id": quote.id,
                "service_id": "fedex:1",
                "carrier": "fedex",
                "carrier_name": "FedEx",
                "service_name": "Economy",
                "price": 120.0,
                "currency_name": self.order.currency_id.name,
                "drop_off": 2,
            }
        )
        quote.selected_service_id = service
        self.order.set_delivery_line(self.carrier, 120.0)
        self.assertFalse(quote._is_label_ready())
        with patch(
            "odoo.addons.envia.wizards.envia_quote_wizard.EnviaGeocodesClient"
        ) as geocodes, patch(
            "odoo.addons.envia.wizards.envia_quote_wizard.get_envia_adapter"
        ) as get_adapter:
            geocodes.return_value.lookup_zipcode.return_value = []
            wizard = self.env["choose.delivery.carrier"].create(
                {"order_id": self.order.id, "carrier_id": self.carrier.id}
            )
            get_adapter.return_value.quote.assert_not_called()
        self.assertEqual(wizard.envia_wizard_id.origin_location_type, "address")
        self.assertEqual(wizard.envia_wizard_id.destination_location_type, "address")
        selected = wizard.envia_wizard_id.service_line_ids.filtered("is_selected")
        self.assertEqual(selected.service_id, "fedex:1")

    def test_update_shipping_get_rate_requests_all_carriers(self):
        """Update shipping → Get rate must requote every carrier, not only the saved one."""
        mx, state = self._mx_state()
        quote = self.env["envia.quote"].create(
            {
                "origin_postal_code": "06600",
                "origin_country": "MX",
                "destination_postal_code": "44100",
                "destination_country": "MX",
                "origin_location_type": "address",
                "destination_location_type": "address",
                "origin_partner_id": self.partner.id,
                "destination_partner_id": self.partner.id,
                "sale_order_id": self.order.id,
                "weight": 1.0,
                "content": "Test",
                "state": "quoted",
            }
        )
        self.env["envia.quote.service"].create(
            {
                "quote_id": quote.id,
                "service_id": "fedex:1",
                "carrier": "fedex",
                "carrier_name": "FedEx",
                "service_name": "Economy",
                "price": 120.0,
                "currency_name": self.order.currency_id.name,
                "is_selected": True,
            }
        )
        quote.selected_service_id = quote.service_ids[:1]
        self.order.set_delivery_line(self.carrier, 120.0)
        response = QuoteResponse(
            quote_id="requote",
            services=[
                QuoteService(
                    service_id="fedex:1",
                    carrier="fedex",
                    carrier_name="FedEx",
                    service_name="Economy",
                    price=120.0,
                    currency=self.order.currency_id.name,
                ),
                QuoteService(
                    service_id="dhl:1",
                    carrier="dhl",
                    carrier_name="DHL",
                    service_name="Express",
                    price=99.0,
                    currency=self.order.currency_id.name,
                ),
                QuoteService(
                    service_id="estafeta:ground",
                    carrier="estafeta",
                    carrier_name="Estafeta",
                    service_name="Terrestre",
                    price=80.0,
                    currency=self.order.currency_id.name,
                ),
            ],
        )
        captured = {}
        with patch(
            "odoo.addons.envia.wizards.envia_quote_wizard.EnviaGeocodesClient"
        ) as geocodes, patch(
            "odoo.addons.envia.wizards.envia_quote_wizard.get_envia_adapter"
        ) as get_adapter:
            geocodes.return_value.lookup_zipcode.return_value = []
            wizard = self.env["choose.delivery.carrier"].with_context(
                carrier_recompute=True
            ).create({"order_id": self.order.id, "carrier_id": self.carrier.id})
            quote_wizard = wizard.envia_wizard_id
            quote_wizard.write(
                {
                    "origin_partner_id": self.partner.id,
                    "destination_partner_id": self.partner.id,
                    "origin_street": "Origin",
                    "destination_street": "Dest",
                    "origin_city": "CDMX",
                    "destination_city": "GDL",
                    "origin_postal_code": "06600",
                    "destination_postal_code": "44100",
                    "origin_country_id": mx.id,
                    "destination_country_id": mx.id,
                    "origin_state_id": state.id,
                    "destination_state_id": state.id,
                }
            )
            # Simulate Update shipping: a prior rate is already selected.
            if quote_wizard.service_line_ids:
                quote_wizard.service_line_ids[:1].is_selected = True
            else:
                self.env["envia.quote.wizard.service"].create(
                    {
                        "wizard_id": quote_wizard.id,
                        "service_id": "fedex:1",
                        "carrier": "fedex",
                        "carrier_name": "FedEx",
                        "service_name": "Economy",
                        "price": 120.0,
                        "is_selected": True,
                    }
                )
            self.assertEqual(quote_wizard._get_quote_carriers(), "all")

            def _quote(request):
                captured["carriers"] = request.carriers
                return response

            get_adapter.return_value.quote.side_effect = _quote
            wizard.update_price()
        self.assertEqual(captured.get("carriers"), "all")
        carriers = set(wizard.envia_wizard_id.service_line_ids.mapped("carrier"))
        self.assertEqual(carriers, {"fedex", "dhl", "estafeta"})
        self.assertFalse(wizard.envia_wizard_id.service_line_ids.filtered("is_selected"))

    def test_choose_delivery_carrier_update_can_switch_cached_rate(self):
        quote = self.env["envia.quote"].create(
            {
                "origin_postal_code": "06600",
                "origin_country": "MX",
                "destination_postal_code": "44100",
                "destination_country": "MX",
                "origin_location_type": "address",
                "destination_location_type": "address",
                "origin_partner_id": self.partner.id,
                "destination_partner_id": self.partner.id,
                "sale_order_id": self.order.id,
                "weight": 1.0,
                "content": "Test",
                "state": "quoted",
            }
        )
        cheaper = self.env["envia.quote.service"].create(
            {
                "quote_id": quote.id,
                "service_id": "fedex:1",
                "carrier": "fedex",
                "carrier_name": "FedEx",
                "service_name": "Economy",
                "price": 120.0,
                "currency_name": self.order.currency_id.name,
            }
        )
        faster = self.env["envia.quote.service"].create(
            {
                "quote_id": quote.id,
                "service_id": "ups:ground",
                "carrier": "ups",
                "carrier_name": "UPS",
                "service_name": "Ground",
                "price": 150.0,
                "currency_name": self.order.currency_id.name,
            }
        )
        quote.selected_service_id = cheaper
        self.order.set_delivery_line(self.carrier, 120.0)
        with patch(
            "odoo.addons.envia.wizards.envia_quote_wizard.EnviaGeocodesClient"
        ) as geocodes, patch(
            "odoo.addons.envia.wizards.envia_quote_wizard.get_envia_adapter"
        ) as get_adapter:
            geocodes.return_value.lookup_zipcode.return_value = []
            wizard = self.env["choose.delivery.carrier"].create(
                {"order_id": self.order.id, "carrier_id": self.carrier.id}
            )
            get_adapter.return_value.quote.assert_not_called()
        self.assertEqual(
            wizard.envia_wizard_id.service_line_ids.filtered("is_selected").service_id,
            "fedex:1",
        )
        action = wizard.with_context(service_id="ups:ground").action_envia_select_service()
        self.assertFalse(action)
        selected = wizard.envia_wizard_id.service_line_ids.filtered("is_selected")
        self.assertEqual(selected.service_id, "ups:ground")
        self.assertEqual(len(wizard.envia_wizard_id.service_line_ids), 2)
        self.assertTrue(wizard.display_price)

    def test_quote_wizard_rewrite_location_types_keeps_service_lines(self):
        wizard = self.env["envia.quote.wizard"].create(
            {
                "sale_order_id": self.order.id,
                "origin_location_type": "address",
                "destination_location_type": "address",
                "origin_postal_code": "06600",
                "destination_postal_code": "44100",
                "weight": 1.0,
                "content": "Test",
            }
        )
        Service = self.env["envia.quote.wizard.service"]
        Service.create(
            [
                {
                    "wizard_id": wizard.id,
                    "service_id": "fedex:1",
                    "carrier": "fedex",
                    "service_name": "Economy",
                    "price": 120.0,
                },
                {
                    "wizard_id": wizard.id,
                    "service_id": "ups:ground",
                    "carrier": "ups",
                    "service_name": "Ground",
                    "price": 150.0,
                },
            ]
        )
        wizard.write(
            {
                "origin_location_type": "address",
                "destination_location_type": "address",
            }
        )
        self.assertEqual(len(wizard.service_line_ids), 2)

    def test_update_restore_keeps_rates_when_order_weight_differs(self):
        quote = self.env["envia.quote"].create(
            {
                "origin_postal_code": "67192",
                "origin_country": "MX",
                "destination_postal_code": "06500",
                "destination_country": "MX",
                "origin_location_type": "address",
                "destination_location_type": "address",
                "origin_partner_id": self.partner.id,
                "destination_partner_id": self.partner.id,
                "sale_order_id": self.order.id,
                "weight": 2.5,
                "content": "Test",
                "state": "quoted",
            }
        )
        for service_id, carrier, price in (
            ("fedex:1", "fedex", 120.0),
            ("ups:ground", "ups", 150.0),
        ):
            self.env["envia.quote.service"].create(
                {
                    "quote_id": quote.id,
                    "service_id": service_id,
                    "carrier": carrier,
                    "service_name": service_id,
                    "price": price,
                    "currency_name": self.order.currency_id.name,
                }
            )
        quote.selected_service_id = quote.service_ids[:1]
        self.order.set_delivery_line(self.carrier, 120.0)
        self.order.write({"shipping_weight": 0.01})
        with patch(
            "odoo.addons.envia.wizards.envia_quote_wizard.EnviaGeocodesClient"
        ) as geocodes:
            geocodes.return_value.lookup_zipcode.return_value = []
            wizard = self.env["choose.delivery.carrier"].with_context(
                carrier_recompute=True
            ).create({"order_id": self.order.id, "carrier_id": self.carrier.id})
        self.assertEqual(len(wizard.envia_wizard_id.service_line_ids), 2)
        self.assertTrue(wizard.envia_show_service_rates)

    def test_choose_delivery_carrier_write_keeps_restored_service_lines(self):
        quote = self.env["envia.quote"].create(
            {
                "origin_postal_code": "06600",
                "origin_country": "MX",
                "destination_postal_code": "44100",
                "destination_country": "MX",
                "origin_location_type": "address",
                "destination_location_type": "address",
                "origin_partner_id": self.partner.id,
                "destination_partner_id": self.partner.id,
                "sale_order_id": self.order.id,
                "weight": 1.0,
                "content": "Test",
                "state": "quoted",
            }
        )
        for service_id, carrier, price in (
            ("fedex:1", "fedex", 120.0),
            ("ups:ground", "ups", 150.0),
        ):
            self.env["envia.quote.service"].create(
                {
                    "quote_id": quote.id,
                    "service_id": service_id,
                    "carrier": carrier,
                    "service_name": service_id,
                    "price": price,
                    "currency_name": self.order.currency_id.name,
                }
            )
        quote.selected_service_id = quote.service_ids[:1]
        self.order.set_delivery_line(self.carrier, 120.0)
        with patch(
            "odoo.addons.envia.wizards.envia_quote_wizard.EnviaGeocodesClient"
        ) as geocodes:
            geocodes.return_value.lookup_zipcode.return_value = []
            wizard = self.env["choose.delivery.carrier"].create(
                {"order_id": self.order.id, "carrier_id": self.carrier.id}
            )
        self.assertEqual(len(wizard.envia_wizard_id.service_line_ids), 2)
        wizard.write(
            {
                "envia_origin_location_type": "address",
                "envia_destination_location_type": "address",
            }
        )
        self.assertEqual(len(wizard.envia_wizard_id.service_line_ids), 2)

    def test_update_shipping_opens_envia_when_order_carrier_missing_pickup(self):
        mx, state = self._mx_state()
        quote = self.env["envia.quote"].create(
            {
                "origin_postal_code": "06600",
                "origin_country": "MX",
                "destination_postal_code": "64000",
                "destination_country": "MX",
                "destination_location_type": "branch",
                "destination_branch_code": "MTY01",
                "destination_branch_name": "DHL MTY01",
                "origin_partner_id": self.partner.id,
                "destination_partner_id": self.partner.id,
                "sale_order_id": self.order.id,
                "weight": 2.5,
                "content": "Test",
                "state": "quoted",
            }
        )
        service = self.env["envia.quote.service"].create(
            {
                "quote_id": quote.id,
                "service_id": "dhl:ocurre",
                "carrier": "dhl",
                "service_name": "Economy Ocurre",
                "price": 281.16,
                "currency_name": self.order.currency_id.name,
                "drop_off": 2,
            }
        )
        quote.selected_service_id = service
        self.order.set_delivery_line(self.carrier, 281.16)
        self.order.carrier_id = False
        with patch(
            "odoo.addons.envia.wizards.envia_quote_wizard.EnviaGeocodesClient"
        ) as geocodes:
            geocodes.return_value.lookup_zipcode.return_value = []
            action = self.order.with_context(carrier_recompute=True).action_open_delivery_wizard()
            self.assertTrue(action.get("res_id"))
            pending = self.env["choose.delivery.carrier"].browse(action["res_id"])
            self.assertEqual(pending.carrier_id, self.carrier)
            self.assertEqual(pending.total_weight, 2.5)
        self.assertEqual(pending.envia_wizard_id.destination_location_type, "branch")
        self.assertTrue(pending.envia_wizard_id.service_line_ids)

    def test_update_shipping_finds_pickup_quote_without_selected_service(self):
        quote = self.env["envia.quote"].create(
            {
                "origin_postal_code": "06600",
                "origin_country": "MX",
                "destination_postal_code": "64000",
                "destination_country": "MX",
                "destination_location_type": "branch",
                "destination_branch_code": "MTY01",
                "origin_partner_id": self.partner.id,
                "destination_partner_id": self.partner.id,
                "sale_order_id": self.order.id,
                "weight": 3.0,
                "content": "Test",
                "state": "draft",
            }
        )
        self.order.set_delivery_line(self.carrier, 100.0)
        self.order.carrier_id = False
        self.assertEqual(self.order._get_restorable_envia_quote(), quote)
        defaults = self.env["choose.delivery.carrier"].with_context(
            default_order_id=self.order.id,
            carrier_recompute=True,
        ).default_get(["carrier_id", "total_weight"])
        self.assertEqual(defaults["carrier_id"], self.carrier.id)
        self.assertEqual(defaults["total_weight"], 3.0)

    def test_location_type_change_clears_mismatched_rates(self):
        wizard = self.env["envia.quote.wizard"].create(
            {
                "sale_order_id": self.order.id,
                "origin_location_type": "address",
                "destination_location_type": "address",
                "origin_postal_code": "06600",
                "destination_postal_code": "44100",
                "weight": 1.0,
                "content": "Test",
            }
        )
        self.env["envia.quote.wizard.service"].create(
            {
                "wizard_id": wizard.id,
                "service_id": "fedex:1",
                "carrier": "fedex",
                "service_name": "Economy",
                "price": 120.0,
                "drop_off": 0,
            }
        )
        wizard.destination_location_type = "branch"
        wizard._clear_stale_rates_if_route_mismatch()
        self.assertFalse(wizard.service_line_ids)

    def test_write_location_type_change_clears_rates(self):
        wizard = self.env["envia.quote.wizard"].create(
            {
                "sale_order_id": self.order.id,
                "origin_location_type": "address",
                "destination_location_type": "address",
                "origin_postal_code": "06600",
                "destination_postal_code": "44100",
                "weight": 1.0,
                "content": "Test",
            }
        )
        self.env["envia.quote.wizard.service"].create(
            {
                "wizard_id": wizard.id,
                "service_id": "fedex:1",
                "carrier": "fedex",
                "service_name": "Economy",
                "price": 120.0,
                "drop_off": 0,
            }
        )
        wizard.write({"destination_location_type": "branch"})
        self.assertFalse(wizard.service_line_ids)

    def _create_hybrid_wizard(self, **values):
        mexico = self.env.ref("base.mx")
        state = self.env.ref("base.state_mx_df")
        company = self.env.company
        defaults = {
            "sale_order_id": self.order.id,
            "origin_partner_id": company.partner_id.id,
            "origin_location_type": "address",
            "origin_street": "Av Negocio 100",
            "origin_postal_code": "06600",
            "origin_city": "Ciudad de Mexico",
            "origin_country_id": mexico.id,
            "origin_state_id": state.id,
            "destination_partner_id": self.order.partner_shipping_id.id,
            "destination_location_type": "address",
            "destination_street": "Calle Cliente 200",
            "destination_postal_code": "44100",
            "destination_city": "Guadalajara",
            "destination_country_id": mexico.id,
            "destination_state_id": self.env.ref("base.state_mx_jal").id,
            "weight": 1.0,
            "content": "Test",
        }
        defaults.update(values)
        return self.env["envia.quote.wizard"].with_context(
            envia_skip_branch_autoload=True
        ).create(defaults)

    def test_hybrid_contact_scenario2_dom_to_ocurre(self):
        wizard = self._create_hybrid_wizard(
            destination_location_type="branch",
            destination_street="Pino Suarez",
            destination_postal_code="64400",
            destination_city="Monterrey",
        )
        contact = wizard._build_contact_for_side("destination")
        self.assertEqual(contact.name, self.order.partner_shipping_id.name)
        self.assertFalse(contact.branch_code)
        self.assertEqual(contact.street, "Pino Suarez")
        self.assertEqual(wizard._expected_route_drop_off(), 2)

    def test_hybrid_contact_scenario3_ocurre_to_dom(self):
        wizard = self._create_hybrid_wizard(origin_location_type="branch")
        contact = wizard._build_contact_for_side("origin")
        self.assertEqual(contact.name, self.env.company.partner_id.name)
        self.assertFalse(contact.branch_code)
        self.assertEqual(contact.street, "Av Negocio 100")
        dest = wizard._build_contact_for_side("destination")
        self.assertFalse(dest.branch_code)
        self.assertEqual(wizard._expected_route_drop_off(), 1)

    def test_hybrid_contact_scenario1_both_ocurre(self):
        wizard = self._create_hybrid_wizard(
            origin_location_type="branch",
            destination_location_type="branch",
            destination_street="Branch Street 50",
            destination_postal_code="03100",
        )
        origin = wizard._build_contact_for_side("origin")
        dest = wizard._build_contact_for_side("destination")
        self.assertFalse(origin.branch_code)
        self.assertFalse(dest.branch_code)
        self.assertEqual(wizard._expected_route_drop_off(), 3)

    def test_get_quote_passes_expected_drop_off(self):
        wizard = self._create_hybrid_wizard(destination_location_type="branch")
        response = QuoteResponse(
            quote_id="test",
            services=[
                QuoteService(
                    service_id="dhl:ocurre",
                    carrier="dhl",
                    carrier_name="DHL",
                    service_name="Economy Ocurre",
                    price=281.16,
                    currency=self.order.currency_id.name,
                    drop_off=2,
                ),
            ],
        )
        captured = {}

        def _quote(request):
            captured["expected_drop_off"] = request.expected_drop_off
            return response

        with patch(
            "odoo.addons.envia.wizards.envia_quote_wizard.get_envia_adapter"
        ) as get_adapter:
            get_adapter.return_value.quote.side_effect = _quote
            wizard._perform_get_quote()
        self.assertEqual(captured.get("expected_drop_off"), 2)

    def test_get_quote_skips_package_dimensions_preview(self):
        """Package dimensions preview is temporarily disabled (no Envia API call)."""
        response = QuoteResponse(
            quote_id="test",
            services=[
                QuoteService(
                    service_id="fedex:1",
                    carrier="fedex",
                    carrier_name="FedEx",
                    service_name="Economy",
                    price=120.0,
                    currency=self.order.currency_id.name,
                ),
            ],
        )
        wizard = self.env["envia.quote.wizard"].with_context(
            envia_skip_branch_autoload=True,
            envia_skip_auto_quote=True,
        ).create(
            {
                "sale_order_id": self.order.id,
                "destination_partner_id": self.order.partner_shipping_id.id,
                "weight": 1.0,
                "content": "Test",
            }
        )
        with patch(
            "odoo.addons.envia.wizards.envia_quote_wizard.get_envia_adapter"
        ) as get_adapter:
            adapter = get_adapter.return_value
            adapter.quote.return_value = response
            wizard._perform_get_quote()
        adapter.fetch_package_dimensions.assert_not_called()
        self.assertFalse(wizard.envia_package_preview)
        self.assertFalse(wizard.envia_package_sync_hint)
        self.assertEqual(len(wizard.service_line_ids), 1)

    def test_confirm_with_origin_pickup_without_origin_branch(self):
        # Origin=branch does not require selecting a concrete origin branch code.
        response = QuoteResponse(
            quote_id="test",
            services=[
                QuoteService(
                    service_id="fedex:1",
                    carrier="fedex",
                    carrier_name="FedEx",
                    service_name="Economy",
                    price=120.0,
                    currency=self.order.currency_id.name,
                    drop_off=1,
                ),
            ],
        )
        wizard = self.env["choose.delivery.carrier"].create(
            {"order_id": self.order.id, "carrier_id": self.carrier.id}
        )
        quote_wizard = wizard.envia_wizard_id
        quote_wizard.write(
            {
                "origin_location_type": "branch",
                "origin_street": "Av Negocio 100",
                "origin_postal_code": "67192",
                "origin_city": "Guadalupe",
                "origin_country_id": self.env.ref("base.mx").id,
                "origin_state_id": self.env.ref("base.state_mx_nl").id,
            }
        )
        with patch(
            "odoo.addons.envia.wizards.envia_quote_wizard.get_envia_adapter"
        ) as get_adapter:
            get_adapter.return_value.quote.return_value = response
            wizard.update_price()
        wizard.with_context(service_id="fedex:1").action_envia_select_service()
        with patch(
            "odoo.addons.envia.wizards.envia_quote_wizard.get_envia_adapter"
        ) as get_adapter:
            get_adapter.return_value.quote.return_value = response
            wizard.button_confirm()
        self.assertTrue(self.order.order_line.filtered("is_delivery"))
        self.assertFalse(wizard.envia_wizard_id.quote_id.origin_branch_code)

    def test_confirm_with_destination_branch_persists_branch_code(self):
        mx, state = self._mx_state()
        response = QuoteResponse(
            quote_id="test",
            services=[
                QuoteService(
                    service_id="paquetexpress:ground_do",
                    carrier="paquetexpress",
                    carrier_name="Paquetexpress",
                    service_name="Domicilio - Ocurre",
                    price=39.44,
                    currency=self.order.currency_id.name,
                    drop_off=2,
                ),
            ],
        )
        wizard = self.env["choose.delivery.carrier"].create(
            {"order_id": self.order.id, "carrier_id": self.carrier.id}
        )
        quote_wizard = wizard.envia_wizard_id
        quote_wizard.write(
            {
                "destination_location_type": "branch",
                "destination_partner_id": self.order.partner_shipping_id.id,
                "destination_street": "Pino Suarez",
                "destination_country_id": mx.id,
                "destination_postal_code": "03100",
                "destination_city": "CDMX",
                "destination_state_id": state.id,
            }
        )
        self.env["envia.quote.wizard.branch"].create(
            {
                "wizard_id": quote_wizard.id,
                "side": "destination",
                "name": "Paquetexpress Branch",
                "branch_code": "MEX05",
                "carrier": "paquetexpress",
                "zip": "03100",
                "city": "CDMX",
                "country_code": "MX",
                "state_code": state.code,
                "is_selected": True,
            }
        )
        with patch(
            "odoo.addons.envia.wizards.envia_quote_wizard.get_envia_adapter"
        ) as get_adapter:
            get_adapter.return_value.quote.return_value = response
            wizard.update_price()
        wizard.with_context(service_id="paquetexpress:ground_do").action_envia_select_service()
        with patch(
            "odoo.addons.envia.wizards.envia_quote_wizard.get_envia_adapter"
        ) as get_adapter:
            get_adapter.return_value.quote.return_value = response
            wizard.button_confirm()
        quote = quote_wizard.quote_id
        self.assertEqual(quote.destination_branch_code, "MEX05")
        self.assertEqual(quote.state, "quoted")
        module = self.order.read(["envia_module"])[0]["envia_module"]
        self.assertEqual(module["branch_code"], "MEX05")

    def test_choose_delivery_carrier_confirm_applies_delivery_line_and_quote(self):
        response = QuoteResponse(
            quote_id="test",
            services=[
                QuoteService(
                    service_id="fedex:1",
                    carrier="fedex",
                    carrier_name="FedEx",
                    service_name="Economy",
                    price=120.0,
                    currency=self.order.currency_id.name,
                ),
            ],
        )
        wizard = self.env["choose.delivery.carrier"].create(
            {
                "order_id": self.order.id,
                "carrier_id": self.carrier.id,
            }
        )
        with patch(
            "odoo.addons.envia.wizards.envia_quote_wizard.get_envia_adapter"
        ) as get_adapter:
            get_adapter.return_value.quote.return_value = response
            wizard.update_price()
        wizard.with_context(service_id="fedex:1").action_envia_select_service()
        with patch(
            "odoo.addons.envia.wizards.envia_quote_wizard.get_envia_adapter"
        ) as get_adapter:
            get_adapter.return_value.quote.return_value = response
            wizard.button_confirm()
        delivery_line = self.order.order_line.filtered("is_delivery")
        self.assertTrue(delivery_line)
        self.assertEqual(delivery_line.price_unit, 120.0)
        quote = wizard.envia_wizard_id.quote_id
        self.assertTrue(quote.selected_service_id)
        self.assertIn("FedEx", quote.selected_service_label)
        self.assertEqual(quote.sale_order_id, self.order)

    def test_rate_validate_path_does_not_call_send_to_shipper(self):
        """With integration_level=rate, Core validate path does not auto-send."""
        self.assertEqual(self.carrier.integration_level, "rate")
        product = self.env["product.product"].create(
            {
                "name": "Rate Verify Merchandise",
                "sale_ok": True,
                "type": "service",
                "list_price": 10.0,
            }
        )
        order = self.env["sale.order"].create(
            {
                "partner_id": self.partner.id,
                "partner_invoice_id": self.partner.id,
                "partner_shipping_id": self.partner.id,
                "order_line": [
                    (0, 0, {"product_id": product.id, "product_uom_qty": 1.0}),
                ],
            }
        )
        response = QuoteResponse(
            quote_id="rate-verify",
            services=[
                QuoteService(
                    service_id="fedex:1",
                    carrier="fedex",
                    carrier_name="FedEx",
                    service_name="Economy",
                    price=120.0,
                    currency=order.currency_id.name,
                ),
            ],
        )
        wizard = self.env["choose.delivery.carrier"].create(
            {
                "order_id": order.id,
                "carrier_id": self.carrier.id,
            }
        )
        with patch(
            "odoo.addons.envia.wizards.envia_quote_wizard.get_envia_adapter"
        ) as get_adapter:
            get_adapter.return_value.quote.return_value = response
            wizard.update_price()
        wizard.with_context(service_id="fedex:1").action_envia_select_service()
        with patch(
            "odoo.addons.envia.wizards.envia_quote_wizard.get_envia_adapter"
        ) as get_adapter:
            get_adapter.return_value.quote.return_value = response
            wizard.button_confirm()

        order.action_confirm()
        warehouse = order.warehouse_id
        picking_type = warehouse.out_type_id
        picking = self.env["stock.picking"].create(
            {
                "partner_id": self.partner.id,
                "picking_type_id": picking_type.id,
                "location_id": picking_type.default_location_src_id.id,
                "location_dest_id": self.partner.property_stock_customer.id,
                "carrier_id": self.carrier.id,
                "sale_id": order.id,
                "origin": order.name,
                "state": "done",
            }
        )
        picking_type.print_label = True
        self.assertEqual(picking.carrier_id.integration_level, "rate")

        with patch(
            "odoo.addons.stock_delivery.models.stock_picking.StockPicking"
            ".send_to_shipper"
        ) as send_to_shipper:
            picking._send_confirmation_email()

        send_to_shipper.assert_not_called()
        self.assertFalse(picking.envia_shipment_ids)

    def test_add_shipping_after_removing_delivery_does_not_restore_old_quote(self):
        response = QuoteResponse(
            quote_id="test",
            services=[
                QuoteService(
                    service_id="fedex:1",
                    carrier="fedex",
                    carrier_name="FedEx",
                    service_name="Economy",
                    price=120.0,
                    currency=self.order.currency_id.name,
                ),
            ],
        )
        wizard = self.env["choose.delivery.carrier"].create(
            {"order_id": self.order.id, "carrier_id": self.carrier.id}
        )
        with patch(
            "odoo.addons.envia.wizards.envia_quote_wizard.get_envia_adapter"
        ) as get_adapter:
            get_adapter.return_value.quote.return_value = response
            wizard.update_price()
        wizard.with_context(service_id="fedex:1").action_envia_select_service()
        with patch(
            "odoo.addons.envia.wizards.envia_quote_wizard.get_envia_adapter"
        ) as get_adapter:
            get_adapter.return_value.quote.return_value = response
            wizard.button_confirm()
        self.order.order_line.filtered("is_delivery").unlink()
        self.assertFalse(self.order.delivery_set)
        self.assertFalse(self.order._get_restorable_envia_quote())
        fresh = self.env["choose.delivery.carrier"].create(
            {"order_id": self.order.id, "carrier_id": self.carrier.id}
        )
        self.assertFalse(fresh.envia_wizard_id.service_line_ids)

    def test_label_generation_requires_settings_flag(self):
        quote = self.env["envia.quote"].create(
            {
                "origin_postal_code": "06600",
                "origin_country": "MX",
                "destination_postal_code": "44100",
                "destination_country": "MX",
                "weight": 1.0,
                "content": "Test",
                "state": "quoted",
            }
        )
        service = self.env["envia.quote.service"].create(
            {
                "quote_id": quote.id,
                "service_id": "fedex:1",
                "carrier": "fedex",
                "carrier_name": "FedEx",
                "service_name": "Economy",
                "price": 120.0,
                "currency_name": self.order.currency_id.name,
            }
        )
        quote.selected_service_id = service
        with self.assertRaises(UserError):
            quote._validate_label_generation()
        self.env.company.envia_enable_labels = True
        quote._validate_label_generation()

    def test_show_quote_archive_toggles_quotes_menu(self):
        menu = self.env.ref("envia.menu_envia_quotes")
        settings = self.env["res.config.settings"].create({})
        settings.envia_show_quote_archive = True
        settings.execute()
        self.assertTrue(menu.active)
        settings.envia_show_quote_archive = False
        settings.execute()
        self.assertFalse(menu.active)

    def test_default_carrier_setting_preselects_envia(self):
        self.env.company.envia_default_carrier = True
        defaults = self.env["choose.delivery.carrier"].with_context(
            default_order_id=self.order.id,
        ).default_get(["carrier_id"])
        self.assertEqual(defaults.get("carrier_id"), self.carrier.id)

    def test_branches_disabled_forces_address_only(self):
        self.env.company.envia_enable_branches = False
        wizard = self.env["envia.quote.wizard"].create(
            {
                "sale_order_id": self.order.id,
                "destination_partner_id": self.order.partner_shipping_id.id,
                "origin_location_type": "branch",
                "destination_location_type": "branch",
                "origin_postal_code": "06600",
                "destination_postal_code": "44100",
                "weight": 1.0,
                "content": "Test",
            }
        )
        self.assertEqual(wizard.origin_location_type, "address")
        self.assertEqual(wizard.destination_location_type, "address")
        self.assertFalse(wizard._uses_branch_route())

    def _make_out_picking(self, **extra):
        warehouse = self.order.warehouse_id
        picking_type = warehouse.out_type_id
        vals = {
            "partner_id": self.partner.id,
            "picking_type_id": picking_type.id,
            "location_id": picking_type.default_location_src_id.id,
            "location_dest_id": self.partner.property_stock_customer.id,
            "carrier_id": self.carrier.id,
            "sale_id": self.order.id,
            "origin": self.order.name,
        }
        vals.update(extra)
        return self.env["stock.picking"].create(vals)

    def _make_quoted_envia_rate(
        self, picking, *, service_id, envia_service_id, service_name, carrier="fedex"
    ):
        quote = self.env["envia.quote"].create(
            {
                "picking_id": picking.id,
                "sale_order_id": self.order.id,
                "company_id": self.env.company.id,
                "origin_postal_code": "06600",
                "origin_country": "MX",
                "destination_postal_code": "44100",
                "destination_country": "MX",
                "weight": 1.0,
                "content": "Test",
                "state": "quoted",
            }
        )
        service = self.env["envia.quote.service"].create(
            {
                "quote_id": quote.id,
                "service_id": service_id,
                "envia_service_id": envia_service_id,
                "carrier": carrier,
                "carrier_name": carrier.title(),
                "service_name": service_name,
                "price": float(envia_service_id),
                "currency_name": self.order.currency_id.name,
            }
        )
        service.action_select_service()
        return quote

    def test_envia_send_shipping_uses_label_create(self):
        """Send to shipper → ecommerce label/create with sale.order id and service_id."""
        self.env.company.envia_enable_labels = True
        self.env.company.envia_shop_id = "34084"
        self.env.company.envia_api_token = "shipping-token"
        picking = self._make_out_picking()
        quote = self.env["envia.quote"].create(
            {
                "picking_id": picking.id,
                "sale_order_id": self.order.id,
                "company_id": self.env.company.id,
                "origin_postal_code": "06600",
                "origin_country": "MX",
                "destination_postal_code": "44100",
                "destination_country": "MX",
                "weight": 1.0,
                "content": "Test",
                "state": "quoted",
            }
        )
        service = self.env["envia.quote.service"].create(
            {
                "quote_id": quote.id,
                "service_id": "fedex:1",
                "carrier": "fedex",
                "carrier_name": "FedEx",
                "service_name": "Economy",
                "price": 120.0,
                "currency_name": self.order.currency_id.name,
                "envia_service_id": 442,
            }
        )
        quote.selected_service_id = service
        label_body = {
            "status": True,
            "data": {
                "labels": [
                    {
                        "orderId": 110342,
                        "shipmentId": 179761,
                        "trackingNumber": "1ZSEND",
                        "label": "https://example.com/label.pdf",
                        "carrier": "dhl",
                        "service": "express_1200",
                        "serviceDescription": "DHL Express Domestic 12:00",
                        "totalPrice": 13.21,
                        "currency": "EUR",
                    }
                ]
            },
        }
        with patch(
            "odoo.addons.envia.services.envia_official_adapter.EnviaClient._post",
            return_value=label_body,
        ) as mock_post:
            result = self.carrier.with_context(
                envia_skip_dedicated_cursor=True,
            ).envia_send_shipping(picking)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["tracking_number"], "1ZSEND")
        self.assertAlmostEqual(result[0]["exact_price"], 13.21)
        shipment = picking.envia_shipment_ids[:1]
        self.assertTrue(shipment)
        self.assertEqual(shipment.external_shipment_id, "179761")
        self.assertEqual(shipment.external_order_id, "110342")
        self.assertEqual(shipment.label_url, "https://example.com/label.pdf")
        self.assertEqual(shipment.carrier, "dhl")
        self.assertEqual(shipment.service_name, "DHL Express Domestic 12:00")
        self.assertAlmostEqual(shipment.pricing_total, 13.21)
        self.assertEqual(shipment.pricing_currency_id.name, "EUR")
        self.assertEqual(self.order.envia_external_order_id, "110342")
        mock_post.assert_called_once()
        path, payload = mock_post.call_args.args[:2]
        self.assertEqual(path, "label/create/34084")
        self.assertEqual(
            payload,
            {"id": str(self.order.id), "service_id": 442},
        )
        self.assertTrue(picking._envia_has_label_url_message())
        body = picking.message_ids.filtered(
            lambda message: "example.com/label.pdf" in (message.body or "")
        )[:1].body or ""
        self.assertIn("Open shipping label (PDF)", body)
        self.assertIn("1ZSEND", body)

    def test_send_shipping_existing_shipment_posts_label_in_chatter(self):
        picking = self._make_out_picking()
        self.env["envia.shipment"].create(
            {
                "picking_id": picking.id,
                "sale_order_id": self.order.id,
                "company_id": self.env.company.id,
                "tracking_number": "1209169292",
                "label_url": "https://example.com/guia.pdf",
                "carrier": "noventa9minutos",
                "carrier_name": "noventa9Minutos",
                "state": "created",
            }
        )
        result = self.carrier.envia_send_shipping(picking)
        self.assertEqual(result[0]["tracking_number"], "1209169292")
        self.assertTrue(picking._envia_has_label_url_message())
        body = picking.message_ids.filtered(
            lambda message: "example.com/guia.pdf" in (message.body or "")
        )[:1].body or ""
        self.assertIn("Open shipping label (PDF)", body)
        self.assertIn("1209169292", body)

    def test_envia_send_shipping_unlinks_prior_then_creates_label(self):
        """Generate after Replace: DELETE prior fulfillment, then label/create."""
        self.env.company.envia_enable_labels = True
        self.env.company.envia_shop_id = "34165"
        self.env.company.envia_api_token = "shipping-token"
        picking = self._make_out_picking()
        self.env["envia.shipment"].create(
            {
                "picking_id": picking.id,
                "sale_order_id": self.order.id,
                "company_id": self.env.company.id,
                "external_shipment_id": "179909",
                "external_order_id": "110331",
                "tracking_number": "1ZOLD",
                "state": "replaced",
            }
        )
        quote = self.env["envia.quote"].create(
            {
                "picking_id": picking.id,
                "sale_order_id": self.order.id,
                "company_id": self.env.company.id,
                "origin_postal_code": "06600",
                "origin_country": "MX",
                "destination_postal_code": "44100",
                "destination_country": "MX",
                "weight": 1.0,
                "content": "Test",
                "state": "quoted",
            }
        )
        service = self.env["envia.quote.service"].create(
            {
                "quote_id": quote.id,
                "service_id": "fedex:1",
                "carrier": "fedex",
                "carrier_name": "FedEx",
                "service_name": "Economy",
                "price": 120.0,
                "currency_name": self.order.currency_id.name,
                "envia_service_id": 442,
            }
        )
        quote.selected_service_id = service
        label_body = {
            "status": True,
            "data": {
                "labels": [
                    {
                        "orderId": 110342,
                        "shipmentId": 179941,
                        "trackingNumber": "1ZNEW",
                        "label": "https://example.com/new.pdf",
                        "carrier": "dhl",
                        "serviceDescription": "DHL Express",
                        "totalPrice": 13.21,
                        "currency": "EUR",
                    }
                ]
            },
        }
        with patch(
            "odoo.addons.envia.services.envia_client.EnviaClient._delete",
            return_value={"success": True},
        ) as mock_delete, patch(
            "odoo.addons.envia.services.envia_official_adapter.EnviaClient._post",
            return_value=label_body,
        ) as mock_post:
            result = self.carrier.with_context(
                envia_skip_dedicated_cursor=True,
            ).envia_send_shipping(picking)
        mock_delete.assert_called_once()
        delete_path, delete_payload = mock_delete.call_args.args[:2]
        self.assertEqual(
            delete_path,
            "orders/34165/110331/fulfillment/order-shipments",
        )
        self.assertEqual(delete_payload, {"shipment_id": 179909})
        mock_post.assert_called_once()
        self.assertEqual(mock_post.call_args.args[0], "label/create/34165")
        self.assertEqual(
            mock_post.call_args.args[1],
            {"id": str(self.order.id), "service_id": 442},
        )
        self.assertEqual(result[0]["tracking_number"], "1ZNEW")
        new_shipment = picking.envia_shipment_ids.filtered(
            lambda item: item.state == "created"
        )[:1]
        self.assertEqual(new_shipment.external_order_id, "110342")
        self.assertEqual(new_shipment.external_shipment_id, "179941")

    def test_envia_send_shipping_unlinks_using_so_order_id_fallback(self):
        """Regenerate DELETE uses sale.order.envia_external_order_id when row lacks it."""
        self.env.company.envia_enable_labels = True
        self.env.company.envia_shop_id = "34165"
        self.env.company.envia_api_token = "shipping-token"
        picking = self._make_out_picking()
        self.order.envia_external_order_id = "110331"
        self.env["envia.shipment"].create(
            {
                "picking_id": picking.id,
                "sale_order_id": self.order.id,
                "company_id": self.env.company.id,
                "external_shipment_id": "179909",
                "tracking_number": "1ZOLD",
                "state": "replaced",
            }
        )
        quote = self.env["envia.quote"].create(
            {
                "picking_id": picking.id,
                "sale_order_id": self.order.id,
                "company_id": self.env.company.id,
                "origin_postal_code": "06600",
                "origin_country": "MX",
                "destination_postal_code": "44100",
                "destination_country": "MX",
                "weight": 1.0,
                "content": "Test",
                "state": "quoted",
            }
        )
        service = self.env["envia.quote.service"].create(
            {
                "quote_id": quote.id,
                "service_id": "fedex:1",
                "carrier": "fedex",
                "carrier_name": "FedEx",
                "service_name": "Economy",
                "price": 120.0,
                "currency_name": self.order.currency_id.name,
                "envia_service_id": 922,
            }
        )
        quote.selected_service_id = service
        label_body = {
            "status": True,
            "data": {
                "labels": [
                    {
                        "orderId": 110342,
                        "shipmentId": 179941,
                        "trackingNumber": "1ZNEW",
                        "label": "https://example.com/new.pdf",
                        "carrier": "dhl",
                        "serviceDescription": "DHL Express",
                        "totalPrice": 13.21,
                        "currency": "EUR",
                    }
                ]
            },
        }
        with patch(
            "odoo.addons.envia.services.envia_client.EnviaClient._delete",
            return_value={"success": True},
        ) as mock_delete, patch(
            "odoo.addons.envia.services.envia_official_adapter.EnviaClient._post",
            return_value=label_body,
        ):
            self.carrier.with_context(
                envia_skip_dedicated_cursor=True,
            ).envia_send_shipping(picking)
        mock_delete.assert_called_once()
        delete_path, delete_payload = mock_delete.call_args.args[:2]
        self.assertEqual(
            delete_path,
            "orders/34165/110331/fulfillment/order-shipments",
        )
        self.assertEqual(delete_payload, {"shipment_id": 179909})
        self.assertEqual(self.order.envia_external_order_id, "110342")

    def test_envia_send_shipping_already_fulfilled_without_ids_raises(self):
        self.env.company.envia_enable_labels = True
        self.env.company.envia_shop_id = "34165"
        self.env.company.envia_api_token = "shipping-token"
        picking = self._make_out_picking()
        quote = self.env["envia.quote"].create(
            {
                "picking_id": picking.id,
                "sale_order_id": self.order.id,
                "company_id": self.env.company.id,
                "origin_postal_code": "06600",
                "origin_country": "MX",
                "destination_postal_code": "44100",
                "destination_country": "MX",
                "weight": 1.0,
                "content": "Test",
                "state": "quoted",
            }
        )
        service = self.env["envia.quote.service"].create(
            {
                "quote_id": quote.id,
                "service_id": "fedex:1",
                "carrier": "fedex",
                "carrier_name": "FedEx",
                "service_name": "Economy",
                "price": 120.0,
                "currency_name": self.order.currency_id.name,
            }
        )
        quote.selected_service_id = service
        with patch(
            "odoo.addons.envia.services.envia_official_adapter.EnviaClient._post",
            side_effect=UserError(
                "Envia did not generate a label: "
                "{'status': False, 'errors': ['Order already fulfilled']}"
            ),
        ):
            with self.assertRaises(UserError) as ctx:
                self.carrier.with_context(
                    envia_skip_dedicated_cursor=True,
                ).envia_send_shipping(picking)
        self.assertIn("no Envia orderId/shipmentId", str(ctx.exception))

    def test_envia_get_tracking_link(self):
        picking = self._make_out_picking(carrier_tracking_ref="1Z999")
        url = self.carrier.envia_get_tracking_link(picking)
        self.assertEqual(url, "https://envia.com/rastreo?label=1Z999")
        picking.carrier_tracking_ref = False
        self.assertFalse(self.carrier.envia_get_tracking_link(picking))

    def test_envia_cancel_shipment_unlinks_on_envia_and_locally(self):
        self.env.company.envia_shop_id = "34165"
        self.env.company.envia_api_token = "shipping-token"
        picking = self._make_out_picking(carrier_tracking_ref="1ZCANCEL")
        shipment = self.env["envia.shipment"].create(
            {
                "picking_id": picking.id,
                "sale_order_id": self.order.id,
                "company_id": self.env.company.id,
                "external_shipment_id": "40772217",
                "external_order_id": "110342",
                "tracking_number": "1ZCANCEL",
                "state": "created",
            }
        )
        picking.envia_label_url = "https://example.com/old.pdf"
        with patch(
            "odoo.addons.envia.services.envia_client.EnviaClient._delete",
            return_value={"success": True},
        ) as mock_delete:
            self.carrier.envia_cancel_shipment(picking)
        mock_delete.assert_called_once()
        path, payload = mock_delete.call_args.args[:2]
        self.assertEqual(
            path,
            "orders/34165/110342/fulfillment/order-shipments",
        )
        self.assertEqual(payload, {"shipment_id": 40772217})
        self.assertEqual(shipment.state, "replaced")
        self.assertFalse(picking.carrier_tracking_ref)
        self.assertFalse(picking.envia_label_url)

    def test_envia_replace_label_unlinks_and_opens_quote_wizard(self):
        self.env.company.envia_shop_id = "34165"
        self.env.company.envia_api_token = "shipping-token"
        picking = self._make_out_picking(carrier_tracking_ref="1ZREPLACE")
        picking.state = "assigned"
        shipment = self.env["envia.shipment"].create(
            {
                "picking_id": picking.id,
                "sale_order_id": self.order.id,
                "company_id": self.env.company.id,
                "external_shipment_id": "179909",
                "external_order_id": "110331",
                "tracking_number": "1ZREPLACE",
                "state": "created",
            }
        )
        self.assertTrue(picking.envia_can_replace_label)
        self.assertFalse(picking.envia_can_generate_label)
        with patch(
            "odoo.addons.envia.services.envia_client.EnviaClient._delete",
            return_value={"success": True},
        ) as mock_delete:
            action = picking.action_envia_replace_label()
        mock_delete.assert_called_once()
        path, payload = mock_delete.call_args.args[:2]
        self.assertEqual(
            path,
            "orders/34165/110331/fulfillment/order-shipments",
        )
        self.assertEqual(payload, {"shipment_id": 179909})
        self.assertEqual(shipment.state, "replaced")
        self.assertFalse(picking.carrier_tracking_ref)
        self.assertEqual(action["res_model"], "envia.quote.wizard")
        self.assertTrue(picking.envia_can_generate_label)
        self.assertFalse(picking.envia_can_replace_label)

    def test_envia_generate_label_hidden_when_setting_disabled(self):
        self.env.company.envia_enable_labels = False
        picking = self._make_out_picking()
        picking.state = "assigned"
        self.assertFalse(picking.envia_can_generate_label)
        with self.assertRaises(UserError) as error:
            picking.action_envia_generate_label()
        self.assertIn("Enable label generation", str(error.exception))

    def test_envia_replace_label_retires_original_quote(self):
        self.env.company.envia_shop_id = "34165"
        self.env.company.envia_api_token = "shipping-token"
        picking = self._make_out_picking(carrier_tracking_ref="1ZREPLACE")
        picking.state = "assigned"
        quote = self._make_quoted_envia_rate(
            picking,
            service_id="fedex:1",
            envia_service_id=101,
            service_name="Economy",
        )
        self.env["envia.shipment"].create(
            {
                "picking_id": picking.id,
                "sale_order_id": self.order.id,
                "company_id": self.env.company.id,
                "external_shipment_id": "179909",
                "external_order_id": "110331",
                "tracking_number": "1ZREPLACE",
                "state": "created",
            }
        )
        with patch(
            "odoo.addons.envia.services.envia_client.EnviaClient._delete",
            return_value={"success": True},
        ):
            picking.action_envia_replace_label()
        self.assertEqual(quote.state, "used")
        self.assertFalse(picking._get_active_envia_quote())

    def test_envia_replace_label_continues_without_envia_order_id(self):
        """Old shipments without orderId: local unlink only, no blocking API error."""
        self.env.company.envia_shop_id = "34165"
        self.env.company.envia_api_token = "shipping-token"
        picking = self._make_out_picking(carrier_tracking_ref="1ZOLD")
        picking.state = "assigned"
        shipment = self.env["envia.shipment"].create(
            {
                "picking_id": picking.id,
                "sale_order_id": self.order.id,
                "company_id": self.env.company.id,
                "external_shipment_id": "179900",
                "tracking_number": "1ZOLD",
                "state": "created",
            }
        )
        with patch(
            "odoo.addons.envia.services.envia_client.EnviaClient._delete",
        ) as mock_delete:
            action = picking.action_envia_replace_label()
        mock_delete.assert_not_called()
        self.assertEqual(shipment.state, "replaced")
        self.assertFalse(picking.carrier_tracking_ref)
        self.assertEqual(action["res_model"], "envia.quote.wizard")

    def test_get_active_envia_quote_uses_latest_rate_after_requote(self):
        """Replace + re-quote must not keep the first selected service."""
        picking = self._make_out_picking()
        old = self._make_quoted_envia_rate(
            picking,
            service_id="fedex:1",
            envia_service_id=101,
            service_name="Economy",
        )
        new = self._make_quoted_envia_rate(
            picking,
            service_id="dhl:express",
            envia_service_id=202,
            service_name="Express",
            carrier="dhl",
        )
        self.assertEqual(picking._get_active_envia_quote(), new)
        self.assertNotEqual(picking._get_active_envia_quote(), old)
        self.assertEqual(self.order.envia_service_id, 202)
        self.assertEqual(self.order.envia_module["service_id"], "202")
        self.assertEqual(self.order.envia_module["service_name"], "Express")

    def test_send_shipping_syncs_latest_quote_not_first_selected(self):
        """label/create must expose the newly selected rate, not the original one."""
        self.env.company.envia_enable_labels = True
        self.env.company.envia_shop_id = "34084"
        self.env.company.envia_api_token = "shipping-token"
        picking = self._make_out_picking()
        self._make_quoted_envia_rate(
            picking,
            service_id="fedex:1",
            envia_service_id=101,
            service_name="Economy",
        )
        self._make_quoted_envia_rate(
            picking,
            service_id="dhl:express",
            envia_service_id=202,
            service_name="Express",
            carrier="dhl",
        )
        captured = {}

        def _create_label(order_id):
            captured["envia_service_id"] = self.order.envia_service_id
            captured["service_id"] = self.order.envia_module["service_id"]
            return CreateShipmentResponse(
                shipment_id=179941,
                tracking_number="1ZNEW",
                carrier="dhl",
                carrier_name="DHL",
                service="Express",
                status="created",
                status_description="ok",
                label_url="https://example.com/new.pdf",
            )

        with patch.object(
            EnviaOfficialAdapter, "create_label_for_odoo_order", side_effect=_create_label
        ):
            self.carrier.with_context(
                envia_skip_dedicated_cursor=True,
            ).envia_send_shipping(picking)
        self.assertEqual(captured["envia_service_id"], 202)
        self.assertEqual(captured["service_id"], "202")

    def test_quote_wizard_select_rate_persists_over_previous_quote(self):
        picking = self._make_out_picking()
        old = self._make_quoted_envia_rate(
            picking,
            service_id="fedex:1",
            envia_service_id=101,
            service_name="Economy",
        )
        new = self._make_quoted_envia_rate(
            picking,
            service_id="dhl:express",
            envia_service_id=202,
            service_name="Express",
            carrier="dhl",
        )
        wizard = self.env["envia.quote.wizard"].with_context(
            envia_skip_auto_quote=True,
            envia_skip_branch_autoload=True,
        ).create(
            {
                "picking_id": picking.id,
                "sale_order_id": self.order.id,
                "quote_id": new.id,
                "weight": 1.0,
                "content": "Test",
            }
        )
        cheaper = self.env["envia.quote.wizard.service"].create(
            {
                "wizard_id": wizard.id,
                "service_id": "fedex:1",
                "envia_service_id": 101,
                "carrier": "fedex",
                "carrier_name": "Fedex",
                "service_name": "Economy",
                "price": 101.0,
                "is_selected": True,
            }
        )
        selected = self.env["envia.quote.wizard.service"].create(
            {
                "wizard_id": wizard.id,
                "service_id": "dhl:express",
                "envia_service_id": 202,
                "carrier": "dhl",
                "carrier_name": "Dhl",
                "service_name": "Express",
                "price": 202.0,
            }
        )
        wizard.action_select_service_rate(service_id=selected.service_id)
        self.assertFalse(cheaper.is_selected)
        self.assertTrue(selected.is_selected)
        self.assertEqual(new.selected_service_id.envia_service_id, 202)
        self.assertEqual(self.order.envia_service_id, 202)
        self.assertEqual(old.state, "used")
        self.assertEqual(picking._get_active_envia_quote(), new)

    def test_envia_generate_label_calls_send_to_shipper(self):
        picking = self._make_out_picking()
        picking.state = "assigned"
        quote = self.env["envia.quote"].create(
            {
                "picking_id": picking.id,
                "sale_order_id": self.order.id,
                "company_id": self.env.company.id,
                "origin_postal_code": "06600",
                "origin_country": "MX",
                "destination_postal_code": "44100",
                "destination_country": "MX",
                "weight": 1.0,
                "content": "Test",
                "state": "quoted",
            }
        )
        service = self.env["envia.quote.service"].create(
            {
                "quote_id": quote.id,
                "service_id": "fedex:1",
                "carrier": "fedex",
                "carrier_name": "FedEx",
                "service_name": "Economy",
                "price": 120.0,
                "currency_name": self.order.currency_id.name,
            }
        )
        quote.selected_service_id = service
        self.assertTrue(picking.envia_can_generate_label)
        with patch.object(type(picking), "send_to_shipper", return_value=True) as send:
            picking.action_envia_generate_label()
        send.assert_called_once()

    def test_envia_generate_label_is_idempotent_when_label_exists(self):
        picking = self._make_out_picking(carrier_tracking_ref="1ZEXISTING")
        picking.state = "assigned"
        self.env["envia.shipment"].create(
            {
                "picking_id": picking.id,
                "company_id": self.env.company.id,
                "tracking_number": "1ZEXISTING",
                "state": "created",
            }
        )
        with patch.object(type(picking), "send_to_shipper", return_value=True) as send:
            self.assertTrue(picking.action_envia_generate_label())
        send.assert_not_called()

    def test_create_shipment_posts_label_url_on_picking_chatter(self):
        picking = self._make_out_picking()
        quote = self.env["envia.quote"].create(
            {
                "picking_id": picking.id,
                "sale_order_id": self.order.id,
                "company_id": self.env.company.id,
                "origin_postal_code": "06600",
                "origin_country": "MX",
                "destination_postal_code": "44100",
                "destination_country": "MX",
                "weight": 1.0,
                "content": "Test",
                "state": "quoted",
            }
        )
        service = self.env["envia.quote.service"].create(
            {
                "quote_id": quote.id,
                "service_id": "fedex:1",
                "carrier": "fedex",
                "carrier_name": "FedEx",
                "service_name": "Economy",
                "price": 120.0,
                "currency_name": self.order.currency_id.name,
            }
        )
        quote.selected_service_id = service
        response = CreateShipmentResponse(
            shipment_id=40772217,
            tracking_number="1ZLABEL",
            carrier="fedex",
            carrier_name="FedEx",
            service="Economy",
            status="created",
            status_description="ok",
            label_url="https://example.com/label.pdf",
        )
        shipment = self.env["envia.shipment"].create_from_api_response(
            response, quote, picking=picking
        )
        self.assertFalse(shipment.label_attachment_id)
        self.assertEqual(shipment.label_url, "https://example.com/label.pdf")
        self.assertEqual(picking.carrier_tracking_ref, "1ZLABEL")
        label_messages = picking.message_ids.filtered(
            lambda message: "example.com/label.pdf" in (message.body or "")
        )
        self.assertTrue(label_messages)
        self.assertTrue(picking._envia_has_label_url_message())

    def test_create_from_api_keeps_shipment_without_local_pdf(self):
        picking = self._make_out_picking()
        quote = self.env["envia.quote"].create(
            {
                "picking_id": picking.id,
                "sale_order_id": self.order.id,
                "company_id": self.env.company.id,
                "origin_postal_code": "06600",
                "origin_country": "MX",
                "destination_postal_code": "44100",
                "destination_country": "MX",
                "weight": 1.0,
                "content": "Test",
                "state": "quoted",
            }
        )
        service = self.env["envia.quote.service"].create(
            {
                "quote_id": quote.id,
                "service_id": "fedex:1",
                "carrier": "fedex",
                "carrier_name": "FedEx",
                "service_name": "Economy",
                "price": 120.0,
                "currency_name": self.order.currency_id.name,
            }
        )
        quote.selected_service_id = service
        response = CreateShipmentResponse(
            shipment_id="",
            tracking_number="1ZNOROLLBACK",
            carrier="fedex",
            carrier_name="FedEx",
            service="Economy",
            status="created",
            status_description="ok",
            label_url="https://example.com/label.pdf",
        )
        shipment = self.env["envia.shipment"].create_from_api_response(
            response, quote, picking=picking
        )
        self.assertTrue(shipment.exists())
        self.assertFalse(shipment.label_attachment_id)
        self.assertEqual(shipment.tracking_number, "1ZNOROLLBACK")
        self.assertEqual(picking.carrier_tracking_ref, "1ZNOROLLBACK")
        self.assertEqual(quote.state, "used")
        self.assertTrue(picking._envia_has_label_url_message())

    def test_sale_order_envia_status_shipped_from_picking_tracking(self):
        """Portal label writes tracking on the picking → SO Status = Label created."""
        quote = self.env["envia.quote"].create(
            {
                "sale_order_id": self.order.id,
                "company_id": self.env.company.id,
                "origin_postal_code": "06600",
                "origin_country": "MX",
                "destination_postal_code": "44100",
                "destination_country": "MX",
                "weight": 1.0,
                "content": "Test",
                "state": "quoted",
            }
        )
        service = self.env["envia.quote.service"].create(
            {
                "quote_id": quote.id,
                "service_id": "dhl:1",
                "carrier": "dhl",
                "carrier_name": "DHL",
                "service_name": "express",
                "price": 50.0,
                "currency_name": self.order.currency_id.name,
                "is_selected": True,
            }
        )
        quote.selected_service_id = service
        self.assertEqual(self.order.envia_status, "quoted")
        picking = self._make_out_picking(carrier_tracking_ref="1ZWEBHOOK")
        self.assertFalse(picking.envia_shipment_ids)
        self.assertEqual(picking.envia_status, "shipped")
        self.assertEqual(self.order.envia_status, "shipped")
        self.assertIn("1ZWEBHOOK", self.order.envia_summary)

    def test_send_shipping_recovers_bookkeeping_when_tracking_exists(self):
        picking = self._make_out_picking(carrier_tracking_ref="2117041242")
        quote = self.env["envia.quote"].create(
            {
                "sale_order_id": self.order.id,
                "company_id": self.env.company.id,
                "origin_postal_code": "06600",
                "origin_country": "MX",
                "destination_postal_code": "44100",
                "destination_country": "MX",
                "weight": 1.0,
                "content": "Test",
                "state": "quoted",
            }
        )
        service = self.env["envia.quote.service"].create(
            {
                "quote_id": quote.id,
                "service_id": "dhl:1",
                "carrier": "dhl",
                "carrier_name": "DHL",
                "service_name": "express_1200",
                "price": 27.32,
                "currency_name": self.order.currency_id.name,
            }
        )
        quote.selected_service_id = service
        with patch.object(
            type(picking), "_get_active_envia_quote", return_value=quote
        ):
            shipment = self.env["envia.shipment"].create_bookkeeping_from_picking(
                picking, quote=quote
            )
        shipment.label_url = "https://example.com/already.pdf"
        picking.envia_label_url = shipment.label_url
        picking._envia_post_label_url(
            label_url=shipment.label_url,
            tracking_number=picking.carrier_tracking_ref,
        )
        with patch.object(
            type(picking), "_get_active_envia_quote", return_value=quote
        ), patch(
            "odoo.addons.envia.models.delivery_carrier.get_envia_adapter"
        ) as mock_adapter:
            result = self.carrier.envia_send_shipping(picking)
        mock_adapter.assert_not_called()
        self.assertEqual(result[0]["tracking_number"], "2117041242")
        self.assertEqual(picking.envia_shipment_ids, shipment)
        self.assertEqual(quote.state, "used")

    def test_envia_post_label_url_in_chatter(self):
        picking = self._make_out_picking(carrier_tracking_ref="1ZCORE")
        picking._envia_post_label_url(
            label_url="https://s3.example.com/guia.pdf",
            tracking_number="1ZCORE",
        )
        self.assertEqual(picking.envia_label_url, "https://s3.example.com/guia.pdf")
        self.assertTrue(picking._envia_has_label_url_message())
        body = picking.message_ids[:1].body or ""
        self.assertIn("<br/>", body)
        self.assertIn('<a href="https://s3.example.com/guia.pdf"', body)
        self.assertIn("Open shipping label (PDF)", body)
        self.assertIn("https://envia.com/rastreo?label=1ZCORE", body)
        self.assertNotIn("&lt;br", body)
        # UX: do not dump the raw S3 URL as visible link text.
        self.assertNotIn(">https://s3.example.com/guia.pdf<", body)
        self.assertFalse(
            self.env["ir.attachment"].search_count(
                [
                    ("res_model", "=", "stock.picking"),
                    ("res_id", "=", picking.id),
                    ("name", "=like", "LabelShipping-%"),
                ]
            ),
            "Odoo must not download/store Envia label PDFs",
        )
