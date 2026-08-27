from odoo import _
from odoo.exceptions import UserError, ValidationError
from odoo.http import request, route

from odoo.addons.website_sale.controllers.delivery import Delivery

from ..services.website_pickup import ROUTE_SHIP, WebsitePickupService


class WebsiteSaleEnviaDelivery(Delivery):
    @route(
        "/shop/envia/delivery/options",
        type="jsonrpc",
        auth="public",
        website=True,
        methods=["POST"],
    )
    def shop_envia_delivery_options(self, route_type=ROUTE_SHIP, **kwargs):
        order = request.cart
        if not order:
            raise ValidationError(_("Your cart is empty."))
        service = WebsitePickupService(request.env)
        try:
            options = service.list_options(order.sudo(), route_type)
        except UserError as error:
            return {"error": error.args[0] if error.args else str(error), "options": []}
        return {"options": options}

    @route(
        "/shop/envia/delivery/select",
        type="jsonrpc",
        auth="public",
        website=True,
        methods=["POST"],
    )
    def shop_envia_delivery_select(self, **payload):
        order = request.cart
        if not order:
            raise ValidationError(_("Your cart is empty."))
        order_sudo = order.sudo()
        # Do not call _set_delivery_method here: it re-rates cheapest and
        # would overwrite the customer selection until sync runs.
        service = WebsitePickupService(request.env)
        try:
            result = service.apply_selection(order_sudo, payload)
        except UserError as error:
            return {
                "success": False,
                "error": error.args[0] if error.args else str(error),
            }
        summary = self._order_summary_values(order_sudo)
        summary.update(result)
        summary["success"] = True
        return summary
