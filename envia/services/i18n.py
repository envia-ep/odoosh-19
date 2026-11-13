"""Service-layer gettext that does not WARN when no env lang is on the stack.

Plain ``odoo._`` inspects the caller for ``self.env``; service helpers and
static methods have none, so Odoo 19 logs WARNING during tests/CI. LazyTranslate
with ``default_lang`` falls back at DEBUG instead.
"""

from odoo.tools.translate import LazyTranslate

_lt = LazyTranslate("envia", default_lang="en_US")


def _(source: str) -> str:
    return str(_lt(source))
