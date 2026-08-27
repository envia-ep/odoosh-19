# Envia HTTP Bridge

Server-wide Odoo module that exposes Envia integration endpoints **before** a database is selected. Install it and add it to `server_wide_modules` so Envia.com can reach Odoo during OAuth setup and callbacks.

## Installation

1. Put `envia` and `envia_http` on the Odoo addons path (same package).
2. Add this module to `server_wide_modules` in `odoo.conf`:

```ini
server_wide_modules = web,base,envia_http
```

3. Restart Odoo.
4. In Apps, install **Envia.com** (`envia`) only — Odoo installs `envia_http` and other dependencies automatically.

The main `envia` module depends on `envia_http`, but the HTTP bridge must still be loaded server-wide for nodb routes to work. You do **not** need a separate Apps install for `envia_http`.

## Routes

| Route | Purpose |
| --- | --- |
| `POST /envia/integration/callback` | Receives the Envia.com integration result after OAuth |
| `POST /envia/integration/connect` | Starts the Envia.com plugin connection handshake |
| `POST /jsonrpc` | JSON-RPC proxy used by Envia.com to validate the Odoo store |
| `POST /xmlrpc/2/common` | XML-RPC common proxy for store validation |
| `POST /xmlrpc/2/object` | XML-RPC object proxy for integration calls without a selected database |

Each route delegates to the `envia` module when it is installed. If `envia` is missing from the addons path, the bridge returns HTTP 503.

## Verify

After restart, a missing bridge is logged by the `envia` module. You can also probe the callback route:

```bash
curl -X POST https://<your-domain>/envia/integration/callback
```

Expect a JSON error response (not 404) when the bridge is loaded.
