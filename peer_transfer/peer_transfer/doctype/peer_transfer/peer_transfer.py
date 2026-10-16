"""Controller for Peer Transfer.

Header record for one peer-transfer run. All transfer logic lives in
``peer_transfer.api`` (owned by another worker); no validation here by
design -- the class must exist so ``get_controller()`` can import the
doctype.
"""

import frappe  # noqa: F401  (kept for parity with app controllers)
from frappe.model.document import Document


class PeerTransfer(Document):
    pass
