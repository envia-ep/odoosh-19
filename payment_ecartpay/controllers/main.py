# Part of Odoo. See LICENSE file for full copyright and licensing details.

import hashlib
import hmac
import json
import pprint

from werkzeug.exceptions import Forbidden

from odoo import http
from odoo.exceptions import ValidationError
from odoo.http import request

from odoo.addons.payment.controllers.post_processing import PaymentPostProcessing
from odoo.addons.payment.logging import get_payment_logger
from odoo.addons.payment_ecartpay import const


_logger = get_payment_logger(__name__)


class EcartPayController(http.Controller):
    _return_url = '/payment/ecartpay/return'
    _webhook_url = '/payment/ecartpay/webhook'

    @http.route(_return_url, type='http', methods=['GET'], auth='public')
    def ecartpay_return_from_checkout(self, **data):
        """ Handle the customer's redirection from the Ecart Pay payment window.

        The transaction is confirmed through the webhook, but this endpoint also fetches the order
        status from the API as a fallback (useful when the webhook is unreachable, e.g. in local
        development) before sending the customer back to the flow-specific landing page.

        :param dict data: The query parameters appended by Ecart Pay, if any.
        """
        _logger.info("Handling redirection from Ecart Pay with data:\n%s", pprint.pformat(data))

        tx_id = request.session.get(PaymentPostProcessing.MONITORED_TX_ID_KEY)
        tx_sudo = request.env['payment.transaction'].sudo().browse(tx_id).exists()
        if tx_sudo and tx_sudo.provider_code == 'ecartpay' and tx_sudo.provider_reference:
            try:
                order_data = tx_sudo._send_api_request(
                    'GET', f'odoo/orders/{tx_sudo.provider_reference}'
                )
            except ValidationError:
                _logger.exception("Unable to fetch the Ecart Pay order status on return.")
            else:
                tx_sudo._process('ecartpay', order_data)
        # Respect the transaction's landing route (e.g. the POS online payment confirmation page)
        # and fall back to the generic payment status page for the eCommerce/website flow.
        landing_route = tx_sudo.landing_route if tx_sudo else None
        return request.redirect(landing_route or '/payment/status')

    @http.route(_webhook_url, type='http', methods=['POST'], auth='public', csrf=False)
    def ecartpay_webhook(self):
        """ Process the order status update sent by Ecart Pay to the webhook.

        :return: An empty string to acknowledge the notification.
        :rtype: str
        """
        body = request.get_json_data()
        _logger.info("Notification received from Ecart Pay with data:\n%s", pprint.pformat(body))

        event = body.get('event')
        payment_data = body.get('data', body)
        if event and event not in const.ORDER_WEBHOOK_EVENTS:
            # Ignore events unrelated to order status updates.
            return request.make_json_response('')

        tx_sudo = request.env['payment.transaction'].sudo()._search_by_reference(
            'ecartpay', payment_data
        )
        if tx_sudo:
            self._verify_signature(request.httprequest, payment_data, tx_sudo)
            tx_sudo._process('ecartpay', payment_data)
        return request.make_json_response('')

    @staticmethod
    def _verify_signature(httprequest, payment_data, tx_sudo):
        """ Check that the webhook signature matches the expected one.

        The signature is computed as an HMAC-SHA256 of `{timestamp}.{webhook_id}.{data}` where
        `data` is the compact JSON of the event data, prefixed with `SHA256=`.
        See https://docs.ecartpay.com/docs/webhook-authentication.

        :param httprequest: The Werkzeug request holding the signature headers.
        :param dict payment_data: The event data used to compute the signature.
        :param payment.transaction tx_sudo: The sudoed transaction referenced by the payment data.
        :return: None
        :raise Forbidden: If the signature is missing or does not match.
        """
        timestamp = httprequest.headers.get('x-pay-timestamp')
        received_signature = httprequest.headers.get('x-pay-signature')
        webhook_id = httprequest.headers.get('x-pay-webhook-id')
        if not timestamp or not received_signature or not webhook_id:
            _logger.warning("Received webhook notification with missing signature headers.")
            raise Forbidden()

        secret = tx_sudo.provider_id.ecartpay_webhook_secret
        if not secret:
            _logger.warning("Ecart Pay webhook secret is not configured on the provider.")
            raise Forbidden()

        # Ecart Pay signs `{timestamp}.{webhook_id}.{data}` where `data` is the compact JSON of the
        # event data as produced by JavaScript's `JSON.stringify` (no spaces, non-ASCII kept as-is).
        # `ensure_ascii=False` and `separators=(",", ":")` reproduce that exact byte string.
        serialized_data = json.dumps(payment_data, separators=(',', ':'), ensure_ascii=False)
        signed_payload = f'{timestamp}.{webhook_id}.{serialized_data}'
        expected_signature = hmac.new(
            secret.encode(), signed_payload.encode(), hashlib.sha256
        ).hexdigest()

        # Ecart Pay prefixes the signature with `SHA256=`; ignore it when comparing.
        received_digest = received_signature.split('=', 1)[-1]
        if not hmac.compare_digest(received_digest, expected_signature):
            _logger.warning(
                "Ecart Pay webhook signature mismatch.\n"
                "  signed_payload=%s\n  expected=%s\n  received=%s",
                signed_payload, expected_signature, received_digest,
            )
            raise Forbidden()
