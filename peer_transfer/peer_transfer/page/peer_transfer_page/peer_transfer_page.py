"""Context for the "Peer Transfer" desk page.

Thin page only: all data flows through ``peer_transfer.api.transfer.*``
called directly from the client script. No server logic lives here.
"""

import frappe  # noqa: F401


def get_context(context):
	context.no_cache = 1
	context.title = "Peer Transfer"
	return context
