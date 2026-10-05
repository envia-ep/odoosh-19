from markupsafe import Markup
import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from ..services.envia_config import ENVIA_PUBLIC_TRACKING_URL

_logger = logging.getLogger(__name__)


class StockPicking(models.Model):
    _name = "stock.picking"
    _inherit = ["stock.picking", "envia.read.grouping.mixin"]

    envia_quote_ids = fields.One2many("envia.quote", "picking_id", string="Envia Quotes")
    envia_shipment_ids = fields.One2many("envia.shipment", "picking_id", string="Envia Shipments")
    envia_quote_count = fields.Integer(compute="_compute_envia_counts")
    envia_shipment_count = fields.Integer(compute="_compute_envia_counts")
    envia_show_quote_archive = fields.Boolean(related="company_id.envia_show_quote_archive")
    envia_label_url = fields.Char(string="Envia Label URL", copy=False)
    envia_status = fields.Selection(
        [
            ("none", "No Envia activity"),
            ("quoted", "Rate selected"),
            ("shipped", "Label created"),
        ],
        compute="_compute_envia_status",
        string="Envia Status",
    )
    envia_summary = fields.Char(compute="_compute_envia_status")
    envia_service_id = fields.Integer(string="Envia Service ID", copy=False)
    envia_can_generate_label = fields.Boolean(compute="_compute_envia_label_actions")
    envia_can_replace_label = fields.Boolean(compute="_compute_envia_label_actions")

    def _compute_envia_counts(self):
        for picking in self:
            picking.envia_quote_count = len(picking.envia_quote_ids)
            picking.envia_shipment_count = len(picking.envia_shipment_ids)

    def _envia_active_shipments(self):
        self.ensure_one()
        return self.envia_shipment_ids.filtered(lambda item: item._is_active())

    def _envia_has_active_label(self):
        self.ensure_one()
        return bool(
            self._envia_active_shipments()
            or (self.carrier_tracking_ref or "").strip()
        )

    @api.depends(
        "carrier_id.delivery_type",
        "carrier_tracking_ref",
        "envia_shipment_ids",
        "envia_shipment_ids.state",
        "state",
        "picking_type_code",
        "company_id.envia_enable_labels",
    )
    def _compute_envia_label_actions(self):
        for picking in self:
            is_envia = picking.carrier_id.delivery_type == "envia"
            labels_on = picking.company_id.envia_enable_labels
            outgoing = (
                picking.picking_type_code != "incoming"
                and picking.state not in ("draft", "cancel")
            )
            has_label = picking._envia_has_active_label() if is_envia else False
            picking.envia_can_generate_label = (
                labels_on and is_envia and outgoing and not has_label
            )
            picking.envia_can_replace_label = labels_on and is_envia and has_label

    def _get_active_envia_quote(self):
        self.ensure_one()
        forced = self.env.context.get("envia_force_quote_id")
        if forced:
            quote = self.env["envia.quote"].browse(forced)
            if quote.exists():
                return quote
        quotes = self.envia_quote_ids
        if self.sale_id:
            quotes |= self.sale_id.envia_quote_ids
        return quotes.filtered(lambda item: item._is_label_ready()).sorted(
            "id", reverse=True
        )[:1]

    def _compute_envia_status(self):
        for picking in self:
            shipment = picking._envia_active_shipments()[:1]
            quote = picking._get_active_envia_quote()
            tracking = (picking.carrier_tracking_ref or "").strip()
            is_envia = picking.carrier_id.delivery_type == "envia"
            if shipment or (is_envia and tracking):
                picking.envia_status = "shipped"
                picking.envia_summary = _(
                    "Envia label created: %(tracking)s · %(carrier)s",
                    tracking=(
                        (shipment.tracking_number if shipment else False)
                        or tracking
                        or (shipment.name if shipment else "")
                    ),
                    carrier=(
                        (shipment.carrier_name or shipment.carrier)
                        if shipment
                        else (picking.carrier_id.name or _("Carrier"))
                    ),
                )
            elif quote:
                service = quote.selected_service_id
                picking.envia_summary = _(
                    "Envia rate selected: %(carrier)s · %(service)s · %(price).2f %(currency)s",
                    carrier=service.carrier_name or service.carrier,
                    service=service.service_name,
                    price=service.price,
                    currency=service.currency_name or picking.company_id.currency_id.name,
                )
                picking.envia_status = "quoted"
            else:
                picking.envia_status = "none"
                picking.envia_summary = False

    def _envia_unlink_prior_fulfillments(self):
        """DELETE prior Envia fulfillments for this delivery (incl. replaced).

        Called immediately before ``label/create`` so regenerate is always
        unlink → create, even if Replace only cleared local fields.

        Includes sale-order shipments and uses ``sale.order.envia_external_order_id``
        when a row only has ``shipmentId`` (persist race / older rows).
        """
        self.ensure_one()
        Shipment = self.env["envia.shipment"]
        candidates = self.envia_shipment_ids
        sale = self.sale_id
        if sale:
            candidates |= sale.envia_shipment_ids
        so_order_id = (sale.envia_external_order_id or "").strip() if sale else ""
        candidates = candidates.filtered(
            lambda item: (item.external_shipment_id or "").strip()
            and (
                (item.external_order_id or "").strip()
                or so_order_id
            )
        )
        unlinked = False
        for shipment in candidates:
            if shipment._envia_delete_order_shipment():
                unlinked = True
        active = candidates.filtered(lambda item: item._is_active())
        if active:
            active._mark_replaced()
        return unlinked

    def _envia_unlink_label(self):
        """Unlink label on Envia (order-shipments DELETE), then clear local fields."""
        for picking in self:
            # Prefer Envia orderId rows; also mark any other active as replaced.
            picking._envia_unlink_prior_fulfillments()
            leftover = picking._envia_active_shipments()
            if leftover:
                leftover._mark_replaced()
            vals = {}
            if picking.carrier_tracking_ref:
                vals["carrier_tracking_ref"] = False
            if picking.envia_label_url:
                vals["envia_label_url"] = False
            if vals:
                picking.write(vals)

    def action_envia_generate_label(self):
        """Create Envia label from the delivery (manual; not on Validate).

        After Replace + quote: unlink prior fulfillment on Envia, then
        ``label/create`` via ``send_to_shipper`` / ``envia_send_shipping``.

        If a label is already linked (e.g. double-click after success), do nothing
        instead of raising — use Replace Envia Label to unlink and re-quote.
        """
        self.ensure_one()
        if not self.company_id.envia_enable_labels:
            raise UserError(
                _("Enable label generation in Settings > Envia.com before creating labels.")
            )
        if self.carrier_id.delivery_type != "envia":
            raise UserError(_("This transfer is not using Envia.com."))
        if self.picking_type_code == "incoming" or self.state in ("draft", "cancel"):
            raise UserError(
                _("Open an outgoing delivery that is ready or done to generate a label.")
            )
        if self._envia_has_active_label():
            shipment = self._envia_active_shipments()[:1]
            self._envia_ensure_chatter_label(
                label_url=shipment.label_url or self.envia_label_url,
                tracking_number=shipment.tracking_number
                or self.carrier_tracking_ref,
            )
            return True
        if not self._get_active_envia_quote():
            return self.action_open_envia_quote_wizard()
        return self.send_to_shipper()

    def _envia_has_unlink_targets(self):
        """True when Odoo can DELETE the Envia fulfillment (needs order + shipment id)."""
        self.ensure_one()
        sale = self.sale_id
        so_order_id = (sale.envia_external_order_id or "").strip() if sale else ""
        shipments = self.envia_shipment_ids
        if sale:
            shipments |= sale.envia_shipment_ids
        return bool(
            shipments.filtered(
                lambda item: (item.external_shipment_id or "").strip()
                and (
                    (item.external_order_id or "").strip()
                    or so_order_id
                )
            )
        )

    def action_envia_replace_label(self):
        """Clear local label link and reopen rates; Envia unlink runs on Generate."""
        self.ensure_one()
        if self.carrier_id.delivery_type != "envia":
            raise UserError(_("This transfer is not using Envia.com."))
        if not self._envia_has_active_label():
            raise UserError(_("No Envia label is linked to this delivery."))
        if not self._envia_has_unlink_targets():
            raise UserError(
                _(
                    "Cannot replace this Envia label from Odoo: the Envia "
                    "orderId/shipmentId was not saved (often after a timeout when "
                    "the label was already created on Envia). "
                    "Unlink the label in Envia Shipping, then either generate "
                    "the new label there, or clear the tracking on this delivery "
                    "and generate again from Odoo."
                )
            )
        self._envia_unlink_label()
        quotes = self.envia_quote_ids
        if self.sale_id:
            quotes |= self.sale_id.envia_quote_ids
        quotes.filtered(lambda item: item.state == "quoted").write({"state": "used"})
        return self.action_open_envia_quote_wizard()

    def action_open_envia_quote_wizard(self):
        self.ensure_one()
        sale_order = self.sale_id
        return {
            "type": "ir.actions.act_window",
            "name": _("Ship with Envia"),
            "res_model": "envia.quote.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_picking_id": self.id,
                "default_sale_order_id": sale_order.id if sale_order else False,
                "default_destination_partner_id": self.partner_id.id,
                "dialog_size": "extra-large",
            },
        }

    def action_view_envia_quotes(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Envia Quotes"),
            "res_model": "envia.quote",
            "view_mode": "list,form",
            "domain": [("picking_id", "=", self.id)],
        }

    def action_view_envia_shipments(self):
        self.ensure_one()
        # Tracking on the DO without an Envios row (persist race) → bookkeep now.
        if (
            self.carrier_id.delivery_type == "envia"
            and (self.carrier_tracking_ref or "").strip()
            and not self._envia_active_shipments()
        ):
            self.carrier_id._envia_recover_shipping_after_tracking(self)
        shipment = self._envia_active_shipments()[:1]
        if shipment:
            self._envia_ensure_chatter_label(
                label_url=shipment.label_url or self.envia_label_url,
                tracking_number=shipment.tracking_number
                or self.carrier_tracking_ref,
            )
        return {
            "type": "ir.actions.act_window",
            "name": _("Envia Shipments"),
            "res_model": "envia.shipment",
            "view_mode": "list,form",
            "domain": [("picking_id", "=", self.id)],
        }

    def _envia_ensure_chatter_label(self, *, label_url=None, tracking_number=None):
        """Post the Envia PDF/tracking link if chatter does not already have it."""
        self.ensure_one()
        self.invalidate_recordset(["message_ids", "envia_label_url"])
        url = (label_url or self.envia_label_url or "").strip()
        tracking = tracking_number or self.carrier_tracking_ref
        if self._envia_has_label_url_message(label_url=url):
            return False
        try:
            return self._envia_post_label_url(
                label_url=url or False,
                tracking_number=tracking,
            )
        except Exception as error:  # noqa: BLE001
            _logger.warning(
                "Envia label chatter post failed for %s: %s",
                self.name,
                error,
            )
            return False

    def _envia_post_label_url(self, *, label_url, tracking_number=None):
        """Post Envia label link in the delivery chatter (no local PDF copy)."""
        self.ensure_one()
        tracking = tracking_number or self.carrier_tracking_ref or self.name
        tracking_url = ENVIA_PUBLIC_TRACKING_URL.format(tracking=tracking)
        label_url = (label_url or "").strip()
        if not label_url and not tracking:
            return False
        # Markup: otherwise chatter shows raw <br/> / <a> as escaped text.
        # Keep the PDF on Envia/S3; only show a short clickable label (no attachment).
        if label_url:
            body = Markup(
                _(
                    "Shipment created into Envia<br/>"
                    'Tracking Number: <a href="%(tracking_url)s" target="_blank">'
                    "%(tracking)s</a><br/>"
                    '<a href="%(url)s" target="_blank">Open shipping label (PDF)</a>'
                )
            ) % {
                "tracking": tracking,
                "tracking_url": tracking_url,
                "url": label_url,
            }
        else:
            body = Markup(
                _(
                    "Shipment created into Envia<br/>"
                    'Tracking Number: <a href="%(tracking_url)s" target="_blank">'
                    "%(tracking)s</a>"
                )
            ) % {
                "tracking": tracking,
                "tracking_url": tracking_url,
            }
        self.message_post(body=body)
        if label_url and not self.envia_label_url:
            self.envia_label_url = label_url
        return True

    def _envia_has_label_url_message(self, label_url=None):
        """True if chatter already contains an Envia label URL or tracking link."""
        self.ensure_one()
        needles = [
            (label_url or self.envia_label_url or "").strip(),
            "envia.com/rastreo",
            "Open shipping label",
            "Abrir etiqueta de envío",
            "Abrir guía de envío",
        ]
        for message in self.message_ids:
            body = message.body or ""
            if any(needle and needle in body for needle in needles):
                return True
        return False
