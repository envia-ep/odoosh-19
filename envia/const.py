import base64
from urllib.parse import urlencode

ENVIA_LOCATION_TYPE_SELECTION = [
    ("address", "Domicile"),
    ("branch", "Branch"),
]

ENVIA_ECOMMERCE_EMBED_URL_PRODUCTION = "https://shipping.envia.com/ecommerce"
ENVIA_ECOMMERCE_EMBED_URL_SANDBOX = "https://shipping-test.envia.com/ecommerce"
# Back-compat alias (production default).
ENVIA_ECOMMERCE_EMBED_URL = ENVIA_ECOMMERCE_EMBED_URL_PRODUCTION

ENVIA_CHECKOUT_SETTINGS_FRAGMENT = "settings-checkout-automatic-quote"
ENVIA_SHIPPING_RULES_SETTINGS_FRAGMENT = "settings-shipping-rules"


def get_envia_ecommerce_embed_base_url(environment: str | None = None) -> str:
    """Ecommerce iframe host: sandbox/dev → shipping-test, else shipping prod."""
    from .services.envia_config import resolve_envia_environment

    env = (environment or resolve_envia_environment()).strip().lower()
    if env in ("sandbox", "test", "dev"):
        return ENVIA_ECOMMERCE_EMBED_URL_SANDBOX
    return ENVIA_ECOMMERCE_EMBED_URL_PRODUCTION


def get_envia_shipping_app_base_url(environment: str | None = None) -> str:
    """Shipping web app host (settings UI), derived from the ecommerce embed host."""
    return get_envia_ecommerce_embed_base_url(environment).removesuffix("/ecommerce")


def get_envia_shop_settings_url(
    shop_id: str | None,
    *,
    fragment: str,
    environment: str | None = None,
) -> str:
    """Deep-link into Envia shop integration settings (``#fragment``)."""
    shop = str(shop_id or "").strip()
    if not shop:
        raise ValueError("shop_id is required")
    section = (fragment or "").strip().lstrip("#")
    if not section:
        raise ValueError("fragment is required")
    base = get_envia_shipping_app_base_url(environment).rstrip("/")
    return f"{base}/settings/integrations/ecommerce/{shop}#{section}"


def get_envia_checkout_settings_url(
    shop_id: str | None,
    *,
    environment: str | None = None,
) -> str:
    """Deep-link to enable Automatic Quoting / Checkout for this shop."""
    return get_envia_shop_settings_url(
        shop_id,
        fragment=ENVIA_CHECKOUT_SETTINGS_FRAGMENT,
        environment=environment,
    )


def get_envia_shipping_rules_settings_url(
    shop_id: str | None,
    *,
    environment: str | None = None,
) -> str:
    """Deep-link to enable label generation / shipping rules for this shop."""
    return get_envia_shop_settings_url(
        shop_id,
        fragment=ENVIA_SHIPPING_RULES_SETTINGS_FRAGMENT,
        environment=environment,
    )


def build_envia_ecommerce_embed_hash(store_url: str, company: str, shop: str) -> str:
    """WooCommerce contract: base64(store_url:company:shop)."""
    store = (store_url or "").strip()
    company_id = str(company or "").strip()
    shop_id = str(shop or "").strip()
    if not store or not company_id or not shop_id:
        raise ValueError("store_url, company and shop are required")
    return base64.b64encode(f"{store}:{company_id}:{shop_id}".encode("utf-8")).decode("ascii")


def get_envia_dashboard_embed_url(
    store_url: str | None = None,
    company: str | None = None,
    shop: str | None = None,
    *,
    environment: str | None = None,
) -> str:
    """Iframe src for Envia Ecommerce Pro (host follows ENVIA_ENVIRONMENT)."""
    base = get_envia_ecommerce_embed_base_url(environment).rstrip("/")
    try:
        digest = build_envia_ecommerce_embed_hash(store_url or "", company or "", shop or "")
    except ValueError:
        return base
    return f"{base}?{urlencode({'hash': digest})}"
