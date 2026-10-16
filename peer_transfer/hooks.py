app_name = "peer_transfer"
app_title = "Peer Transfer"
app_publisher = "developers@peertransfer.local"
app_description = "Peer Transfer"
app_email = "developers@peertransfer.local"
app_license = "gpl-3.0"

# Minimal dependency set: this scaffold needs only the framework.
required_apps = ["frappe"]

# Fixtures
fixtures = []

# Post-install / post-migrate hook. The stub only logs and can never
# abort install-app / bench migrate (see install.py).
after_install = "peer_transfer.install.after_install"
after_migrate = "peer_transfer.install.after_install"
