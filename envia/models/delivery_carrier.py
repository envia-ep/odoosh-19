import logging

from odoo import SUPERUSER_ID, _, api, fields, models
from odoo.exceptions import UserError
from odoo.http import request
from odoo.modules.registry import Registry

from ..services.envia_config import ENVIA_PUBLIC_TRACKING_URL
from ..services.payload_mapper import PayloadMapper, get_envia_adapter
from ..services.website_pickup import WebsitePickupService

_logger = logging.getLogger(__name__)


class DeliveryCarrier(models.Model):
    _inherit = "delivery.carrier"

    delivery_type = fields.Selection(
        selection_add=[("envia", "Envia.com")],
        ondelete={"envia": "set default"},
    )
    envia_use_locations = fields.Boolean(
        string="Envia Pickup Locations",
        compute="_compute_envia_use_locations",
        help="Native pickup block stays off; Envia Ship/Pickup panel owns website UX.",
    )

    @api.depends("delivery_type", "company_id.envia_enable_branches")
    def _compute_envia_use_locations(self):
        # Custom Ship|Pickup panel owns the UX; keep native pickup block off.
        for carrier in self:
            carrier.envia_use_locations = False

    def envia_send_shipping(self, pickings):
        """Core dispatcher: create label from Generate Envia Label / send_to_shipper.

        Uses ecommerce ``label/create/{shop_id}`` with the linked sale order id.

        After ``label/create``, bookkeeping is committed in a dedicated cursor so a
        PostgreSQL serialization retry (Envia XML-RPC writes the picking concurrently)
        does not call ``label/create`` again without an unlink path.
        """
        self.ensure_one()
        result = []
        for picking in pickings:
            sale_order = picking.sale_id
            existing = picking.envia_shipment_ids.filtered(
                lambda item: item._is_active()
            )[:1]
            if not existing and sale_order:
                existing = sale_order.envia_shipment_ids.filtered(
                    lambda item: item._is_active()
                    and (not item.picking_id or item.picking_id == picking)
                )[:1]
                if existing and not existing.picking_id:
                    existing.picking_id = picking.id
            if existing:
                picking._envia_ensure_chatter_label(
                    label_url=existing.label_url,
                    tracking_number=existing.tracking_number,
                )
                result.append(
                    {
                        "exact_price": existing.pricing_total or 0.0,
                        "tracking_number": existing.tracking_number or "",
                    }
                )
                continue
            # Core skips send_to_shipper once tracking exists; recover Envios + PDF.
            if (picking.carrier_tracking_ref or "").strip():
                shipment = self._envia_recover_shipping_after_tracking(picking)
                picking._envia_ensure_chatter_label(
                    label_url=shipment.label_url,
                    tracking_number=shipment.tracking_number
                    or picking.carrier_tracking_ref,
                )
                result.append(
                    {
                        "exact_price": shipment.pricing_total or 0.0,
                        "tracking_number": shipment.tracking_number
                        or picking.carrier_tracking_ref
                        or "",
                    }
                )
                continue
            if not sale_order:
                raise UserError(
                    _(
                        "Envia label/create requires a sales order on "
                        "transfer %(name)s.",
                        name=picking.name,
                    )
                )
            quote = picking._get_active_envia_quote()
            if not quote:
                raise UserError(
                    _(
                        "Get Envia rates and select a carrier before sending "
                        "transfer %(name)s to the shipper.",
                        name=picking.name,
                    )
                )
            if not quote.picking_id:
                quote.picking_id = picking.id
            # Keep envia_module / service id visible to Envia before label/create.
            sale_order._envia_sync_service_id_from_quote(quote)
            # Regenerate path: unlink prior Envia fulfillment, then label/create.
            picking._envia_unlink_prior_fulfillments()
            # ponytail: Envia label/create XML-RPC-writes stock.picking during this
            # HTTP call. Holding the picking lock until the response makes Envia's
            # proxy time out at ~30s (HTTP 503 HTML). Ceiling: if label/create
            # fails after commit, shipping cost/unlink stay applied; retry Generate.
            if not self.env.context.get("envia_skip_dedicated_cursor"):
                self.env.cr.commit()
            adapter = get_envia_adapter(picking.company_id)
            envia_service_id = self._envia_resolve_label_service_id(
                quote, sale_order, picking
            )
            try:
                response = adapter.create_label_for_odoo_order(
                    sale_order.id, envia_service_id
                )
            except UserError as error:
                message = str(error.args[0] if error.args else error).lower()
                if "already fulfilled" not in message:
                    raise
                # Last fulfillment still linked — unlink again and retry once.
                _logger.warning(
                    "Envia label/create already fulfilled for %s; "
                    "retrying unlink + label/create",
                    picking.name,
                )
                if not picking._envia_unlink_prior_fulfillments():
                    raise UserError(
                        _(
                            "Envia already has a label for this order, but Odoo "
                            "has no Envia orderId/shipmentId to unlink it. "
                            "Unlink the label in Envia Shipping, then either "
                            "generate the new label there, or clear the tracking "
                            "on this delivery and generate again from Odoo."
                        )
                    ) from error
                if not self.env.context.get("envia_skip_dedicated_cursor"):
                    self.env.cr.commit()
                response = adapter.create_label_for_odoo_order(
                    sale_order.id, envia_service_id
                )
            # Persist orderId/shipmentId immediately (SO + Envios). Envia may have
            # already created the label; losing these IDs blocks Replace/unlink.
            self._envia_persist_label_side_effects(picking, quote, response)
            if not self.env.context.get("envia_skip_dedicated_cursor"):
                self.env.cr.commit()
            # Dedicated cursor still used for chatter/bookkeeping idempotency.
            self._envia_commit_label_side_effects(
                picking_id=picking.id,
                quote_id=quote.id,
                response=response,
            )
            picking._envia_ensure_chatter_label(
                label_url=response.label_url,
                tracking_number=response.tracking_number,
            )
            exact_price = quote.selected_service_id.price or 0.0
            if response.pricing_total not in (None, False):
                exact_price = response.pricing_total
            result.append(
                {
                    "exact_price": exact_price,
                    "tracking_number": response.tracking_number or "",
                }
            )
        return result

    @api.model
    def _envia_resolve_label_service_id(self, quote, sale_order, picking):
        """Numeric Envia ``service_id`` for label/create (required by the API)."""
        from odoo.addons.envia.services.envia_official_adapter import EnviaOfficialAdapter

        selected = quote.selected_service_id
        if not selected:
            selected = quote.service_ids.filtered("is_selected")[:1]
        candidates = [
            selected.envia_service_id if selected else False,
            sale_order.envia_service_id,
            picking.envia_service_id,
            selected.service_id if selected else False,
        ]
        for candidate in candidates:
            service_id = EnviaOfficialAdapter._label_create_service_id(candidate)
            if service_id:
                _logger.info(
                    "Envia label/create resolved service_id=%s (order=%s picking=%s)",
                    service_id,
                    sale_order.id,
                    picking.id,
                )
                return service_id
        _logger.warning(
            "Envia label/create missing service_id order=%s picking=%s "
            "selected=%s so=%s picking_field=%s",
            sale_order.id,
            picking.id,
            selected.envia_service_id if selected else None,
            sale_order.envia_service_id,
            picking.envia_service_id,
        )
        raise UserError(
            _(
                "Envia service_id is missing on the selected rate. "
                "Get a new quote, select a carrier, then try Generate Label again."
            )
        )

    def _envia_commit_label_side_effects(self, *, picking_id, quote_id, response):
        """Commit shipment + Envia orderId in a dedicated cursor."""
        if self.env.context.get("envia_skip_dedicated_cursor"):
            picking = self.env["stock.picking"].browse(picking_id)
            quote = self.env["envia.quote"].browse(quote_id)
            return self._envia_persist_label_side_effects(picking, quote, response)
        db_name = self.env.cr.dbname
        try:
            with Registry(db_name).cursor() as cr:
                env = api.Environment(cr, SUPERUSER_ID, dict(self.env.context))
                picking = env["stock.picking"].browse(picking_id)
                quote = env["envia.quote"].browse(quote_id)
                return env["delivery.carrier"]._envia_persist_label_side_effects(
                    picking, quote, response
                )
        except Exception:  # noqa: BLE001
            _logger.exception(
                "Envia dedicated label commit failed for picking %s; "
                "falling back to request transaction",
                picking_id,
            )
            picking = self.env["stock.picking"].browse(picking_id)
            quote = self.env["envia.quote"].browse(quote_id)
            return self._envia_persist_label_side_effects(picking, quote, response)

    @api.model
    def _envia_persist_label_side_effects(self, picking, quote, response):
        """Create envia.shipment; avoid writing stock.picking in this path.

        Envia ``label/create`` often XML-RPC-writes ``carrier_tracking_ref`` on the
        same picking while this transaction runs. Touching the picking here causes
        SERIALIZATION_FAILURE → full RPC retry → duplicate label/create.
        Store orderId/shipmentId on Envios (+ SO) so regenerate can DELETE.
        """
        Shipment = picking.env["envia.shipment"]
        external_id = (
            str(response.shipment_id)
            if response.shipment_id not in (None, False, "")
            else ""
        )
        tracking = (response.tracking_number or "").strip()
        sale = picking.sale_id
        shipment = picking.envia_shipment_ids.filtered(
            lambda item: item._is_active()
            and (
                (external_id and item.external_shipment_id == external_id)
                or (tracking and (item.tracking_number or "") == tracking)
            )
        )[:1]
        if not shipment and sale:
            shipment = sale.envia_shipment_ids.filtered(
                lambda item: item._is_active()
                and (
                    (external_id and item.external_shipment_id == external_id)
                    or (tracking and (item.tracking_number or "") == tracking)
                )
            )[:1]
        quote_ok = bool(quote and quote.exists())
        if not shipment and quote_ok:
            shipment = Shipment.with_context(
                envia_skip_picking_tracking=True,
                envia_skip_label_download=True,
            ).create_from_api_response(response, quote, picking=picking)
        elif not shipment:
            shipment = Shipment._create_from_label_response(response, picking=picking)
        else:
            vals = {}
            if response.order_id and not shipment.external_order_id:
                vals["external_order_id"] = str(response.order_id)
            if response.label_url and not shipment.label_url:
                vals["label_url"] = response.label_url
            if picking and not shipment.picking_id:
                vals["picking_id"] = picking.id
            if vals:
                shipment.write(vals)
        if sale and response.order_id:
            order_id = str(response.order_id)
            if sale.envia_external_order_id != order_id:
                sale.envia_external_order_id = order_id
        # Do not write stock.picking here: Envia label/create XML-RPC-updates the
        # same row; concurrent writes trigger SERIALIZATION_FAILURE retries.
        return shipment

    def _envia_recover_shipping_after_tracking(self, picking):
        """Build Envios row when the picking has tracking but no active shipment.

        Does not call label/create again (would mint another label).
        """
        Shipment = self.env["envia.shipment"]
        existing = picking.envia_shipment_ids.filtered(
            lambda item: item._is_active()
        )[:1]
        if existing:
            return existing
        return Shipment.create_bookkeeping_from_picking(
            picking, quote=picking._get_active_envia_quote()
        )

    def envia_get_tracking_link(self, picking):
        """Return public Envia tracking URL for the picking, or False."""
        tracking = (picking.carrier_tracking_ref or "").strip()
        if not tracking:
            return False
        # Multi-ref: Core may join with commas; open first label.
        first = tracking.split(",")[0].strip()
        if not first:
            return False
        return ENVIA_PUBLIC_TRACKING_URL.format(tracking=first)

    def envia_cancel_shipment(self, pickings):
        """Core Cancel dispatcher: unlink on Envia + clear local label fields."""
        pickings._envia_unlink_label()

    def envia_rate_shipment(self, order):
        """Return Odoo delivery rate dict for delivery_type=envia (rate-only)."""
        self.ensure_one()
        quote = order._get_active_envia_quote()
        if quote and quote.selected_service_id:
            price = order._envia_shipping_unit_price(quote)
            return {
                "success": True,
                "price": price,
                "error_message": False,
                "warning_message": False,
            }
        try:
            request = PayloadMapper.build_quote_request_from_sale_order(order)
            adapter = get_envia_adapter(order.company_id)
            response = adapter.quote(request)
            service = adapter.pick_cheapest_service(response.services)
            if not service:
                return {
                    "success": False,
                    "price": 0.0,
                    "error_message": _(
                        "No Envia rates available for this order."
                    ),
                    "warning_message": False,
                }
            price = service.price
            rate_currency = self.env["res.currency"].search(
                [("name", "=", service.currency)],
                limit=1,
            )
            if rate_currency and rate_currency != order.currency_id:
                price = rate_currency._convert(
                    price,
                    order.currency_id,
                    order.company_id,
                    fields.Date.context_today(self),
                )
            warning = False
            if len(response.services) > 1:
                warning = _(
                    "Cheapest Envia rate: %(carrier)s - %(service)s",
                    carrier=service.carrier_name,
                    service=service.service_name,
                )
            return {
                "success": True,
                "price": price,
                "error_message": False,
                "warning_message": warning,
            }
        except UserError as error:
            return {
                "success": False,
                "price": 0.0,
                # Keep translated Lazy string from UserError (not str()).
                "error_message": error.args[0] if error.args else error,
                "warning_message": False,
            }

    def _envia_get_close_locations(self, partner_address, **kwargs):
        """Pickup points for the native Website location selector (list + Leaflet)."""
        self.ensure_one()
        order = request.cart
        if not order:
            return []
        try:
            options = WebsitePickupService(self.env).list_pickup_options(order.sudo())
        except UserError:
            return []
        return [
            {
                "id": option["id"],
                "name": option.get("name") or option.get("branch_code"),
                "street": option.get("street") or option.get("address") or "",
                "city": option.get("city") or "",
                "zip_code": option.get("zip") or "",
                "state": option.get("state_code") or "",
                "country_code": option.get("country_code") or "",
                "latitude": option.get("lat") or 0.0,
                "longitude": option.get("lng") or 0.0,
                "additional_data": {"envia_option": option},
                "opening_hours": {},
            }
            for option in options
        ]
