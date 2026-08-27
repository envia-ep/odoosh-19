import logging

from . import controllers

_logger = logging.getLogger(__name__)


def post_load():
    _logger.info(
        "Envia HTTP bridge active: POST /envia/integration/callback, /envia/integration/connect,"
        " /jsonrpc, /xmlrpc/2/common, /xmlrpc/2/object"
    )
