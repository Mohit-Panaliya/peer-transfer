"""Controller for Peer Transfer Log.

Append-only audit rows for a Peer Transfer. Written by
``peer_transfer.api`` (owned by another worker); no validation here by
design -- the class must exist so ``get_controller()`` can import the
doctype.
"""

import frappe  # noqa: F401  (kept for parity with app controllers)
from frappe.model.document import Document


class PeerTransferLog(Document):
    pass
