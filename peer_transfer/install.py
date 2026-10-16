"""Post-install / post-migrate hook (stub, install-safe).

Wired from ``hooks.py`` as both ``after_install`` and ``after_migrate``.
Intentionally a no-op that only logs: it must never break
``install-app`` / ``bench migrate``, so everything is wrapped in
try/except.
"""

import frappe


def after_install():
	try:
		frappe.logger().info("peer_transfer: after_install stub (no-op)")
	except Exception:
		pass
