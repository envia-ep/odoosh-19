from unittest.mock import patch

from odoo.addons.envia.services.dto import Contact
from odoo.addons.envia.services.envia_official_adapter import EnviaOfficialAdapter
from odoo.addons.envia.services.payload_mapper import PayloadMapper
from odoo.addons.envia.wizards.envia_quote_wizard import EnviaQuoteWizard
from odoo.exceptions import UserError
from odoo.tests import Form, tagged
from odoo.tests.common import TransactionCase


@tagged("post_install", "-at_install")
class TestSaleOrderEnvia(TransactionCase):
    def setUp(self):
        super().setUp()
        self.env.company.envia_enable_branches = True

    def test_action_envia_reship_creates_linked_outgoing_after_return(self):
        """After OUT done + return with to_refund, Reship creates a new linked OUT."""
        warehouse = self.env["stock.warehouse"].search(
            [("company_id", "=", self.env.company.id)], limit=1
        )
        product = self.env["product.product"].create(
            {
                "name": "Reship QA Product",
                "is_storable": True,
                "sale_ok": True,
                "list_price": 10.0,
            }
        )
        self.env["stock.quant"]._update_available_quantity(
            product, warehouse.lot_stock_id, 5
        )
        partner = self.env["res.partner"].create(
            {
                "name": "Reship Customer",
                "street": "Calle 1",
                "city": "CDMX",
                "zip": "06600",
                "country_id": self.env.ref("base.mx").id,
            }
        )
        carrier = self.env.ref("envia.delivery_carrier_envia")
        order = self.env["sale.order"].create(
            {
                "partner_id": partner.id,
                "partner_invoice_id": partner.id,
                "partner_shipping_id": partner.id,
                "warehouse_id": warehouse.id,
                "order_line": [
                    (0, 0, {"product_id": product.id, "product_uom_qty": 1.0})
                ],
            }
        )
        order.action_confirm()
        out_picking = order.picking_ids.filtered(
            lambda picking: picking.picking_type_code == "outgoing"
        )
        self.assertEqual(len(out_picking), 1)
        out_picking.move_ids.write({"quantity": 1, "picked": True})
        out_picking.button_validate()
        self.assertEqual(order.order_line.filtered("product_id").qty_delivered, 1.0)
        self.assertFalse(order.envia_can_reship)

        return_form = Form(
            self.env["stock.return.picking"].with_context(
                active_ids=out_picking.ids,
                active_id=out_picking.id,
                active_model="stock.picking",
            )
        )
        return_wiz = return_form.save()
        return_wiz.product_return_moves.quantity = 1.0
        return_wiz.product_return_moves.to_refund = True
        return_action = return_wiz.action_create_returns()
        return_picking = self.env["stock.picking"].browse(return_action["res_id"])
        return_picking.move_ids.write({"quantity": 1, "picked": True})
        return_picking.button_validate()
        self.assertEqual(order.order_line.filtered("product_id").qty_delivered, 0.0)
        self.assertTrue(order.envia_can_reship)

        order.write({"carrier_id": carrier.id, "envia_service_id": 442})
        action = order.action_envia_reship()
        new_outs = order.picking_ids.filtered(
            lambda picking: picking.picking_type_code == "outgoing"
            and picking.state not in ("done", "cancel")
        )
        self.assertEqual(len(new_outs), 1)
        self.assertEqual(new_outs.sale_id, order)
        self.assertEqual(new_outs.carrier_id, carrier)
        self.assertEqual(new_outs.envia_service_id, 442)
        self.assertTrue(
            new_outs.move_ids.filtered(lambda move: move.sale_line_id in order.order_line)
        )
        self.assertEqual(action["res_id"], new_outs.id)
        self.assertFalse(order.envia_can_reship)

    def test_action_envia_reship_without_return_raises(self):
        warehouse = self.env["stock.warehouse"].search(
            [("company_id", "=", self.env.company.id)], limit=1
        )
        product = self.env["product.product"].create(
            {
                "name": "No Return Reship",
                "is_storable": True,
                "sale_ok": True,
                "list_price": 10.0,
            }
        )
        self.env["stock.quant"]._update_available_quantity(
            product, warehouse.lot_stock_id, 2
        )
        partner = self.env["res.partner"].create({"name": "No Return Customer"})
        order = self.env["sale.order"].create(
            {
                "partner_id": partner.id,
                "partner_shipping_id": partner.id,
                "warehouse_id": warehouse.id,
                "order_line": [
                    (0, 0, {"product_id": product.id, "product_uom_qty": 1.0})
                ],
            }
        )
        order.action_confirm()
        picking = order.picking_ids
        picking.move_ids.write({"quantity": 1, "picked": True})
        picking.button_validate()
        with self.assertRaises(UserError):
            order.action_envia_reship()

    def test_envia_state_code_maps_mexico_city(self):
        self.assertEqual(EnviaOfficialAdapter.envia_state_code("MX", "CMX"), "CX")
        self.assertEqual(EnviaOfficialAdapter.envia_state_code("MX", "CX"), "CX")
        self.assertEqual(EnviaOfficialAdapter.envia_state_code("MX", "NLE"), "NL")

    def test_contact_to_official_address_includes_branch_code(self):
        address = EnviaOfficialAdapter._contact_to_official_address(
            Contact(
                name="Branch",
                street="Av. Test",
                city="CDMX",
                state="CMX",
                postal_code="03100",
                country="MX",
                phone="5555555555",
                email="test@example.com",
                branch_code="RMX",
            )
        )
        self.assertEqual(address["state"], "CX")
        self.assertEqual(address["branchCode"], "RMX")
        self.assertEqual(address["number"], "S/N")

    def test_expected_drop_off_for_ship_to_pickup(self):
        origin = Contact(
            name="Origin",
            street="Street",
            city="Guadalupe",
            state="NL",
            postal_code="67192",
            country="MX",
            phone="5555555555",
            email="origin@example.com",
        )
        destination = Contact(
            name="Branch",
            street="Branch",
            city="Ciudad de Mexico",
            state="CX",
            postal_code="03100",
            country="MX",
            phone="5555555555",
            email="dest@example.com",
            branch_code="RMX",
        )
        self.assertEqual(
            EnviaOfficialAdapter._expected_drop_off(origin, destination),
            2,
        )

    def test_pickup_point_additional_services_by_route(self):
        origin_address = Contact(
            name="Origin",
            street="Street",
            city="Guadalupe",
            state="NL",
            postal_code="67192",
            country="MX",
            phone="5555555555",
            email="origin@example.com",
        )
        origin_branch = Contact(
            name="Origin Branch",
            street="Branch",
            city="Guadalupe",
            state="NL",
            postal_code="67192",
            country="MX",
            phone="5555555555",
            email="origin@example.com",
            branch_code="GDL",
        )
        destination_branch = Contact(
            name="Branch",
            street="Branch",
            city="Ciudad de Mexico",
            state="CX",
            postal_code="03100",
            country="MX",
            phone="5555555555",
            email="dest@example.com",
            branch_code="RMX",
        )
        self.assertEqual(
            EnviaOfficialAdapter._pickup_point_additional_services(
                origin_address,
                destination_branch,
            ),
            [{"service": "pickup_point_delivery"}],
        )
        self.assertEqual(
            EnviaOfficialAdapter._pickup_point_additional_services(
                origin_branch,
                origin_address,
            ),
            [{"service": "pickup_point_pickup"}],
        )
        self.assertEqual(
            EnviaOfficialAdapter._pickup_point_additional_services(
                origin_branch,
                destination_branch,
            ),
            [
                {"service": "pickup_point_pickup"},
                {"service": "pickup_point_delivery"},
            ],
        )

    def test_build_checkout_payload_includes_branch_codes(self):
        from odoo.addons.envia.services.dto import QuoteRequest

        request = QuoteRequest(
            origin_postal_code="67192",
            origin_country="MX",
            origin_state="NL",
            destination_postal_code="03100",
            destination_country="MX",
            destination_state="CX",
            weight=1.0,
            content="Package",
            origin_contact=Contact(
                name="Origin Branch",
                street="Branch",
                city="Guadalupe",
                state="NL",
                postal_code="67192",
                country="MX",
                phone="5555555555",
                email="origin@example.com",
                branch_code="GDL",
            ),
            destination_contact=Contact(
                name="Destination Branch",
                street="Branch",
                city="Ciudad de Mexico",
                state="CX",
                postal_code="03100",
                country="MX",
                phone="5555555555",
                email="dest@example.com",
                branch_code="RMX",
            ),
            carriers="estafeta",
        )
        payload = EnviaOfficialAdapter._build_checkout_payload(request)
        self.assertEqual(payload["origin"]["branchCode"], "GDL")
        self.assertEqual(payload["destination"]["branchCode"], "RMX")
        self.assertEqual(payload["package"]["type"], "box")
        self.assertEqual(payload["locale"], "es_MX")

    def test_build_checkout_payload_uses_product_product_id(self):
        partner = self.env.company.partner_id
        product = self.env["product.product"].search([("sale_ok", "=", True)], limit=1)
        order = self.env["sale.order"].create(
            {
                "partner_id": partner.id,
                "partner_invoice_id": partner.id,
                "partner_shipping_id": partner.id,
                "order_line": [(0, 0, {"product_id": product.id, "product_uom_qty": 1.0})],
            }
        )
        request = PayloadMapper.build_quote_request_from_sale_order(order)
        payload = EnviaOfficialAdapter._build_checkout_payload(request)
        self.assertEqual(payload["items"][0]["productId"], str(product.id))

    def test_parse_checkout_rates_accepts_top_level_list(self):
        from odoo.addons.envia.services.dto import QuoteRequest

        request = QuoteRequest(
            origin_postal_code="67192",
            origin_country="MX",
            destination_postal_code="03100",
            destination_country="MX",
            weight=1.0,
            content="Package",
        )
        services = EnviaOfficialAdapter._parse_checkout_rates(
            [
                {
                    "carrier": "fedex",
                    "service": "economy",
                    "serviceDescription": "FedEx Economy",
                    "totalPrice": "182.00",
                    "currency": "MXN",
                }
            ],
            request,
        )
        self.assertEqual(len(services), 1)
        self.assertEqual(services[0].service_id, "fedex:economy")
        self.assertEqual(services[0].price, 182.0)

    def test_quote_raises_on_envia_checkout_meta_error(self):
        from unittest.mock import MagicMock

        from odoo.addons.envia.services.dto import QuoteRequest
        from odoo.addons.envia.services.envia_official_adapter import EnviaOfficialAdapter

        request = QuoteRequest(
            origin_postal_code="67192",
            origin_country="MX",
            destination_postal_code="03100",
            destination_country="MX",
            weight=1.0,
            content="Package",
        )
        client = MagicMock()
        client._post.return_value = {
            "meta": "error",
            "error": {
                "code": 1365,
                "description": "Invalid Option",
                "message": "Error processing request no carriers enabled",
            },
        }
        adapter = EnviaOfficialAdapter(client, shop_id="34174")
        with self.assertRaises(UserError) as error:
            adapter.quote(request)
        self.assertIn("enable Checkout", str(error.exception))
        self.assertIn("carriers you want to quote", str(error.exception))

    def test_quote_filters_services_by_drop_off(self):
        from unittest.mock import MagicMock

        from odoo.addons.envia.services.dto import QuoteRequest

        request = QuoteRequest(
            origin_postal_code="67192",
            origin_country="MX",
            origin_state="NL",
            destination_postal_code="03100",
            destination_country="MX",
            destination_state="CX",
            weight=1.0,
            content="Package",
            origin_contact=Contact(
                name="Origin",
                street="Street",
                city="Guadalupe",
                state="NL",
                postal_code="67192",
                country="MX",
                phone="5555555555",
                email="origin@example.com",
            ),
            destination_contact=Contact(
                name="Branch",
                street="Branch",
                city="Ciudad de Mexico",
                state="CX",
                postal_code="03100",
                country="MX",
                phone="5555555555",
                email="dest@example.com",
                branch_code="RMX",
            ),
            carriers="estafeta",
        )
        client = MagicMock()
        client._post.return_value = {
            "data": [
                {
                    "carrier": "estafeta",
                    "service": "ground",
                    "serviceDescription": "Terrestre",
                    "totalPrice": 185.72,
                    "currency": "MXN",
                    "dropOff": 0,
                },
                {
                    "carrier": "estafeta",
                    "service": "ground_ocurre",
                    "serviceId": 23,
                    "serviceDescription": "Terrestre Ocurre",
                    "totalPrice": 190.0,
                    "currency": "MXN",
                    "dropOff": 2,
                },
            ]
        }
        adapter = EnviaOfficialAdapter(client, shop_id="34084", default_carriers="estafeta")
        response = adapter.quote(request)
        self.assertEqual(len(response.services), 1)
        self.assertEqual(response.services[0].service_id, "estafeta:ground_ocurre")
        self.assertEqual(response.services[0].envia_service_id, 23)
        self.assertEqual(response.services[0].drop_off, 2)

    def test_quote_excludes_ship_ship_rates_on_pickup_route(self):
        from unittest.mock import MagicMock

        from odoo.addons.envia.services.dto import QuoteRequest

        request = QuoteRequest(
            origin_postal_code="67192",
            origin_country="MX",
            origin_state="NL",
            destination_postal_code="03100",
            destination_country="MX",
            destination_state="CX",
            weight=1.0,
            content="Package",
            origin_contact=Contact(
                name="Origin Branch",
                street="Street",
                city="Guadalupe",
                state="NL",
                postal_code="67192",
                country="MX",
                phone="5555555555",
                email="origin@example.com",
                branch_code="MTY01",
            ),
            destination_contact=Contact(
                name="Branch",
                street="Branch",
                city="Ciudad de Mexico",
                state="CX",
                postal_code="03100",
                country="MX",
                phone="5555555555",
                email="dest@example.com",
                branch_code="RMX",
            ),
            carriers="ups",
        )
        client = MagicMock()
        client._post.return_value = {
            "data": [
                {
                    "carrier": "ups",
                    "service": "saver",
                    "serviceDescription": "Ups Saver",
                    "totalPrice": 11.60,
                    "currency": "MXN",
                    "dropOff": 0,
                },
                {
                    "carrier": "ups",
                    "service": "branch",
                    "serviceDescription": "Branch to Branch",
                    "totalPrice": 20.0,
                    "currency": "MXN",
                    "dropOff": 3,
                },
            ]
        }
        adapter = EnviaOfficialAdapter(client, shop_id="34084", default_carriers="ups")
        response = adapter.quote(request)
        self.assertEqual(len(response.services), 1)
        self.assertEqual(response.services[0].drop_off, 3)
        self.assertEqual(response.services[0].service_id, "ups:branch")

    def test_quote_raises_when_only_ship_ship_rates_on_pickup_route(self):
        from unittest.mock import MagicMock

        from odoo.addons.envia.services.dto import QuoteRequest

        request = QuoteRequest(
            origin_postal_code="67192",
            origin_country="MX",
            origin_state="NL",
            destination_postal_code="03100",
            destination_country="MX",
            destination_state="CX",
            weight=1.0,
            content="Package",
            origin_contact=Contact(
                name="Origin Branch",
                street="Street",
                city="Guadalupe",
                state="NL",
                postal_code="67192",
                country="MX",
                phone="5555555555",
                email="origin@example.com",
                branch_code="MTY01",
            ),
            destination_contact=Contact(
                name="Branch",
                street="Branch",
                city="Ciudad de Mexico",
                state="CX",
                postal_code="03100",
                country="MX",
                phone="5555555555",
                email="dest@example.com",
                branch_code="RMX",
            ),
            carriers="ups",
        )
        client = MagicMock()
        client._post.return_value = {
            "data": [
                {
                    "carrier": "ups",
                    "service": "saver",
                    "serviceDescription": "Ups Saver",
                    "totalPrice": 11.60,
                    "currency": "MXN",
                    "dropOff": 0,
                }
            ]
        }
        adapter = EnviaOfficialAdapter(client, shop_id="34084", default_carriers="ups")
        with self.assertRaises(UserError):
            adapter.quote(request)

    def test_contact_to_official_address_splits_street_number(self):
        address = EnviaOfficialAdapter._contact_to_official_address(
            Contact(
                name="My Company",
                street="Aurora Boreal 301",
                city="Guadalupe",
                state="NL",
                postal_code="67192",
                country="MX",
                phone="8121211454",
                email="test@example.com",
            )
        )
        self.assertEqual(address["street"], "Aurora Boreal")
        self.assertEqual(address["number"], "301")

    def test_contact_to_official_address_uses_street2_number(self):
        address = EnviaOfficialAdapter._contact_to_official_address(
            Contact(
                name="Customer",
                street="Av Reforma",
                number="123",
                city="Ciudad de Mexico",
                state="CX",
                postal_code="06600",
                country="MX",
                phone="5555555555",
                email="test@example.com",
            )
        )
        self.assertEqual(address["street"], "Av Reforma")
        self.assertEqual(address["number"], "123")

    def test_quote_wizard_opens_in_modal_from_sale_order(self):
        partner = self.env.company.partner_id
        product = self.env["product.product"].search([("sale_ok", "=", True)], limit=1)
        order = self.env["sale.order"].create(
            {
                "partner_id": partner.id,
                "partner_invoice_id": partner.id,
                "partner_shipping_id": partner.id,
                "order_line": [(0, 0, {"product_id": product.id, "product_uom_qty": 1.0})],
            }
        )
        order.action_confirm()

        action = order.action_open_envia_quote_wizard()
        self.assertEqual(action["target"], "new")
        self.assertEqual(action["context"]["default_sale_order_id"], order.id)

        wizard = self.env["envia.quote.wizard"].create({"sale_order_id": order.id})
        self.assertEqual(wizard._reopen_wizard()["target"], "new")
        stay_open = wizard._wizard_action()
        self.assertEqual(stay_open["type"], "ir.actions.client")
        self.assertEqual(stay_open["tag"], "envia_wizard_noop")
        self.assertEqual(wizard.action_discard()["type"], "ir.actions.act_window_close")

    def test_service_option_labels_match_checkout_format(self):
        wizard = self.env["envia.quote.wizard"].create({})
        service = self.env["envia.quote.wizard.service"].create(
            {
                "wizard_id": wizard.id,
                "service_id": "fedex-economico",
                "carrier_name": "FedEx",
                "service_name": "Nacional Económico",
                "estimated_delivery_days": 2,
                "price": 182.0,
                "currency_name": "MXN",
            }
        )
        self.assertIn("FedEx Nacional Económico", service.option_label)
        self.assertIn("1-2 days", service.option_label)
        self.assertTrue(service.price_label)

    def test_mx_postal_code_pads_leading_zeros(self):
        wizard = self.env["envia.quote.wizard"]
        self.assertEqual(wizard._normalize_postal_code("MX", "3100"), "03100")
        self.assertEqual(wizard._normalize_postal_code("MX", "03100"), "03100")
        self.assertEqual(wizard._normalize_postal_code("MX", "67192"), "67192")

    def test_pickup_point_services_for_drop_off(self):
        from odoo.addons.envia.services.envia_official_adapter import EnviaOfficialAdapter

        self.assertEqual(
            EnviaOfficialAdapter._pickup_point_services_for_drop_off(2),
            [{"service": "pickup_point_delivery"}],
        )
        self.assertEqual(
            EnviaOfficialAdapter._pickup_point_services_for_drop_off(1),
            [{"service": "pickup_point_pickup"}],
        )
        self.assertEqual(
            EnviaOfficialAdapter._pickup_point_services_for_drop_off(3),
            [
                {"service": "pickup_point_pickup"},
                {"service": "pickup_point_delivery"},
            ],
        )

    def test_branch_selection_normalizes_mx_postal_code(self):
        mexico = self.env.ref("base.mx")
        self.assertEqual(
            self.env["envia.quote.wizard"]._normalize_postal_code("MX", "3100"),
            "03100",
        )

    def test_address_selection_applies_warehouse_defaults(self):
        mexico = self.env.ref("base.mx")
        warehouses = self.env["stock.warehouse"].search(
            [("company_id", "=", self.env.company.id)]
        )
        self.assertGreaterEqual(len(warehouses), 1)
        alt_warehouse = warehouses[0]
        if len(warehouses) < 2:
            alt_partner = self.env["res.partner"].create(
                {
                    "name": "Warehouse B Address",
                    "street": "Calle Alt 123",
                    "city": "Monterrey",
                    "zip": "64000",
                    "country_id": mexico.id,
                }
            )
            alt_warehouse = self.env["stock.warehouse"].create(
                {
                    "name": "Warehouse B",
                    "code": "WHB",
                    "company_id": self.env.company.id,
                    "partner_id": alt_partner.id,
                }
            )
        wizard = self.env["envia.quote.wizard"].with_context(
            envia_skip_branch_autoload=True
        ).create({})
        lines = wizard.origin_address_line_ids
        self.assertGreaterEqual(len(lines), 2)
        target = lines.filtered(lambda line: line.warehouse_id == alt_warehouse)[:1]
        with patch.object(type(wizard), "action_get_quote", return_value=wizard._reopen_wizard()):
            target.action_select_address()
        self.assertEqual(wizard.origin_warehouse_id, alt_warehouse)
        self.assertEqual(wizard.origin_partner_id, alt_warehouse.partner_id)
        self.assertEqual(wizard.origin_postal_code, alt_warehouse.partner_id.zip)
        self.assertEqual(wizard.origin_city, alt_warehouse.partner_id.city)

    def test_sale_order_origin_uses_delivery_warehouse(self):
        mexico = self.env.ref("base.mx")
        partner = self.env.company.partner_id
        product = self.env["product.product"].create(
            {"name": "Origin WH Merchandise", "sale_ok": True, "list_price": 10.0}
        )
        order = self.env["sale.order"].create(
            {
                "partner_id": partner.id,
                "partner_invoice_id": partner.id,
                "partner_shipping_id": partner.id,
                "order_line": [(0, 0, {"product_id": product.id, "product_uom_qty": 1.0})],
            }
        )
        delivery_warehouse = order.warehouse_id
        self.assertTrue(delivery_warehouse)
        other_partner = self.env["res.partner"].create(
            {
                "name": "Company Default WH Address",
                "street": "Calle Default 1",
                "city": "Monterrey",
                "zip": "64000",
                "country_id": mexico.id,
                "state_id": self.env.ref("base.state_mx_nl").id,
            }
        )
        other_warehouse = self.env["stock.warehouse"].create(
            {
                "name": "Company Default WH",
                "code": "CDWH",
                "company_id": self.env.company.id,
                "partner_id": other_partner.id,
            }
        )
        self.env.company.envia_default_origin_warehouse_id = other_warehouse
        defaults = self.env["envia.quote.wizard"].with_context(
            default_sale_order_id=order.id,
        ).default_get(["origin_warehouse_id", "sale_order_id"])
        self.assertEqual(defaults["origin_warehouse_id"], delivery_warehouse.id)
        with patch(
            "odoo.addons.envia.wizards.envia_quote_wizard.EnviaGeocodesClient"
        ) as geocodes:
            geocodes.return_value.lookup_zipcode.return_value = []
            wizard = self.env["envia.quote.wizard"].with_context(
                envia_skip_branch_autoload=True,
            ).create({"sale_order_id": order.id})
        self.assertEqual(wizard.origin_warehouse_id, delivery_warehouse)
        self.assertFalse(wizard.origin_readonly)
        self.assertTrue(wizard.destination_partner_readonly)
        self.assertEqual(wizard.destination_partner_id, order.partner_shipping_id)

    def test_sale_order_destination_uses_delivery_address(self):
        mexico = self.env.ref("base.mx")
        customer = self.env["res.partner"].create(
            {
                "name": "SO Customer",
                "street": "Av Cliente 1",
                "city": "CDMX",
                "zip": "06600",
                "country_id": mexico.id,
                "state_id": self.env.ref("base.state_mx_df").id,
            }
        )
        delivery = self.env["res.partner"].create(
            {
                "name": "Delivery Address Partner",
                "type": "delivery",
                "parent_id": customer.id,
                "street": "Calle Entrega 9",
                "city": "Guadalajara",
                "zip": "44100",
                "country_id": mexico.id,
                "state_id": self.env.ref("base.state_mx_jal").id,
            }
        )
        product = self.env["product.product"].create(
            {"name": "Dest Addr Merchandise", "sale_ok": True, "list_price": 10.0}
        )
        order = self.env["sale.order"].create(
            {
                "partner_id": customer.id,
                "partner_invoice_id": customer.id,
                "partner_shipping_id": delivery.id,
                "order_line": [(0, 0, {"product_id": product.id, "product_uom_qty": 1.0})],
            }
        )
        defaults = self.env["envia.quote.wizard"].with_context(
            default_sale_order_id=order.id,
            default_destination_partner_id=customer.id,
        ).default_get(["destination_partner_id", "sale_order_id"])
        self.assertEqual(defaults["destination_partner_id"], delivery.id)
        with patch(
            "odoo.addons.envia.wizards.envia_quote_wizard.EnviaGeocodesClient"
        ) as geocodes:
            geocodes.return_value.lookup_zipcode.return_value = []
            wizard = self.env["envia.quote.wizard"].with_context(
                envia_skip_branch_autoload=True,
            ).create(
                {
                    "sale_order_id": order.id,
                    "destination_partner_id": customer.id,
                }
            )
        self.assertEqual(wizard.destination_partner_id, delivery)
        self.assertTrue(wizard.destination_partner_readonly)
        self.assertEqual(wizard.destination_postal_code, delivery.zip)

    def test_wizard_destination_updates_when_delivery_address_changes(self):
        mexico = self.env.ref("base.mx")
        customer = self.env["res.partner"].create(
            {
                "name": "Dest Sync Customer",
                "street": "Av Vieja 1",
                "city": "CDMX",
                "zip": "06600",
                "country_id": mexico.id,
                "state_id": self.env.ref("base.state_mx_df").id,
            }
        )
        new_delivery = self.env["res.partner"].create(
            {
                "name": "Dest Sync Delivery",
                "type": "delivery",
                "parent_id": customer.id,
                "street": "Calle Nueva 9",
                "city": "Guadalajara",
                "zip": "44100",
                "country_id": mexico.id,
                "state_id": self.env.ref("base.state_mx_jal").id,
            }
        )
        product = self.env["product.product"].create(
            {"name": "Dest Sync Merchandise", "sale_ok": True, "list_price": 10.0}
        )
        order = self.env["sale.order"].create(
            {
                "partner_id": customer.id,
                "partner_invoice_id": customer.id,
                "partner_shipping_id": customer.id,
                "order_line": [(0, 0, {"product_id": product.id, "product_uom_qty": 1.0})],
            }
        )
        with patch(
            "odoo.addons.envia.wizards.envia_quote_wizard.EnviaGeocodesClient"
        ) as geocodes:
            geocodes.return_value.lookup_zipcode.return_value = []
            wizard = self.env["envia.quote.wizard"].with_context(
                envia_skip_branch_autoload=True,
            ).create({"sale_order_id": order.id})
        self.assertEqual(wizard.destination_partner_id, customer)
        order.partner_shipping_id = new_delivery
        wizard._apply_sale_order_destination()
        self.assertEqual(wizard.destination_partner_id, new_delivery)
        self.assertEqual(wizard.destination_postal_code, new_delivery.zip)
        self.assertEqual(wizard.destination_city, new_delivery.city)

    def test_address_line_sync_after_destination_change_no_missing_error(self):
        mexico = self.env.ref("base.mx")
        customer = self.env["res.partner"].create(
            {
                "name": "Addr Sync Customer",
                "street": "Av A 1",
                "city": "CDMX",
                "zip": "06600",
                "country_id": mexico.id,
            }
        )
        other = self.env["res.partner"].create(
            {
                "name": "Addr Sync Other",
                "type": "delivery",
                "parent_id": customer.id,
                "street": "Av B 2",
                "city": "Monterrey",
                "zip": "64000",
                "country_id": mexico.id,
            }
        )
        product = self.env["product.product"].create(
            {"name": "Addr Sync Merchandise", "sale_ok": True, "list_price": 10.0}
        )
        order = self.env["sale.order"].create(
            {
                "partner_id": customer.id,
                "partner_invoice_id": customer.id,
                "partner_shipping_id": customer.id,
                "order_line": [(0, 0, {"product_id": product.id, "product_uom_qty": 1.0})],
            }
        )
        with patch(
            "odoo.addons.envia.wizards.envia_quote_wizard.EnviaGeocodesClient"
        ) as geocodes:
            geocodes.return_value.lookup_zipcode.return_value = []
            wizard = self.env["envia.quote.wizard"].with_context(
                envia_skip_branch_autoload=True,
            ).create({"sale_order_id": order.id})
        wizard._sync_address_lines("destination")
        order.partner_shipping_id = other
        # Changing destination partner reshuffles address options; must not crash.
        wizard._apply_sale_order_destination()
        wizard._sync_address_lines("destination")
        self.assertEqual(wizard.destination_partner_id, other)
        self.assertTrue(wizard.destination_address_line_ids)

    def test_confirmed_sale_locks_origin_only(self):
        partner = self.env.company.partner_id
        product = self.env["product.product"].create(
            {
                "name": "Origin Lock Merchandise",
                "sale_ok": True,
                "type": "service",
                "list_price": 10.0,
            }
        )
        order = self.env["sale.order"].create(
            {
                "partner_id": partner.id,
                "partner_invoice_id": partner.id,
                "partner_shipping_id": partner.id,
                "order_line": [(0, 0, {"product_id": product.id, "product_uom_qty": 1.0})],
            }
        )
        order.action_confirm()
        with patch(
            "odoo.addons.envia.wizards.envia_quote_wizard.EnviaGeocodesClient"
        ) as geocodes:
            geocodes.return_value.lookup_zipcode.return_value = []
            wizard = self.env["envia.quote.wizard"].with_context(
                envia_skip_branch_autoload=True,
            ).create(
                {
                    "sale_order_id": order.id,
                    "destination_partner_id": order.partner_shipping_id.id,
                }
            )
        self.assertTrue(wizard.origin_readonly)
        self.assertEqual(wizard.origin_warehouse_id, order.warehouse_id)
        self.assertEqual(wizard.destination_partner_id, order.partner_shipping_id)

    def test_changing_delivery_address_flags_envia_recompute(self):
        mexico = self.env.ref("base.mx")
        customer = self.env["res.partner"].create(
            {
                "name": "Ship Recompute Customer",
                "street": "Av Uno 1",
                "city": "CDMX",
                "zip": "06600",
                "country_id": mexico.id,
            }
        )
        other_delivery = self.env["res.partner"].create(
            {
                "name": "Other Delivery",
                "type": "delivery",
                "parent_id": customer.id,
                "street": "Calle Dos 2",
                "city": "Monterrey",
                "zip": "64000",
                "country_id": mexico.id,
            }
        )
        product = self.env["product.product"].create(
            {"name": "Ship Recompute Merchandise", "sale_ok": True, "list_price": 10.0}
        )
        carrier = self.env.ref("envia.delivery_carrier_envia")
        order = self.env["sale.order"].create(
            {
                "partner_id": customer.id,
                "partner_invoice_id": customer.id,
                "partner_shipping_id": customer.id,
                "order_line": [(0, 0, {"product_id": product.id, "product_uom_qty": 1.0})],
            }
        )
        order.set_delivery_line(carrier, 120.0)
        order.envia_service_id = 23
        self.assertTrue(order.order_line.filtered("is_delivery"))
        self.assertEqual(order.carrier_id, carrier)
        order.partner_shipping_id = other_delivery
        # Native delivery style: keep shipping line, mark cost as stale.
        self.assertTrue(order.order_line.filtered("is_delivery"))
        self.assertEqual(order.carrier_id, carrier)
        self.assertEqual(order.envia_service_id, 23)
        self.assertTrue(order.recompute_delivery_price)

    def test_update_shipping_with_recompute_clears_stale_rates(self):
        mexico = self.env.ref("base.mx")
        customer = self.env["res.partner"].create(
            {
                "name": "Stale Rate Customer",
                "street": "Av Vieja 1",
                "city": "CDMX",
                "zip": "06600",
                "country_id": mexico.id,
                "state_id": self.env.ref("base.state_mx_df").id,
            }
        )
        new_delivery = self.env["res.partner"].create(
            {
                "name": "Stale Rate Delivery",
                "type": "delivery",
                "parent_id": customer.id,
                "street": "Calle Nueva 9",
                "city": "Guadalajara",
                "zip": "44100",
                "country_id": mexico.id,
                "state_id": self.env.ref("base.state_mx_jal").id,
            }
        )
        product = self.env["product.product"].create(
            {"name": "Stale Rate Merchandise", "sale_ok": True, "list_price": 10.0}
        )
        carrier = self.env.ref("envia.delivery_carrier_envia")
        order = self.env["sale.order"].create(
            {
                "partner_id": customer.id,
                "partner_invoice_id": customer.id,
                "partner_shipping_id": customer.id,
                "order_line": [(0, 0, {"product_id": product.id, "product_uom_qty": 1.0})],
            }
        )
        quote = self.env["envia.quote"].create(
            {
                "sale_order_id": order.id,
                "origin_postal_code": "67192",
                "origin_country": "MX",
                "destination_postal_code": "06600",
                "destination_country": "MX",
                "weight": 1.0,
                "content": "Test",
                "state": "quoted",
            }
        )
        service = self.env["envia.quote.service"].create(
            {
                "quote_id": quote.id,
                "service_id": "estafeta:ground",
                "carrier": "estafeta",
                "carrier_name": "Estafeta",
                "service_name": "Estafeta Terrestre",
                "price": 120.0,
                "currency_name": "MXN",
                "is_selected": True,
            }
        )
        quote.selected_service_id = service.id
        order.set_delivery_line(carrier, 120.0)
        order.partner_shipping_id = new_delivery
        self.assertTrue(order.recompute_delivery_price)
        with patch(
            "odoo.addons.envia.wizards.envia_quote_wizard.EnviaGeocodesClient"
        ) as geocodes:
            geocodes.return_value.lookup_zipcode.return_value = []
            wizard = self.env["choose.delivery.carrier"].with_context(
                carrier_recompute=True,
                envia_skip_branch_autoload=True,
            ).create(
                {
                    "order_id": order.id,
                    "carrier_id": carrier.id,
                }
            )
        quote_wizard = wizard.envia_wizard_id
        self.assertTrue(quote_wizard.is_seeded_from_order)
        self.assertFalse(quote_wizard.service_line_ids)
        self.assertFalse(wizard.envia_has_selected_rate)
        self.assertEqual(quote_wizard.destination_partner_id, new_delivery)
        self.assertEqual(quote_wizard.destination_postal_code, new_delivery.zip)

    def test_side_address_warning_uses_wizard_fields(self):
        mexico = self.env.ref("base.mx")
        partner = self.env["res.partner"].create(
            {
                "name": "Incomplete Shipping",
                "street": "",
                "city": "",
                "zip": "",
                "country_id": mexico.id,
            }
        )
        wizard = self.env["envia.quote.wizard"].with_context(
            envia_skip_branch_autoload=True
        ).create(
            {
                "destination_partner_id": partner.id,
                "destination_street": "Av Reforma 123",
                "destination_postal_code": "06400",
                "destination_city": "Ciudad de Mexico",
                "destination_country_id": mexico.id,
                "destination_state_id": self.env.ref("base.state_mx_df").id,
            }
        )
        self.assertFalse(wizard.destination_address_warning)

    def test_branch_select_applies_side_without_syncing_rate(self):
        mexico = self.env.ref("base.mx")
        wizard = self.env["envia.quote.wizard"].with_context(
            envia_skip_branch_autoload=True
        ).create(
            {
                "origin_location_type": "address",
                "destination_location_type": "branch",
                "origin_country_id": mexico.id,
                "origin_postal_code": "67192",
                "origin_city": "Guadalupe",
                "origin_state_id": self.env.ref("base.state_mx_nl").id,
                "origin_partner_id": self.env.company.partner_id.id,
                "destination_country_id": mexico.id,
                "destination_postal_code": "03100",
            }
        )
        branch = self.env["envia.quote.wizard.branch"].create(
            {
                "wizard_id": wizard.id,
                "side": "destination",
                "name": "Branch CX",
                "zip": "03100",
                "city": "Ciudad de Mexico",
                "state_code": "CX",
                "country_code": "MX",
                "branch_code": "RMX",
                "carrier": "estafeta",
            }
        )
        with patch.object(type(wizard), "action_get_quote", return_value=wizard._reopen_wizard()):
            branch.action_select_branch()
        self.assertFalse(wizard.service_line_ids)
        self.assertEqual(wizard.destination_postal_code, "03100")

    def test_extract_envia_branch_code_prefers_human_code_over_numeric_id(self):
        extract = self.env["envia.quote.wizard"]._extract_envia_branch_code
        self.assertEqual(
            extract({"id": 468, "branch_id": "MTY01", "address": {}}),
            "MTY01",
        )
        self.assertEqual(
            extract({"id": 468, "branchCode": "MTY01"}),
            "MTY01",
        )
        self.assertEqual(extract({"id": 468, "reference": "MTY01"}), "MTY01")
        self.assertEqual(extract({"id": 468, "branch_id": 468}), "468")

    def test_quote_location_values_accepts_numeric_branch_code(self):
        # Some carriers only return a numeric branch id; Get rate must still work.
        mexico = self.env.ref("base.mx")
        state = self.env["res.country.state"].search(
            [("country_id", "=", mexico.id)], limit=1
        )
        wizard = self.env["envia.quote.wizard"].with_context(
            envia_skip_branch_autoload=True
        ).create(
            {
                "origin_location_type": "address",
                "destination_location_type": "branch",
                "origin_country_id": mexico.id,
                "origin_postal_code": "06600",
                "origin_city": "CDMX",
                "origin_state_id": state.id,
                "destination_partner_id": self.env.company.partner_id.id,
                "destination_country_id": mexico.id,
                "destination_postal_code": "44100",
            }
        )
        branch = self.env["envia.quote.wizard.branch"].create(
            {
                "wizard_id": wizard.id,
                "side": "destination",
                "name": "FedEx Branch",
                "branch_code": "468",
                "external_id": "468",
                "carrier": "fedex",
                "zip": "44100",
                "city": "GDL",
                "country_code": "MX",
                "state_code": state.code,
                "is_selected": True,
            }
        )
        values = wizard._quote_location_values()
        self.assertEqual(values["destination_branch_code"], "468")
        self.assertEqual(branch._envia_branch_code(), "468")
        self.assertNotIn("origin_branch_code", values)

    def test_select_branch_option_uses_stable_branch_code(self):
        mexico = self.env.ref("base.mx")
        wizard = self.env["envia.quote.wizard"].with_context(
            envia_skip_branch_autoload=True
        ).create(
            {
                "destination_location_type": "branch",
                "destination_country_id": mexico.id,
                "destination_postal_code": "03100",
            }
        )
        self.env["envia.quote.wizard.branch"].create(
            {
                "wizard_id": wizard.id,
                "side": "destination",
                "name": "Branch CX",
                "zip": "03100",
                "city": "Ciudad de Mexico",
                "state_code": "CX",
                "country_code": "MX",
                "branch_code": "RMX",
                "external_id": "abc123",
                "carrier": "estafeta",
            }
        )
        with patch.object(type(wizard), "action_get_quote", return_value=wizard._reopen_wizard()):
            wizard.action_select_branch_option("destination", "RMX", "estafeta")
        selected = wizard.destination_branch_line_ids.filtered("is_selected")[:1]
        self.assertEqual(selected.branch_code, "RMX")

    def test_is_ready_for_auto_quote_only_for_ship_to_ship(self):
        mexico = self.env.ref("base.mx")
        wizard = self.env["envia.quote.wizard"].with_context(
            envia_skip_branch_autoload=True
        ).create(
            {
                "origin_location_type": "branch",
                "destination_location_type": "branch",
                "origin_country_id": mexico.id,
                "origin_postal_code": "67192",
                "origin_state_id": self.env.ref("base.state_mx_nl").id,
                "destination_country_id": mexico.id,
                "destination_postal_code": "03100",
                "destination_state_id": self.env.ref("base.state_mx_df").id,
            }
        )
        self.assertFalse(wizard._is_ready_for_auto_quote())
        wizard.write(
            {
                "origin_location_type": "address",
                "destination_location_type": "address",
                "origin_street": "Street 1",
                "origin_city": "Guadalupe",
                "origin_postal_code": "67192",
                "origin_state_id": self.env.ref("base.state_mx_nl").id,
                "destination_street": "Street 2",
                "destination_city": "Ciudad de Mexico",
                "destination_postal_code": "03100",
                "destination_state_id": self.env.ref("base.state_mx_df").id,
                "origin_partner_id": self.env.company.partner_id.id,
                "destination_partner_id": self.env.company.partner_id.id,
                "weight": 1.0,
            }
        )
        self.assertTrue(wizard.can_get_rates)
        self.assertTrue(wizard._is_ready_for_auto_quote())

    def test_postal_ready_for_branch_search(self):
        mexico = self.env.ref("base.mx")
        self.assertTrue(EnviaQuoteWizard._postal_ready_for_branch_search(mexico, "03100"))
        self.assertTrue(EnviaQuoteWizard._postal_ready_for_branch_search(mexico, "3100"))
        self.assertFalse(EnviaQuoteWizard._postal_ready_for_branch_search(mexico, ""))

    @patch("odoo.addons.envia.wizards.envia_quote_wizard.EnviaQuoteWizard._load_branches")
    def test_pickup_postal_code_change_does_not_auto_load_branches(self, mock_load):
        mexico = self.env.ref("base.mx")
        wizard = self.env["envia.quote.wizard"].with_context(
            envia_skip_branch_autoload=True
        ).create(
            {
                "destination_location_type": "branch",
                "destination_country_id": mexico.id,
            }
        )
        self.env["envia.quote.wizard"].browse(wizard.id).write(
            {"destination_postal_code": "03100"}
        )
        mock_load.assert_not_called()

    @patch("odoo.addons.envia.wizards.envia_quote_wizard.EnviaQuoteWizard._load_branches")
    def test_refresh_keeps_branch_lines_when_postal_unchanged(self, mock_load):
        mexico = self.env.ref("base.mx")
        wizard = self.env["envia.quote.wizard"].with_context(
            envia_skip_branch_autoload=True
        ).create(
            {
                "destination_location_type": "branch",
                "destination_country_id": mexico.id,
                "destination_postal_code": "03100",
                "weight": 1.0,
            }
        )
        branch = self.env["envia.quote.wizard.branch"].create(
            {
                "wizard_id": wizard.id,
                "side": "destination",
                "name": "Branch",
                "zip": "03100",
                "country_code": "MX",
                "carrier": "estafeta",
            }
        )
        with patch.object(type(wizard), "action_get_quote", return_value=False):
            wizard.action_refresh_wizard_view()
        mock_load.assert_not_called()
        self.assertTrue(branch.exists())
        self.assertEqual(branch, wizard.destination_branch_line_ids[:1])

    def test_branch_route_shows_service_rates_when_quoted(self):
        mexico = self.env.ref("base.mx")
        wizard = self.env["envia.quote.wizard"].with_context(
            envia_skip_branch_autoload=True
        ).create(
            {
                "origin_location_type": "address",
                "destination_location_type": "branch",
                "destination_country_id": mexico.id,
                "destination_postal_code": "03100",
            }
        )
        self.assertFalse(wizard.show_service_rates)
        self.env["envia.quote.wizard.service"].create(
            {
                "wizard_id": wizard.id,
                "service_id": "estafeta-1",
                "carrier": "estafeta",
                "service_name": "Terrestre",
                "price": 185.72,
            }
        )
        self.assertTrue(wizard.show_service_rates)

    @patch("odoo.addons.envia.wizards.envia_quote_wizard.EnviaQuoteWizard._load_branches")
    def test_rate_selection_loads_carrier_branches(self, mock_load):
        mexico = self.env.ref("base.mx")
        wizard = self.env["envia.quote.wizard"].with_context(
            envia_skip_branch_autoload=True
        ).create(
            {
                "origin_location_type": "branch",
                "destination_location_type": "branch",
                "origin_country_id": mexico.id,
                "origin_postal_code": "67192",
                "origin_state_id": self.env.ref("base.state_mx_nl").id,
                "destination_country_id": mexico.id,
                "destination_postal_code": "03100",
                "destination_state_id": self.env.ref("base.state_mx_df").id,
            }
        )
        self.env["envia.quote.wizard.service"].create(
            {
                "wizard_id": wizard.id,
                "service_id": "ups:ground",
                "carrier": "ups",
                "carrier_name": "UPS",
                "service_name": "Ground",
                "price": 200.0,
                "currency_name": "MXN",
            }
        )
        wizard.action_select_service_rate(service_id="ups:ground")
        mock_load.assert_called_once()
        self.assertEqual(mock_load.call_args.args[0], "destination")
        self.assertEqual(mock_load.call_args.kwargs.get("carrier_codes"), "ups")

    @patch(
        "odoo.addons.envia.wizards.envia_quote_wizard.EnviaQuoteWizard._probe_branch_route_carriers",
        return_value=["dhl"],
    )
    @patch("odoo.addons.envia.wizards.envia_quote_wizard.EnviaQuoteWizard._load_branches")
    def test_ship_to_pickup_clears_ship_rates_and_loads_branches(self, mock_load, _mock_probe):
        mexico = self.env.ref("base.mx")
        wizard = self.env["envia.quote.wizard"].with_context(
            envia_skip_branch_autoload=True
        ).create(
            {
                "origin_location_type": "address",
                "destination_location_type": "address",
                "destination_country_id": mexico.id,
                "destination_postal_code": "06500",
                "destination_state_id": self.env.ref("base.state_mx_df").id,
            }
        )
        rate = self.env["envia.quote.wizard.service"].create(
            {
                "wizard_id": wizard.id,
                "service_id": "dhl:ship",
                "carrier": "dhl",
                "carrier_name": "DHL",
                "service_name": "Economy",
                "price": 281.16,
                "currency_name": "MXN",
                "drop_off": 0,
                "is_selected": True,
            }
        )
        self.env["envia.quote.wizard"].browse(wizard.id).write(
            {"destination_location_type": "branch"}
        )
        self.assertFalse(rate.exists())
        self.assertFalse(wizard.service_line_ids)
        # Destination ocurre picker appears only after a rate is selected again.
        self.assertFalse(wizard.show_destination_branch_picker)
        self.assertTrue(
            any(call.args[0] == "destination" for call in mock_load.call_args_list)
        )

    def test_branch_apply_write_keeps_rates_after_quote(self):
        mexico = self.env.ref("base.mx")
        nl_state = self.env.ref("base.state_mx_nl")
        wizard = self.env["envia.quote.wizard"].with_context(
            envia_skip_branch_autoload=True
        ).create(
            {
                "origin_location_type": "branch",
                "destination_location_type": "branch",
                "origin_country_id": mexico.id,
                "origin_postal_code": "67175",
                "origin_state_id": nl_state.id,
                "destination_country_id": mexico.id,
                "destination_postal_code": "03810",
                "destination_state_id": self.env.ref("base.state_mx_df").id,
            }
        )
        self.env["envia.quote.wizard.service"].create(
            {
                "wizard_id": wizard.id,
                "service_id": "estafeta:ground",
                "carrier": "estafeta",
                "carrier_name": "Estafeta",
                "service_name": "Terrestre",
                "price": 185.72,
                "currency_name": "MXN",
                "is_selected": True,
            }
        )
        wizard.with_context(envia_apply_branch=True).write(
            {
                "origin_postal_code": "67175",
                "origin_state_id": nl_state.id,
            }
        )
        self.assertTrue(wizard.service_line_ids)
        wizard.write({"origin_postal_code": "67176"})
        self.assertFalse(wizard.service_line_ids)

    def test_choose_service_survives_unchanged_wizard_write(self):
        mexico = self.env.ref("base.mx")
        wizard = self.env["envia.quote.wizard"].with_context(
            envia_skip_branch_autoload=True
        ).create(
            {
                "origin_postal_code": "67192",
                "origin_city": "Guadalupe",
                "origin_country_id": mexico.id,
                "destination_postal_code": "03100",
                "destination_city": "Ciudad de Mexico",
                "destination_country_id": mexico.id,
            }
        )
        service = self.env["envia.quote.wizard.service"].create(
            {
                "wizard_id": wizard.id,
                "service_id": "fedex-ground",
                "carrier": "fedex",
                "service_name": "Ground",
                "price": 120.0,
            }
        )
        wizard.write({"origin_postal_code": "67192"})
        self.assertEqual(len(wizard.service_line_ids), 1)
        service.action_choose_service()
        self.assertTrue(wizard.service_line_ids.filtered("is_selected"))

    def test_select_service_rate_on_wizard(self):
        wizard = self.env["envia.quote.wizard"].with_context(
            envia_skip_branch_autoload=True
        ).create({})
        self.env["envia.quote.wizard.service"].create(
            {
                "wizard_id": wizard.id,
                "service_id": "fedex-ground",
                "carrier": "fedex",
                "service_name": "Ground",
                "price": 120.0,
            }
        )
        wizard.with_context(service_id="fedex-ground").action_select_service_rate()
        self.assertTrue(wizard.service_line_ids.filtered("is_selected"))

    def test_parse_generate_response_rejects_empty_payload(self):
        with self.assertRaises(UserError):
            EnviaOfficialAdapter._parse_generate_response({}, "estafeta", "ground")

    def test_parse_generate_response_reads_package_tracking(self):
        response = EnviaOfficialAdapter._parse_generate_response(
            {
                "meta": "generate",
                "data": [
                    {
                        "carrier": "estafeta",
                        "service": "ground",
                        "shipmentId": 123,
                        "packages": [
                            {
                                "trackingNumber": "9998887776",
                                "label": "https://example.com/label.pdf",
                            }
                        ],
                        "totalPrice": 185.72,
                        "currency": "MXN",
                    }
                ],
            },
            "estafeta",
            "ground",
        )
        self.assertEqual(response.tracking_number, "9998887776")
        self.assertEqual(response.label_url, "https://example.com/label.pdf")
        self.assertEqual(response.pricing_total, 185.72)

    def test_quote_build_shipment_contact_uses_pickup_branch(self):
        quote = self.env["envia.quote"].create(
            {
                "origin_postal_code": "67192",
                "origin_country": "MX",
                "origin_state": "NL",
                "origin_city": "Guadalupe",
                "origin_location_type": "address",
                "destination_postal_code": "03100",
                "destination_country": "MX",
                "destination_state": "CX",
                "destination_city": "Ciudad de Mexico",
                "destination_location_type": "branch",
                "destination_branch_code": "RMX",
                "destination_branch_name": "Estafeta Benito Juarez",
                "destination_branch_street": "Av. Coyoacan 745",
                "weight": 1.0,
                "content": "Test",
                "origin_partner_id": self.env.company.partner_id.id,
            }
        )
        contact = quote._build_shipment_contact("destination")
        self.assertEqual(contact.branch_code, "RMX")
        self.assertEqual(contact.street, "Av. Coyoacan 745")

    def test_branch_selection_maps_envia_cx_state_to_odoo(self):
        mexico = self.env.ref("base.mx")
        expected_state = self.env.ref("base.state_mx_df")
        wizard = self.env["envia.quote.wizard"].with_context(
            envia_skip_branch_autoload=True
        ).create(
            {
                "destination_location_type": "branch",
                "destination_country_id": mexico.id,
                "destination_postal_code": "03100",
            }
        )
        branch = self.env["envia.quote.wizard.branch"].create(
            {
                "wizard_id": wizard.id,
                "side": "destination",
                "name": "Branch CX",
                "zip": "03100",
                "city": "Ciudad de Mexico",
                "state_code": "CX",
                "country_code": "MX",
                "branch_code": "RMX",
                "carrier": "paquetexpress",
            }
        )
        with patch.object(type(wizard), "action_get_quote", return_value=wizard._reopen_wizard()):
            branch.action_select_branch()
        self.assertEqual(wizard.destination_state_id, expected_state)
        contact = wizard._build_contact_for_side("destination")
        self.assertEqual(contact.state, "CX")
        self.assertEqual(contact.branch_code, "RMX")

    def test_quote_filters_services_by_carrier(self):
        from odoo.addons.envia.services.dto import Contact, QuoteRequest
        from odoo.addons.envia.services.envia_official_adapter import EnviaOfficialAdapter

        request = QuoteRequest(
            origin_postal_code="67192",
            origin_country="MX",
            origin_state="NL",
            destination_postal_code="03100",
            destination_country="MX",
            destination_state="CX",
            weight=1.0,
            content="Package",
            origin_contact=Contact(
                name="Origin",
                street="Street",
                city="Guadalupe",
                state="NL",
                postal_code="67192",
                country="MX",
                phone="5555555555",
                email="origin@example.com",
            ),
            destination_contact=Contact(
                name="Branch",
                street="Branch",
                city="Ciudad de Mexico",
                state="CX",
                postal_code="03100",
                country="MX",
                phone="5555555555",
                email="dest@example.com",
                branch_code="RMX",
            ),
            carriers="dhl",
        )
        adapter = EnviaOfficialAdapter(object(), shop_id="34084", default_carriers="dhl,fedex")
        self.assertEqual(adapter._resolve_carriers("all"), ["dhl", "fedex"])
        payload = EnviaOfficialAdapter._build_checkout_payload(request)
        self.assertEqual(payload["currency"], "MXN")
        self.assertEqual(payload["package"]["weight"], "1.0")

    def test_sync_envia_shipping_line_creates_order_line(self):
        partner = self.env.company.partner_id
        product = self.env["product.product"].search([("sale_ok", "=", True)], limit=1)
        order = self.env["sale.order"].create(
            {
                "partner_id": partner.id,
                "partner_invoice_id": partner.id,
                "partner_shipping_id": partner.id,
                "order_line": [(0, 0, {"product_id": product.id, "product_uom_qty": 1.0})],
            }
        )
        quote = self.env["envia.quote"].create(
            {
                "sale_order_id": order.id,
                "origin_postal_code": "67192",
                "origin_country": "MX",
                "destination_postal_code": "03100",
                "destination_country": "MX",
                "weight": 1.0,
                "content": "Test",
                "state": "quoted",
            }
        )
        service = self.env["envia.quote.service"].create(
            {
                "quote_id": quote.id,
                "service_id": "estafeta:ground",
                "envia_service_id": 23,
                "carrier": "estafeta",
                "carrier_name": "Estafeta",
                "service_name": "Estafeta Terrestre",
                "price": 569.56,
                "currency_name": "MXN",
                "is_selected": True,
            }
        )
        quote.selected_service_id = service.id
        shipping_product = self.env.ref("envia.product_envia_shipping")
        order._sync_envia_shipping_line()
        shipping_line = order.order_line.filtered("is_delivery")
        self.assertEqual(len(shipping_line), 1)
        self.assertEqual(shipping_line.price_unit, 569.56)
        self.assertEqual(shipping_line.name, "Estafeta · Estafeta Terrestre")
        self.assertEqual(order.envia_service_id, 23)
        service.price = 600.0
        order._sync_envia_shipping_line()
        shipping_line = order.order_line.filtered("is_delivery")
        self.assertEqual(shipping_line.price_unit, 600.0)

    def test_delivery_line_description_uses_selected_branch_quote_in_draft(self):
        partner = self.env.company.partner_id
        product = self.env["product.product"].search([("sale_ok", "=", True)], limit=1)
        carrier = self.env.ref("envia.delivery_carrier_envia")
        order = self.env["sale.order"].create(
            {
                "partner_id": partner.id,
                "partner_invoice_id": partner.id,
                "partner_shipping_id": partner.id,
                "order_line": [(0, 0, {"product_id": product.id, "product_uom_qty": 1.0})],
            }
        )
        quote = self.env["envia.quote"].create(
            {
                "sale_order_id": order.id,
                "origin_postal_code": "67192",
                "origin_country": "MX",
                "destination_postal_code": "03100",
                "destination_country": "MX",
                "destination_location_type": "branch",
                "destination_branch_code": "MEX05",
                "weight": 1.0,
                "content": "Test",
                "state": "draft",
            }
        )
        service = self.env["envia.quote.service"].create(
            {
                "quote_id": quote.id,
                "service_id": "paquetexpress:ground_do",
                "carrier": "paquetexpress",
                "carrier_name": "Paquetexpress",
                "service_name": "Paquetexpress Domicilio - Ocurre",
                "price": 39.44,
                "currency_name": "MXN",
                "drop_off": 2,
                "is_selected": True,
            }
        )
        quote.selected_service_id = service.id
        order.set_delivery_line(carrier, 39.44)
        shipping_line = order.order_line.filtered("is_delivery")
        self.assertEqual(
            shipping_line.name,
            "Paquetexpress · Paquetexpress Domicilio - Ocurre",
        )

    def test_sale_order_package_content_excludes_envia_shipping_line(self):
        partner = self.env.company.partner_id
        shipping_product = self.env.ref("envia.product_envia_shipping")
        product = self.env["product.product"].search(
            [("sale_ok", "=", True), ("id", "!=", shipping_product.id)],
            limit=1,
        )
        if not product:
            product = self.env["product.product"].create(
                {
                    "name": "QA Package Product",
                    "sale_ok": True,
                    "list_price": 10.0,
                }
            )
        order = self.env["sale.order"].create(
            {
                "partner_id": partner.id,
                "partner_invoice_id": partner.id,
                "partner_shipping_id": partner.id,
                "order_line": [
                    (0, 0, {"product_id": product.id, "product_uom_qty": 1.0, "name": product.display_name}),
                    (0, 0, {"product_id": shipping_product.id, "product_uom_qty": 1.0, "name": shipping_product.display_name}),
                ],
            }
        )
        long_description = "Classic Brown Jacket " + ("Lightweight bomber jacket. " * 8)
        order.order_line.filtered(lambda line: line.product_id == product).name = long_description
        content = PayloadMapper.sale_order_package_content(order)
        self.assertNotIn("ENVIA-SHIP", content)
        self.assertLessEqual(len(content), PayloadMapper.PACKAGE_CONTENT_MAX_LENGTH)
        items = PayloadMapper.sale_lines_to_items(order)
        self.assertEqual(len(items), 1)
        self.assertLessEqual(len(items[0].description), PayloadMapper.PACKAGE_CONTENT_MAX_LENGTH)

    def test_quote_shipment_available_before_confirm(self):
        partner = self.env.company.partner_id
        product = self.env["product.product"].search([("sale_ok", "=", True)], limit=1)
        order = self.env["sale.order"].create(
            {
                "partner_id": partner.id,
                "partner_invoice_id": partner.id,
                "partner_shipping_id": partner.id,
                "order_line": [(0, 0, {"product_id": product.id, "product_uom_qty": 1.0})],
            }
        )
        action = order.action_open_envia_quote_wizard()
        self.assertEqual(action["context"]["default_sale_order_id"], order.id)
        self.assertEqual(order.state, "draft")

    def test_label_ready_branch_to_home_does_not_require_origin_code(self):
        # Origin=branch means drop at any carrier location; no concrete origin code.
        quote = self.env["envia.quote"].create(
            {
                "origin_postal_code": "67192",
                "origin_country": "MX",
                "origin_location_type": "branch",
                "destination_postal_code": "03100",
                "destination_country": "MX",
                "destination_location_type": "address",
                "weight": 1.0,
                "content": "Package",
                "state": "quoted",
            }
        )
        service = self.env["envia.quote.service"].create(
            {
                "quote_id": quote.id,
                "service_id": "estafeta:ground",
                "carrier": "estafeta",
                "service_name": "Terrestre",
                "price": 100.0,
                "drop_off": 1,
                "is_selected": True,
            }
        )
        quote.selected_service_id = service.id
        self.assertTrue(quote._is_label_ready())
        quote.company_id.envia_enable_labels = True
        quote._validate_label_generation()

    def test_envia_module_json_payload(self):
        partner = self.env.company.partner_id
        product = self.env["product.product"].search([("sale_ok", "=", True)], limit=1)
        order = self.env["sale.order"].create(
            {
                "partner_id": partner.id,
                "partner_invoice_id": partner.id,
                "partner_shipping_id": partner.id,
                "order_line": [(0, 0, {"product_id": product.id, "product_uom_qty": 1.0})],
            }
        )
        quote = self.env["envia.quote"].create(
            {
                "sale_order_id": order.id,
                "origin_postal_code": "67192",
                "origin_country": "MX",
                "destination_postal_code": "66425",
                "destination_country": "MX",
                "destination_state": "NL",
                "destination_city": "SAN NICOLAS DE LOS GARZA",
                "destination_location_type": "branch",
                "destination_branch_code": "MTY01",
                "destination_branch_name": "Office",
                "destination_branch_street": "CALIDAD TOTAL, 520,520-B,520-C",
                "weight": 1.0,
                "content": "Test",
                "state": "quoted",
            }
        )
        service = self.env["envia.quote.service"].create(
            {
                "quote_id": quote.id,
                "service_id": "paquetexpress:ground_do",
                "envia_service_id": 442,
                "carrier": "paquetexpress",
                "carrier_name": "Paquetexpress",
                "service_name": "Paquetexpress Domicilio - Ocurre",
                "price": 307.56,
                "currency_name": "MXN",
                "estimated_delivery_days": 2,
                "drop_off": 2,
                "is_selected": True,
            }
        )
        quote.selected_service_id = service.id
        order._envia_sync_service_id_from_quote(quote)

        payload = order.read(["envia_module", "envia_service_id", "envia_summary"])[0]
        self.assertEqual(payload["envia_service_id"], 442)
        module = payload["envia_module"]
        self.assertEqual(module["branch_code"], "MTY01")
        self.assertEqual(module["service_id"], "442")
        self.assertEqual(module["service_name"], "Paquetexpress Domicilio - Ocurre")
        self.assertEqual(module["carrier_id"], "paquetexpress")
        self.assertEqual(module["carrier_name"], "Paquetexpress")
        self.assertEqual(module["cost"], "307.56")
        for value in module.values():
            self.assertIsInstance(value, str)
        self.assertNotIn("meta_data", module)
        self.assertNotIn("quote_ids", module)
        self.assertNotIn("442", payload["envia_summary"] or "")
        self.assertIn("Paquetexpress", payload["envia_summary"])
        self.assertIn("307.56", payload["envia_summary"])

        order.envia_quote_ids.destination_branch_code = "468"
        module = order.read(["envia_module"])[0]["envia_module"]
        self.assertEqual(module["branch_code"], "468")
