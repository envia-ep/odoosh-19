from __future__ import annotations

from datetime import timedelta

from odoo import fields
from odoo.exceptions import UserError

from .i18n import _

DEFAULT_ENVIA_API_KEY_NAME = "Envia.com"
LEGACY_ENVIA_API_KEY_NAMES = ("conectar con envia.com",)
PENDING_SETUP_PARAM = "envia.pending_plugin_setup_company_id"
ENVIA_MODULE_NAME = "envia"
# ponytail: in-process map api_key -> env.cr.dbname from Connect; survives until Odoo restart
_integration_database_by_api_key: dict[str, str] = {}
# ponytail: nodb /jsonrpc validation before the key row is committed (tests + Connect flow)
_integration_api_key_users: dict[str, tuple[int, str]] = {}


def normalize_integration_store_url(base_url: str) -> str:
    """Prefer HTTPS for dev tunnels when web.base.url was saved as http."""
    base_url = (base_url or "").strip().rstrip("/")
    if base_url.startswith("http://") and (
        ".trycloudflare.com" in base_url or ".ngrok" in base_url
    ):
        return f"https://{base_url[7:]}"
    return base_url


def bind_integration_database(env, api_key: str, user=None) -> None:
    """Remember the database active when the user clicked Connect with Envia.com."""
    api_key = (api_key or "").strip()
    if not api_key:
        return
    user = user or env.user
    _integration_database_by_api_key[api_key] = env.cr.dbname
    _integration_api_key_users[api_key] = (user.id, user.login or "")
    env["ir.config_parameter"].sudo().set_param(
        f"envia.integration_db.{api_key}",
        env.cr.dbname,
    )


def lookup_integration_database(api_key: str) -> str | None:
    api_key = (api_key or "").strip()
    if not api_key:
        return None
    database = _integration_database_by_api_key.get(api_key)
    if database:
        return database

    import odoo
    import odoo.http as http
    import odoo.service.db as db_service
    from odoo import api
    from odoo.modules.registry import Registry

    param_name = f"envia.integration_db.{api_key}"
    for db_name in http.db_filter(db_service.list_dbs(force=True)):
        try:
            registry = Registry(db_name)
            with registry.cursor() as cr:
                env = api.Environment(cr, odoo.SUPERUSER_ID, {})
                stored_db = env["ir.config_parameter"].sudo().get_param(param_name)
                if stored_db:
                    _integration_database_by_api_key[api_key] = stored_db
                    return stored_db
        except Exception:
            continue
    return None


def get_envia_module_version(env) -> str:
    module = env["ir.module.module"].sudo().search(
        [("name", "=", ENVIA_MODULE_NAME)],
        limit=1,
    )
    if module.latest_version:
        return module.latest_version
    return "0.0.0"


def normalize_envia_plugin_version(version: str | bool | None) -> str | False:
    if version in (None, False, ""):
        return False
    normalized = str(version).strip()
    if not normalized or normalized.upper() == "N/A":
        return False
    return normalized


def _revoke_existing_envia_api_keys(env, user, key_name=DEFAULT_ENVIA_API_KEY_NAME) -> None:
    key_names = {key_name, *LEGACY_ENVIA_API_KEY_NAMES}
    existing_keys = env["res.users.apikeys"].sudo().search(
        [
            ("user_id", "=", user.id),
            ("name", "in", list(key_names)),
        ]
    )
    if existing_keys:
        existing_keys.with_user(user).sudo()._remove()


def generate_integration_credentials(
    env,
    company,
    user=None,
    key_name=DEFAULT_ENVIA_API_KEY_NAME,
    expiration_days=None,
):
    user = user or env.user
    if not user._is_internal():
        raise UserError(_("Only internal Odoo users can generate an API key for Envia integration."))

    _revoke_existing_envia_api_keys(env, user, key_name=key_name)

    expiration_date = None
    if expiration_days:
        expiration_date = fields.Datetime.now() + timedelta(days=expiration_days)

    api_key = (
        env["res.users.apikeys"]
        .with_user(user)
        .sudo()
        ._generate(
            scope="rpc",
            name=key_name,
            expiration_date=expiration_date,
        )
    )
    bind_integration_database(env, api_key, user=user)
    base_url = normalize_integration_store_url(
        env["ir.config_parameter"].sudo().get_param("web.base.url", "")
    )
    return {
        "company_id": company.id,
        "store_url": base_url,
        "database_name": env.cr.dbname,
        "user_email": user.login,
        "api_key": api_key,
        "user_id": user.id,
    }


def queue_pending_setup(env, company=None):
    company = company or env.ref("base.main_company")
    env["ir.config_parameter"].sudo().set_param(PENDING_SETUP_PARAM, str(company.id))


def get_pending_setup_company_id(env):
    raw_value = env["ir.config_parameter"].sudo().get_param(PENDING_SETUP_PARAM)
    if not raw_value:
        return None
    try:
        return int(raw_value)
    except ValueError:
        return None


def clear_pending_setup(env):
    env["ir.config_parameter"].sudo().set_param(PENDING_SETUP_PARAM, "")


def pop_pending_setup_company_id(env):
    company_id = get_pending_setup_company_id(env)
    if company_id is None:
        return None
    clear_pending_setup(env)
    return company_id


def resolve_integration_api_key_user(
    database_name: str,
    api_key: str,
    email: str | None = None,
) -> int | None:
    """Validate API key from the in-process Connect cache (nodb / pre-commit)."""
    api_key = (api_key or "").strip()
    database_name = (database_name or "").strip()
    cached = _integration_api_key_users.get(api_key)
    if not cached or _integration_database_by_api_key.get(api_key) != database_name:
        return None
    user_id, login = cached
    if email and login != str(email).strip():
        return None
    return user_id
