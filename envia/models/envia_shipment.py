import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from ..services.dto import CreateShipmentRequest
from ..services.envia_client import EnviaApiError
from ..services.envia_official_adapter import EnviaOfficialAdapter
from ..services.payload_mapper import PayloadMapper, get_envia_adapter

_logger = logging.getLogger(__name__)


class EnviaShipment(models.Model):
    _name = "envia.shipment"
    _description = "Envia Shipment"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "create_date desc"

    name = fields.Char(default="New", required=True, copy=False)
    quote_id = fields.Many2one("envia.quote", ondelete="set null")
    selected_service_id = fields.Many2one("envia.quote.service")
    sale_order_id = fields.Many2one("sale.order", ondelete="set null")
    picking_id = fields.Many2one("stock.picking", ondelete="set null")
    company_id = fields.Many2one("res.company", default=lambda self: self.env.company, required=True)
    external_shipment_id = fields.Char(string="External Shipment ID")
    external_order_id = fields.Char(
        string="Envia Order ID",
        help="Envia ecommerce orderId from label/create; used to unlink fulfillment.",
    )
    tracking_number = fields.Char(tracking=True)
    carrier = fields.Char()
    carrier_name = fields.Char()
    service_name = fields.Char()
    status = fields.Char(tracking=True)
    status_description = fields.Text()
    label_url = fields.Char()
    label_attachment_id = fields.Many2one("ir.attachment", string="Label PDF")
    pricing_total = fields.Float()
    pricing_currency_id = fields.Many2one("res.currency")
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("created", "Created"),
            ("in_transit", "In Transit"),
            ("delivered", "Delivered"),
            ("replaced", "Replaced"),
            ("cancelled", "Cancelled"),
        ],
        default="draft",
        tracking=True,
    )

    _INACTIVE_STATES = frozenset({"cancelled", "replaced"})

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", "New") == "New":
                vals["name"] = self.env["ir.sequence"].next_by_code("envia.shipment") or "New"
        return super().create(vals_list)

    def _is_active(self):
        self.ensure_one()
        return self.state not in self._INACTIVE_STATES

    @api.model
    def _get_envia_adapter(self, company):
        return get_envia_adapter(company)

    def _mark_replaced(self):
        """Mark shipments inactive after Envia unlink (or local-only fallback)."""
        self.filtered(lambda item: item.state not in self._INACTIVE_STATES).write(
            {"state": "replaced", "status": "replaced"}
        )

    def _envia_resolved_order_id(self):
        """Envia orderId on the shipment, or fallback from the sale order."""
        self.ensure_one()
        order_id = (self.external_order_id or "").strip()
        if order_id:
            return order_id
        sale = self.sale_order_id
        if sale:
            return (sale.envia_external_order_id or "").strip()
        return ""

    def _envia_delete_order_shipment(self):
        """DELETE fulfillment link on Envia queries API for this shipment."""
        self.ensure_one()
        external_id = (self.external_shipment_id or "").strip()
        if not external_id:
            _logger.info(
                "Skip Envia order-shipments unlink for %s: no external_shipment_id",
                self.name,
            )
            return False
        envia_order_id = self._envia_resolved_order_id()
        if not envia_order_id:
            _logger.warning(
                "Skip Envia order-shipments unlink for %s: no external_order_id "
                "(label/create orderId). Continuing with local unlink only.",
                self.name,
            )
            return False
        try:
            shipment_id = int(external_id)
            order_id = int(envia_order_id)
        except ValueError as error:
            raise UserError(
                _(
                    "Invalid Envia order/shipment id on %(name)s "
                    "(order=%(order)s, shipment=%(shipment)s).",
                    name=self.name,
                    order=envia_order_id,
                    shipment=external_id,
                )
            ) from error
        adapter = get_envia_adapter(self.company_id)
        queries_base_url = self.company_id._envia_get_queries_base_url()
        try:
            adapter.unlink_order_shipment(
                order_id,
                shipment_id,
                queries_base_url=queries_base_url,
            )
        except EnviaApiError as error:
            message = str(error.args[0] if error.args else error).lower()
            # Already detached / unknown order on Envia — continue local unlink.
            if "404" in message or "cannot be found" in message or "not found" in message:
                _logger.warning(
                    "Envia order-shipments unlink skipped for shipment %s "
                    "(envia order %s): %s",
                    shipment_id,
                    order_id,
                    error,
                )
                return False
            raise
        if not (self.external_order_id or "").strip():
            self.external_order_id = str(order_id)
        return True

    def _unlink_on_envia(self):
        """Unlink each active shipment on Envia, then mark replaced locally."""
        active = self.filtered(lambda item: item._is_active())
        for shipment in active:
            shipment._envia_delete_order_shipment()
        active._mark_replaced()

    def _cancel_on_envia(self):
        """Deprecated: use Replace Envia Label (unlink + re-quote)."""
        self.ensure_one()
        raise UserError(
            _(
                "Envia labels are not cancelled from Odoo. "
                "Use Replace Envia Label on the delivery to unlink and create a new one."
            )
        )

    def action_open_label(self):
        self.ensure_one()
        if self.label_url:
            return {
                "type": "ir.actions.act_url",
                "url": self.label_url,
                "target": "new",
            }
        if self.label_attachment_id:
            return {
                "type": "ir.actions.act_url",
                "url": f"/web/content/{self.label_attachment_id.id}?download=true",
                "target": "self",
            }
        raise UserError(_("No label is available for this shipment yet."))

    @api.model
    def _create_from_label_response(self, response, picking):
        """Bookkeep label/create when no quote record is available."""
        picking.ensure_one()
        currency = False
        if response.pricing_currency:
            currency = self.env["res.currency"].with_context(active_test=False).search(
                [("name", "=", response.pricing_currency)], limit=1
            )
        return self.create(
            {
                "sale_order_id": picking.sale_id.id,
                "picking_id": picking.id,
                "company_id": picking.company_id.id,
                "external_shipment_id": (
                    str(response.shipment_id) if response.shipment_id else False
                ),
                "external_order_id": (
                    str(response.order_id) if response.order_id else False
                ),
                "tracking_number": response.tracking_number,
                "carrier": response.carrier,
                "carrier_name": response.carrier_name,
                "service_name": response.service,
                "status": response.status,
                "status_description": response.status_description,
                "label_url": response.label_url,
                "pricing_total": response.pricing_total,
                "pricing_currency_id": currency.id if currency else False,
                "state": "created",
            }
        )

    @api.model
    def create_from_api_response(self, response, quote, picking=None):
        """Persist bookkeeping; Envia keeps the PDF — we only store the URL."""
        if not quote:
            if not picking:
                raise UserError(_("A delivery is required to register the Envia label."))
            return self._create_from_label_response(response, picking)
        currency = False
        if response.pricing_currency:
            currency = self.env["res.currency"].with_context(active_test=False).search(
                [("name", "=", response.pricing_currency)], limit=1
            )
        picking = picking or quote.picking_id
        shipment = self.create(
            {
                "quote_id": quote.id,
                "selected_service_id": quote.selected_service_id.id,
                "sale_order_id": quote.sale_order_id.id or (picking.sale_id.id if picking else False),
                "picking_id": picking.id if picking else False,
                "external_shipment_id": str(response.shipment_id) if response.shipment_id else False,
                "external_order_id": str(response.order_id) if response.order_id else False,
                "tracking_number": response.tracking_number,
                "carrier": response.carrier,
                "carrier_name": response.carrier_name,
                "service_name": response.service,
                "status": response.status,
                "status_description": response.status_description,
                "label_url": response.label_url,
                "pricing_total": response.pricing_total,
                "pricing_currency_id": currency.id if currency else False,
                "state": "created",
            }
        )
        if shipment.picking_id and response.tracking_number:
            if (
                not self.env.context.get("envia_skip_picking_tracking")
                and not shipment.picking_id.carrier_tracking_ref
            ):
                shipment.picking_id.carrier_tracking_ref = response.tracking_number
        quote.state = "used"
        if (
            response.label_url
            and shipment.picking_id
            and not self.env.context.get("envia_skip_label_download")
            and not shipment.picking_id._envia_has_label_url_message()
        ):
            try:
                with self.env.cr.savepoint():
                    shipment.picking_id._envia_post_label_url(
                        label_url=response.label_url,
                        tracking_number=response.tracking_number,
                    )
            except Exception as error:  # noqa: BLE001
                _logger.warning(
                    "Envia label URL chatter post failed for %s: %s",
                    shipment.name,
                    error,
                )
        return shipment

    @api.model
    def create_bookkeeping_from_picking(self, picking, quote=None):
        """Create envia.shipment for a Core picking that already has tracking."""
        picking.ensure_one()
        existing = picking.envia_shipment_ids.filtered(
            lambda item: item._is_active()
        )[:1]
        if existing:
            return existing
        tracking = (picking.carrier_tracking_ref or "").strip()
        if not tracking:
            raise UserError(
                _("Transfer %(name)s has no tracking to register.", name=picking.name)
            )
        quote = quote or picking._get_active_envia_quote()
        service = quote.selected_service_id if quote else self.env["envia.quote.service"]
        shipment = self.create(
            {
                "quote_id": quote.id if quote else False,
                "selected_service_id": service.id if service else False,
                "sale_order_id": picking.sale_id.id,
                "picking_id": picking.id,
                "tracking_number": tracking.split(",")[0].strip(),
                "carrier": service.carrier if service else False,
                "carrier_name": (service.carrier_name or service.carrier) if service else picking.carrier_id.name,
                "service_name": service.service_name if service else False,
                "status": "created",
                "status_description": _("Recovered from delivery order tracking"),
                "label_url": picking.envia_label_url or False,
                "pricing_total": service.price if service else False,
                "state": "created",
            }
        )
        if quote and quote.state != "used":
            quote.state = "used"
        return shipment

    @api.model
    def action_create_shipment_from_quote(self, quote, picking=None):
        quote._validate_label_generation()
        selected = quote.selected_service_id
        sale_order = quote.sale_order_id
        picking = (
            picking
            or quote.picking_id
            or (sale_order.picking_ids[:1] if sale_order else False)
        )
        mapper = PayloadMapper()
        request = CreateShipmentRequest(
            quote_id=quote.quote_id,
            service_id=selected.service_id,
            origin_contact=quote._build_shipment_contact("origin"),
            destination_contact=quote._build_shipment_contact("destination"),
            items=mapper.sale_lines_to_items(sale_order) if sale_order else [],
            order_reference=sale_order.name if sale_order else quote.name,
            print_format=quote.company_id.envia_label_format,
            print_size=quote.company_id.envia_label_size,
            carrier=selected.carrier,
            service_name=selected.service_name,
            package_weight=quote.weight,
            package_content=quote.content,
            weight_unit=PayloadMapper.envia_weight_unit(quote.env),
        )
        expected_drop_off = EnviaOfficialAdapter._expected_drop_off(
            request.origin_contact,
            request.destination_contact,
        )
        if (
            expected_drop_off is not None
            and selected.drop_off
            and selected.drop_off != expected_drop_off
        ):
            raise UserError(
                _(
                    "The selected rate is not valid for this pickup route. "
                    "Reload branches and generate the label again."
                )
            )
        adapter = self._get_envia_adapter(quote.company_id)
        response = adapter.create_shipment(request)
        return self.create_from_api_response(response, quote, picking=picking)
