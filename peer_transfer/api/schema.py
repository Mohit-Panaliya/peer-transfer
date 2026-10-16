"""Schema snapshot + compare for peer transfer (v1).

Snapshot per doctype in the closure::

    {fieldname: {"fieldtype": ..., "options": ..., "reqd": ...}}

for Customer, Address, Contact, Customer Group, Territory, Price List
(+ Dynamic Link, the child table that carries Address/Contact links).

``schema_hash`` is sha256 over the canonical JSON of the snapshot.
``compare`` diffs a sender snapshot against local meta::

    blocking / warnings / diffs: [{doctype, field, issue, detail, severity}]
    issue in missing_field | type_mismatch | mandatory_mismatch | options_mismatch

``match`` is True only when there are no BLOCKING diffs (warnings alone do
not block import; they may still escalate at accept time if rows carry
values for a warned-away field). Import is gated on match + no app gaps
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

# UI-only fieldtypes that carry no storable row data.
# Verified against frappe/model/__init__.py `display_fieldtypes`:
#   ("Section Break", "Column Break", "Tab Break", "HTML",
#    "Button", "Image", "Fold", "Heading").
# NOTE: "Image" here is the display fieldtype (no_value_fields). It is NOT
# "Attach Image" (which IS data-carrying -- in data_fieldtypes -- and is
# deliberately excluded from this set). When in doubt a type stays OUT.
LAYOUT_TYPES = frozenset(
	{
		"Section Break",
		"Column Break",
		"Tab Break",
		"HTML",
		"Button",
		"Fold",
		"Heading",
		"Image",
	}
)

# Compatible free-text types: values remain storable across these types,
# so a mismatch within the group is a warning, not blocking.
# (All are in frappe's data_fieldtypes.)
TEXT_GROUP = frozenset(
	{
		"Data",
		"Small Text",
		"Text",
		"Text Editor",
		"Code",
		"Read Only",
		"Autocomplete",
	}
)

# Child-table carriers: a missing table locally always risks data loss.
TABLE_TYPES = frozenset({"Table", "Table MultiSelect"})


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


def _norm_options(options):
	"""Normalize a Select options string into a comparable list of lines."""
	if options is None:
		return []
	try:
		text = str(options)
	except Exception:
		return []
	# Frappe Select options are newline-separated; compare line-by-line
	# after stripping whitespace, dropping empties.
	return [line.strip() for line in text.replace("\r\n", "\n").split("\n") if line.strip()]


def compare(sender_snapshot, local_snapshot=None):
	"""Diff a sender snapshot against local meta.

	Returns {"match": bool, "blocking": [...], "warnings": [...], "diffs": [...]}.
	Every diff entry is {doctype, field, issue, detail, severity} with
	severity in "blocking" | "warning". ``diffs`` = blocking + warnings
	(kept for backward compatibility) and ``match`` is True when there is
	no blocking entry (warnings do NOT block import by themselves).

	Severity rules (general-purpose, sender-subset comparison; extra local
	fields are ignored):
	  * missing locally + fieldtype in LAYOUT_TYPES -> warning
	  * missing Table / Table MultiSelect -> blocking
	  * missing data field + sender reqd=1 -> blocking
	  * missing + optional -> warning
	  * type mismatch within TEXT_GROUP -> warning; cross-group -> blocking
	  * Select options differing (same fieldtype, different options) -> warning

	``local_snapshot`` keeps its old meaning: a {doctype: {field: spec}}
	snapshot dict, or None to snapshot live local meta. A non-dict value
	is treated as None (live snapshot) so older callers cannot crash here.
	"""
	sender_snapshot = sender_snapshot or {}
	if local_snapshot is None or not isinstance(local_snapshot, dict):
		doctypes = list(sender_snapshot.keys()) or list(CLOSURE_DOCTYPES)
		local_snapshot = snapshot(doctypes)
	blocking = []
	warnings = []
	for doctype, sender_fields in (sender_snapshot or {}).items():
		sender_fields = sender_fields or {}
		local_fields = (local_snapshot or {}).get(doctype) or {}
		if not local_fields and not frappe.db.exists("DocType", doctype):
			blocking.append(
				{
					"doctype": doctype,
					"field": "",
					"issue": "missing_field",
					"detail": "DocType '{0}' does not exist locally".format(doctype),
					"severity": "blocking",
				}
			)
			continue
		for field, spec in sender_fields.items():
			spec = spec or {}
			s_ft = spec.get("fieldtype") or ""
			s_reqd = int(bool(spec.get("reqd")))
			local = local_fields.get(field)
			if local is None:
				if s_ft in LAYOUT_TYPES:
					warnings.append(
						{
							"doctype": doctype,
							"field": field,
							"issue": "missing_field",
							"detail": "Field '{0}.{1}' ({2}) does not exist locally (layout-only, safe to skip)".format(
								doctype, field, s_ft
							),
							"severity": "warning",
						}
					)
				elif s_ft in TABLE_TYPES:
					blocking.append(
						{
							"doctype": doctype,
							"field": field,
							"issue": "missing_field",
							"detail": "Field '{0}.{1}' ({2}) does not exist locally (child table, data would be lost)".format(
								doctype, field, s_ft
							),
							"severity": "blocking",
						}
					)
				elif s_reqd == 1:
					blocking.append(
						{
							"doctype": doctype,
							"field": field,
							"issue": "missing_field",
							"detail": "Field '{0}.{1}' ({2}) is required on sender but does not exist locally".format(
								doctype, field, s_ft
							),
							"severity": "blocking",
						}
					)
				else:
					warnings.append(
						{
							"doctype": doctype,
							"field": field,
							"issue": "missing_field",
							"detail": "Field '{0}.{1}' ({2}) does not exist locally (optional on sender)".format(
								doctype, field, s_ft
							),
							"severity": "warning",
						}
					)
				continue
			l_ft = local.get("fieldtype") or ""
			l_reqd = int(bool(local.get("reqd")))
			if s_ft != l_ft:
				if s_ft in TEXT_GROUP and l_ft in TEXT_GROUP:
					warnings.append(
						{
							"doctype": doctype,
							"field": field,
							"issue": "type_mismatch",
							"detail": "Field '{0}.{1}' is {2} on sender, {3} locally (compatible text types, values still storable)".format(
								doctype, field, s_ft, l_ft
							),
							"severity": "warning",
						}
					)
				else:
					blocking.append(
						{
							"doctype": doctype,
							"field": field,
							"issue": "type_mismatch",
							"detail": "Field '{0}.{1}' is {2} on sender, {3} locally".format(
								doctype, field, s_ft, l_ft
							),
							"severity": "blocking",
						}
					)
				continue
			# Same fieldtype: Select options drift is a warning, never blocking.
			if s_ft == "Select":
				if _norm_options(spec.get("options")) != _norm_options(local.get("options")):
					warnings.append(
						{
							"doctype": doctype,
							"field": field,
							"issue": "options_mismatch",
							"detail": "Field '{0}.{1}' Select options differ (sender={2} local={3})".format(
								doctype, field, _norm_options(spec.get("options")), _norm_options(local.get("options"))
							),
							"severity": "warning",
						}
					)
					continue
			if s_reqd != l_reqd:
				if s_reqd == 1 and l_reqd == 0:
					warnings.append(
						{
							"doctype": doctype,
							"field": field,
							"issue": "mandatory_mismatch",
							"detail": "Field '{0}.{1}' reqd={2} on sender, reqd={3} locally (local is more permissive)".format(
								doctype, field, s_reqd, l_reqd
							),
							"severity": "warning",
						}
					)
				else:
					blocking.append(
						{
							"doctype": doctype,
							"field": field,
							"issue": "mandatory_mismatch",
							"detail": "Field '{0}.{1}' reqd={2} on sender, reqd={3} locally (local requires a value the sender may not send)".format(
								doctype, field, s_reqd, l_reqd
							),
							"severity": "blocking",
						}
					)
	return {"match": len(blocking) == 0, "blocking": blocking, "warnings": warnings, "diffs": blocking + warnings}


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
