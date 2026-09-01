# Part of Odoo. See LICENSE file for full copyright and licensing details.

from datetime import timedelta

from odoo import api, fields, models
from odoo.modules import module as odoo_module
from odoo.tools.urls import urljoin as url_join

from odoo.addons.payment import utils as payment_utils
from odoo.addons.payment.const import REPORT_REASONS_MAPPING
from odoo.addons.payment.logging import get_payment_logger
from odoo.addons.payment_ecartpay import const


_logger = get_payment_logger(__name__)

# Endpoint used to exchange the API keys for a short-lived bearer token.
_TOKEN_ENDPOINT = 'authorizations/token'
# Facade endpoint used to fetch (and provision) the account-level webhook signing secret.
_WEBHOOK_CREDENTIALS_ENDPOINT = 'odoo/webhook-secret'


class PaymentProvider(models.Model):
    _inherit = 'payment.provider'

    code = fields.Selection(
        selection_add=[('ecartpay', "Ecart Pay")], ondelete={'ecartpay': 'set default'}
    )
    ecartpay_public_key = fields.Char(
        string="Ecart Pay Public Key",
        help="The public API key found in your Ecart Pay account settings.",
        required_if_provider='ecartpay',
        copy=False,
    )
    ecartpay_private_key = fields.Char(
        string="Ecart Pay Private Key",
        help="The private API key found in your Ecart Pay account settings.",
        required_if_provider='ecartpay',
        copy=False,
        groups='base.group_system',
    )
    ecartpay_webhook_secret = fields.Char(
        string="Ecart Pay Webhook Secret",
        help="The global secret used to verify the signature of incoming webhooks. It is fetched "
             "automatically from Ecart Pay using the API keys; no manual input is required.",
        readonly=True,
        copy=False,
        groups='base.group_system',
    )
    # Cached short-lived bearer token (valid for 1 hour on Ecart Pay's side).
    ecartpay_auth_token = fields.Char(
        string="Ecart Pay Auth Token", copy=False, readonly=True, groups='base.group_system'
    )
    ecartpay_token_expiry = fields.Datetime(
        string="Ecart Pay Auth Token Expiry", copy=False, readonly=True, groups='base.group_system'
    )

    # === COMPUTE METHODS === #

    def _get_supported_currencies(self):
        """ Override of `payment` to return the supported currencies. """
        supported_currencies = super()._get_supported_currencies()
        if self.code == 'ecartpay':
            supported_currencies = supported_currencies.filtered(
                lambda c: c.name in const.SUPPORTED_CURRENCIES
            )
        return supported_currencies

    # === CRUD METHODS === #

    @api.model_create_multi
    def create(self, vals_list):
        """ Override of `payment` to fetch the Ecart Pay webhook secret on creation. """
        providers = super().create(vals_list)
        providers._ecartpay_sync_webhook_secret()
        providers._ecartpay_ensure_payment_method_line()
        return providers

    def write(self, vals):
        """ Override of `payment` to keep Ecart Pay state consistent when the config changes.

        - Invalidates the cached bearer token when the API keys or the activation state change, so
          the next request re-authenticates against the correct environment (test/sandbox vs
          production) with the current keys instead of reusing a token minted with the old ones.
        - Refreshes the webhook secret automatically (no manual copy needed).
        - Ensures the provider's payment method line so online/POS payments can create their
          `account.payment` without manual accounting setup.
        """
        # Reset the cached token in the same write when the environment or credentials change.
        if {'ecartpay_public_key', 'ecartpay_private_key', 'state'} & vals.keys():
            vals = {**vals, 'ecartpay_auth_token': False, 'ecartpay_token_expiry': False}
        res = super().write(vals)
        if not self.env.context.get('ecartpay_skip_webhook_secret_sync') and (
            {'ecartpay_public_key', 'ecartpay_private_key', 'state', 'code'} & vals.keys()
        ):
            self._ecartpay_sync_webhook_secret()
        if {'state', 'journal_id', 'code'} & vals.keys():
            self._ecartpay_ensure_payment_method_line()
        return res

    def _ecartpay_ensure_payment_method_line(self):
        """ Ensure each enabled Ecart Pay provider has its payment method line in its journal.

        Online and POS payments create an `account.payment` that requires a payment method line
        linked to the provider on its journal; without it Odoo raises "Please define a payment
        method line on your payment". Odoo normally creates it when the journal is assigned, but we
        enforce it here as a safety net. Guarded so the module keeps working with only the
        `payment` dependency (the helper is provided by `account_payment`).

        :return: None
        """
        for provider in self.filtered(lambda p: p.code == 'ecartpay' and p.state != 'disabled'):
            if provider.journal_id and hasattr(provider, '_ensure_payment_method_line'):
                provider._ensure_payment_method_line()

    def _get_default_payment_method_codes(self):
        """ Override of `payment` to return the default payment method codes. """
        self.ensure_one()
        if self.code != 'ecartpay':
            return super()._get_default_payment_method_codes()
        return const.DEFAULT_PAYMENT_METHOD_CODES

    def _get_removal_values(self):
        """ Override of `payment` to clear the cached token when the module is uninstalled. """
        res = super()._get_removal_values()
        res.update({'ecartpay_auth_token': False, 'ecartpay_token_expiry': False})
        return res

    # === BUSINESS METHODS === #

    @api.model
    def _get_compatible_providers(self, *args, is_validation=False, report=None, **kwargs):
        """ Override of `payment` to filter out Ecart Pay providers for validation operations. """
        providers = super()._get_compatible_providers(
            *args, is_validation=is_validation, report=report, **kwargs
        )

        if is_validation:
            unfiltered_providers = providers
            providers = providers.filtered(lambda p: p.code != 'ecartpay')
            payment_utils.add_to_report(
                report,
                unfiltered_providers - providers,
                available=False,
                reason=REPORT_REASONS_MAPPING['validation_not_supported'],
            )

        return providers

    # === AUTHENTICATION === #

    def _ecartpay_get_bearer_token(self):
        """ Return a valid Ecart Pay bearer token, refreshing it when needed.

        Ecart Pay tokens are obtained by exchanging the public/private keys (sent as HTTP Basic
        auth) and are valid for one hour. The token is cached on the provider record and refreshed
        a few minutes before expiry.

        :return: The bearer token.
        :rtype: str
        """
        self.ensure_one()
        now = fields.Datetime.now()
        if (
            self.ecartpay_auth_token
            and self.ecartpay_token_expiry
            and self.ecartpay_token_expiry > now + timedelta(minutes=1)
        ):
            return self.ecartpay_auth_token

        response = self._send_api_request(
            'POST', _TOKEN_ENDPOINT, ecartpay_token_request=True
        )
        token = response['token']
        # Refresh a few minutes before the documented 1-hour validity to be safe.
        self.sudo().write({
            'ecartpay_auth_token': token,
            'ecartpay_token_expiry': now + timedelta(minutes=55),
        })
        return token

    def _ecartpay_sync_webhook_secret(self):
        """ Fetch and store the account-level webhook signing secret from Ecart Pay.

        Ecart Pay signs every outgoing webhook with the account's global webhook secret, exposed at
        `GET /api/authorizations/credentials/webhooks`. Fetching it here (using the API keys the
        merchant already configured) removes the need to copy the secret manually.

        :return: None
        """
        # Avoid outbound requests while running tests.
        if odoo_module.current_test:
            return
        for provider in self.filtered(lambda p: p.code == 'ecartpay'):
            provider_sudo = provider.sudo()
            if provider.state == 'disabled':
                continue
            if not provider_sudo.ecartpay_public_key or not provider_sudo.ecartpay_private_key:
                continue
            try:
                response = provider_sudo._send_api_request('GET', _WEBHOOK_CREDENTIALS_ENDPOINT)
            except Exception:  # noqa: BLE001 - never block provider setup if the fetch fails.
                _logger.exception("Unable to fetch the Ecart Pay webhook secret.")
                continue
            secret = (response or {}).get('secret')
            if secret and secret != provider_sudo.ecartpay_webhook_secret:
                provider_sudo.with_context(ecartpay_skip_webhook_secret_sync=True).write(
                    {'ecartpay_webhook_secret': secret}
                )

    # === REQUEST HELPERS === #

    def _build_request_url(self, endpoint, **kwargs):
        """ Override of `payment` to build the request URL. """
        if self.code != 'ecartpay':
            return super()._build_request_url(endpoint, **kwargs)
        api_url = const.API_URLS['enabled' if self.state == 'enabled' else 'test']
        return url_join(api_url, endpoint)

    def _build_request_headers(self, method, endpoint, payload, **kwargs):
        """ Override of `payment` to build the request headers. """
        if self.code != 'ecartpay':
            return super()._build_request_headers(method, endpoint, payload, **kwargs)
        # The token endpoint authenticates with HTTP Basic auth (see `_build_request_auth`).
        if kwargs.get('ecartpay_token_request'):
            return {'Accept': 'application/json'}
        # All other endpoints authenticate with the bearer token in the Authorization header.
        return {
            'Authorization': self._ecartpay_get_bearer_token(),
            'Content-Type': 'application/json',
        }

    def _build_request_auth(self, **kwargs):
        """ Override of `payment` to set HTTP Basic auth for the token exchange. """
        if self.code == 'ecartpay' and kwargs.get('ecartpay_token_request'):
            return self.ecartpay_public_key, self.ecartpay_private_key
        return super()._build_request_auth(**kwargs)

    def _parse_response_error(self, response):
        """ Override of `payment` to parse the error message. """
        if self.code != 'ecartpay':
            return super()._parse_response_error(response)
        try:
            return response.json().get('message', response.text)
        except ValueError:
            return response.text

    def _parse_response_content(self, response, **kwargs):
        """ Override of `payment` to parse the response content. """
        if self.code != 'ecartpay':
            return super()._parse_response_content(response, **kwargs)
        return response.json()
