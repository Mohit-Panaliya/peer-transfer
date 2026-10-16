"""Schema snapshot + compare for peer transfer (v1).

Snapshot per doctype in the closure::

    {fieldname: {"fieldtype": ..., "options": ..., "reqd": ...}}

for Customer, Address, Contact, Customer Group, Territory, Price List
(+ Dynamic Link, the child table that carries Address/Contact links).

``schema_hash`` is sha256 over the canonical JSON of the snapshot.
``compare`` diffs a sender snapshot against local meta::

    diffs: [{doctype, field, issue, detail}]
    issue in missing_field | type_mismatch | mandatory_mismatch

``match`` is True only when there are no diffs. Import is gated on match
(strict -- no hash-equality fallback).
"""

import hashlib
import json

import frappe

# Doctypes covered by the v1 Customer closure, in parents-first import order.
CLOSURE_DOCTYPES = [
	"Customer Group",
	"Territory",
	"Price List",
	"Customer",
	"Address",
	"Contact",
	"Dynamic Link",
]


def canonical(obj):
	"""Canonical JSON for hashing (sorted keys, compact separators)."""
	return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def schema_hash(snapshot):
	"""sha256 hex of the canonical snapshot JSON."""
	return hashlib.sha256(canonical(snapshot).encode("utf-8")).hexdigest()


def snapshot_doctype(doctype):
	"""Snapshot one doctype's fields from local meta. Returns {} if DocType missing."""
	try:
		if not frappe.db.exists("DocType", doctype):
			return {}
		meta = frappe.get_meta(doctype)
	except Exception:
		return {}
	out = {}
	try:
		fields = meta.fields or []
	except Exception:
		fields = []
	for df in fields:
		try:
			fname = getattr(df, "fieldname", None)
			if not fname:
				continue
			out[fname] = {
				"fieldtype": getattr(df, "fieldtype", "") or "",
				"options": getattr(df, "options", "") or "",
				"reqd": 1 if getattr(df, "reqd", 0) else 0,
			}
		except Exception:
			continue
	return out


def snapshot(doctypes=None):
	"""Snapshot local meta for the closure doctypes. Returns {doctype: {field: spec}}."""
	doctypes = list(doctypes) if doctypes else list(CLOSURE_DOCTYPES)
	return {dt: snapshot_doctype(dt) for dt in doctypes}


def compare(sender_snapshot, local_snapshot=None):
	"""Diff a sender snapshot against local meta.

	Returns {"match": bool, "diffs": [{doctype, field, issue, detail}]}.
	Extra local fields are ignored (sender-subset comparison only).
	"""
	sender_snapshot = sender_snapshot or {}
	if local_snapshot is None:
		doctypes = list(sender_snapshot.keys()) or list(CLOSURE_DOCTYPES)
		local_snapshot = snapshot(doctypes)
	diffs = []
	for doctype, sender_fields in (sender_snapshot or {}).items():
		sender_fields = sender_fields or {}
		local_fields = (local_snapshot or {}).get(doctype) or {}
		if not local_fields and not frappe.db.exists("DocType", doctype):
			diffs.append(
				{
					"doctype": doctype,
					"field": "",
					"issue": "missing_field",
					"detail": "DocType '{0}' does not exist locally".format(doctype),
				}
			)
			continue
		for field, spec in sender_fields.items():
			spec = spec or {}
			local = local_fields.get(field)
			if local is None:
				diffs.append(
					{
						"doctype": doctype,
						"field": field,
						"issue": "missing_field",
						"detail": "Field '{0}.{1}' does not exist locally".format(doctype, field),
					}
				)
				continue
			if (spec.get("fieldtype") or "") != (local.get("fieldtype") or ""):
				diffs.append(
					{
						"doctype": doctype,
						"field": field,
						"issue": "type_mismatch",
						"detail": "Field '{0}.{1}' is {2} on sender, {3} locally".format(
							doctype, field, spec.get("fieldtype"), local.get("fieldtype")
						),
					}
				)
			elif int(bool(spec.get("reqd"))) != int(bool(local.get("reqd"))):
				diffs.append(
					{
						"doctype": doctype,
						"field": field,
						"issue": "mandatory_mismatch",
						"detail": "Field '{0}.{1}' reqd={2} on sender, reqd={3} locally".format(
							doctype, field, int(bool(spec.get("reqd"))), int(bool(local.get("reqd")))
						),
					}
				)
	return {"match": len(diffs) == 0, "diffs": diffs}


def verify_manifest(manifest):
	"""Recompute schema_hash over the manifest snapshot. Returns (ok, error)."""
	manifest = manifest or {}
	snap = manifest.get("schema_snapshot")
	expected = manifest.get("schema_hash")
	if not snap or not expected:
		return False, "Manifest has no schema_snapshot/schema_hash"
	try:
		actual = schema_hash(snap)
	except Exception as e:
		return False, "Cannot hash manifest snapshot: {0}".format(e)
	if actual != expected:
		return False, "Manifest schema_hash mismatch (tampered or corrupt manifest)"
	return True, None
