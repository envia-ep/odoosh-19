# Part of Odoo. See LICENSE file for full copyright and licensing details.

# The base URLs of the Ecart Pay API, indexed by provider state.
# See https://docs.ecartpay.com/docs/checkout and https://docs.ecartpay.com/reference/create-order
API_URLS = {
    'enabled': 'https://ecartpay.com/api',
    'test': 'https://sandbox.ecartpay.com/api',
}

# The currencies supported by Ecart Pay, in ISO 4217 format.
# See the countries/currencies table at https://docs.ecartpay.com.
SUPPORTED_CURRENCIES = [
    'USD',
    'INR',
    'MXN',
    'BRL',
    'COP',
    'ARS',
    'GTQ',
    'CLP',
    'PEN',
    'CAD',
    'EUR',
    'AUD',
]

# Mapping of transaction states to Ecart Pay order statuses.
# See https://docs.ecartpay.com/docs/webhook-events (orders module events).
PAYMENT_STATUS_MAPPING = {
    'pending': ['created', 'pending', 'processing'],
    'done': ['paid', 'confirmed', 'completed'],
    'cancel': ['cancelled', 'canceled', 'expired'],
    'error': ['failed', 'declined', 'error'],
}

# The codes of the payment methods to activate when Ecart Pay is activated.
DEFAULT_PAYMENT_METHOD_CODES = {
    # Primary payment methods.
    'card',
    # Brand payment methods.
    'visa',
    'mastercard',
    'amex',
}

# The Ecart Pay webhook events that carry a meaningful order status update.
# `orders.create` is intentionally excluded: it only signals that the order was created (status
# "created") and would set the transaction to pending prematurely. The real status (pending/paid/
# cancelled/...) always arrives with `orders.confirmation` (and later changes with `orders.update`).
ORDER_WEBHOOK_EVENTS = [
    'orders.confirmation',
    'orders.update',
]
