"""Closure builder for root_doctype Customer (v1).

Closure = roots + everything needed to re-create them elsewhere:
  reverse : Address / Contact rows whose Dynamic Link points at a root Customer
            (Contact ``phone_nos`` child rows travel inside the doc dict)
  forward : Customer Group + Territory parent chains, default Price List
            (Currency only -- no Item Price rows travel)

Row = {"doctype": ..., "name": ..., "data": {...}, "hash": sha256(canonical json)}
Manifest = {protocol, offer_id, root_doctype, roots, schema_hash,
            schema_snapshot, sender_apps, counts, row_total}
sender_apps = [{app, version}] for every installed app (version guarded).

Serialisation is ``doc.as_dict()`` minus system fields (owner/creation/
modified/modified_by/lft/rgt, ``__``-prefixed keys), applied recursively so
child rows are cleaned too. Rows are ordered parents-first for import.
"""

import hashlib
import uuid

import frappe
from frappe import _

from peer_transfer.api.schema import CLOSURE_DOCTYPES, canonical, schema_hash as _schema_hash, snapshot

PROTOCOL = 1
DISCOVERY_CAP = 500
CHUNK_PAGE = 200

# System/audit fields stripped from every serialized doc (incl. child rows).
SYSTEM_FIELDS = frozenset({"owner", "creation", "modified", "modified_by", "lft", "rgt"})

# Parents-first import order (also the row order).
IMPORT_ORDER = ["Customer Group", "Territory", "Price List", "Customer", "Address", "Contact"]

_GROUP_PARENT_FIELD = "parent_customer_group"
_TERRITORY_PARENT_FIELD = "parent_territory"


def _clean(obj):
	"""Recursively strip system fields from an as_dict structure (keeps idx)."""
	if isinstance(obj, dict):
		return {
			k: _clean(v)
			for k, v in obj.items()
			if k not in SYSTEM_FIELDS and not (isinstance(k, str) and k.startswith("__"))
		}
	if isinstance(obj, (list, tuple)):
		return [_clean(v) for v in obj]
	return obj


def row_hash(doctype, name, data):
	return hashlib.sha256(canonical({"doctype": doctype, "name": name, "data": data}).encode("utf-8")).hexdigest()


def _serialize(doctype, name):
	doc = frappe.get_doc(doctype, name)
	data = _clean(doc.as_dict())
	data["doctype"] = doctype
	if not data.get("name"):
		data["name"] = name
	return data


def _norm_roots(root_names):
	"""Accept a list or a JSON string; return a de-duplicated name list."""
	if root_names is None:
		return []
	if isinstance(root_names, str):
		text = root_names.strip()
		if not text:
			return []
		try:
			import json as _json

			parsed = _json.loads(text)
			if isinstance(parsed, list):
				root_names = parsed
			else:
				root_names = [text]
		except Exception:
			root_names = [s.strip() for s in text.split(",")]
	seen, out = set(), []
	for r in root_names or []:
		if r and r not in seen:
			seen.add(r)
			out.append(r)
	return out


def discover_roots(limit=DISCOVERY_CAP):
	"""All Customer names, capped (used when root_names is empty)."""
	return frappe.get_all("Customer", pluck="name", limit_page_length=limit) or []


def _linked_parents(child_doctype, roots):
	"""Address/Contact names whose Dynamic Link points at one of the roots."""
	if not roots:
		return []
	parents = set()
	batch = 500
	for i in range(0, len(roots), batch):
		chunk = roots[i : i + batch]
		try:
			rows = frappe.get_all(
				"Dynamic Link",
				filters={"link_doctype": "Customer", "link_name": ["in", chunk], "parenttype": child_doctype},
				fields=["parent"],
				limit_page_length=10000,
			)
		except Exception:
			rows = []
		for r in rows or []:
			p = r.get("parent") if isinstance(r, dict) else None
			if p:
				parents.add(p)
	return sorted(parents)


def _parent_chain(doctype, name, parent_field):
	"""Walk up a parent chain; returns [topmost ... name]. Guards missing fields/loops."""
	chain, seen = [], set()
	current = name
	while current and current not in seen:
		seen.add(current)
		if not frappe.db.exists(doctype, current):
			break
		chain.append(current)
		try:
			current = frappe.db.get_value(doctype, current, parent_field) or None
		except Exception:
			current = None
	chain.reverse()
	return chain


def _depth(doctype, name, parent_field):
	depth, seen, current = 0, set(), name
	while current and current not in seen:
		seen.add(current)
		try:
			current = frappe.db.get_value(doctype, current, parent_field) or None
		except Exception:
			current = None
		if current:
			depth += 1
	return depth


def _sender_apps():
	"""Installed apps with versions, guarded (never breaks closure build)."""
	try:
		apps = frappe.get_installed_apps() or []
	except Exception:
		return []
	out = []
	for app in apps:
		try:
			ver = frappe.get_attr("{0}.__version__".format(app))
		except Exception:
			ver = ""
		try:
			version = "" if ver is None else str(ver)
		except Exception:
			version = ""
		try:
			out.append({"app": app, "version": version})
		except Exception:
			continue
	return out


def build_closure(root_doctype="Customer", root_names=None):
	"""Build rows + manifest for the given roots.

	Returns {"rows": [...], "manifest": {...}}. Rows are parents-first.
	"""
	root_doctype = root_doctype or "Customer"
	if root_doctype != "Customer":
		frappe.throw(_("Peer Transfer v1 supports root_doctype 'Customer' only (got {0})").format(root_doctype))
	roots = _norm_roots(root_names)
	if not roots:
		roots = discover_roots()
	if not roots:
		frappe.throw(_("No root Customers found to offer"))

	# Validate roots exist (explicit lists may carry typos).
	missing = [r for r in roots if not frappe.db.exists("Customer", r)]
	if missing:
		frappe.throw(_("Customer(s) not found: {0}").format(", ".join(missing[:10])))

	group_names, territory_names, price_names = [], [], []
	customers = {}
	for r in roots:
		try:
			cg = frappe.db.get_value("Customer", r, "customer_group") or None
			tg = frappe.db.get_value("Customer", r, "territory") or None
			pl = frappe.db.get_value("Customer", r, "default_price_list") or None
		except Exception:
			cg, tg, pl = None, None, None
		customers[r] = (cg, tg, pl)
		if cg:
			group_names.extend(_parent_chain("Customer Group", cg, _GROUP_PARENT_FIELD))
		if tg:
			territory_names.extend(_parent_chain("Territory", tg, _TERRITORY_PARENT_FIELD))
		if pl and frappe.db.exists("Price List", pl):
			price_names.append(pl)

	def _uniq(seq):
		seen, out = set(), []
		for x in seq:
			if x and x not in seen:
				seen.add(x)
				out.append(x)
		return out

	group_names = _uniq(group_names)
	territory_names = _uniq(territory_names)
	price_names = _uniq(price_names)

	# Parents-first within each parent doctype (shallowest first, then name).
	group_names.sort(key=lambda n: (_depth("Customer Group", n, _GROUP_PARENT_FIELD), n))
	territory_names.sort(key=lambda n: (_depth("Territory", n, _TERRITORY_PARENT_FIELD), n))

	address_names = [a for a in _linked_parents("Address", roots) if frappe.db.exists("Address", a)]
	contact_names = [c for c in _linked_parents("Contact", roots) if frappe.db.exists("Contact", c)]

	ordered = (
		[("Customer Group", n) for n in group_names]
		+ [("Territory", n) for n in territory_names]
		+ [("Price List", n) for n in sorted(price_names)]
		+ [("Customer", n) for n in sorted(roots)]
		+ [("Address", n) for n in address_names]
		+ [("Contact", n) for n in contact_names]
	)

	rows = []
	for doctype, name in ordered:
		data = _serialize(doctype, name)
		rows.append({"doctype": doctype, "name": name, "data": data, "hash": row_hash(doctype, name, data)})

	counts = {
		"customer_groups": len(group_names),
		"territories": len(territory_names),
		"price_lists": len(price_names),
		"customers": len(roots),
		"addresses": len(address_names),
		"contacts": len(contact_names),
	}
	snap = snapshot(list(CLOSURE_DOCTYPES))
	manifest = {
		"protocol": PROTOCOL,
		"offer_id": uuid.uuid4().hex,
		"root_doctype": root_doctype,
		"roots": sorted(roots),
		"schema_hash": _schema_hash(snap),
		"schema_snapshot": snap,
		"sender_apps": _sender_apps(),
		"counts": counts,
		"row_total": len(rows),
	}
	return {"rows": rows, "manifest": manifest}
