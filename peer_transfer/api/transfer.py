"""Peer transfer orchestration: offer / connect / preview / accept / reject / status.

Six whitelisted endpoints (exact dotted paths -- the desk page codes against
these, do not rename):

    create_offer(root_doctype="Customer", root_names=None, ttl_secs=3600)
    connect_with_code(code)
    get_preview_rows(code_or_name, offset=0, limit=50)
    accept_and_import(code_or_name)
    reject_transfer(code_or_name)
    transfer_status(code_or_name)

Local ``Peer Transfer`` / ``Peer Transfer Log`` docs are written only when the
doctypes exist (``frappe.db.exists`` guards); field writes are additionally
guarded by ``meta.get_field`` so a diverged schema degrades instead of
throwing. NEVER imports from doctype controllers.
"""

import json
import uuid

import frappe
from frappe import _

from peer_transfer.api import closure as _closure
from peer_transfer.api import relay as _relay
from peer_transfer.api import schema as _schema

ALLOWED_ROLES = (
	"System Manager",
	"Sales User",
	"Sales Manager",
	"Purchase User",
	"Purchase Manager",
	"Accounts User",
	"Accounts Manager",
)

CHUNK_PAGE = 200
PREVIEW_CAP = 200

_TRANSFER_DT = "Peer Transfer"
_LOG_DT = "Peer Transfer Log"

# Local statuses mirror the Peer Transfer Select options.
ST_OFFERED = "Offered"
ST_PREVIEWED = "Previewed"
ST_ACCEPTED = "Accepted"
ST_REJECTED = "Rejected"
ST_IMPORTED = "Imported"
ST_FAILED = "Failed"


def _require_role():
	"""Gate every endpoint. Administrator bypasses like usual."""
	if frappe.session.user == "Administrator":
		return
	try:
		roles = set(frappe.get_roles())
	except Exception:
		roles = set()
	if roles & set(ALLOWED_ROLES):
		return
	frappe.throw(_("Not permitted to use Peer Transfer."), frappe.PermissionError)


def _has_dt(doctype):
	try:
		return bool(frappe.db.exists("DocType", doctype))
	except Exception:
		return False


def _set_if(doc, fieldname, value):
	"""Set a field only if it exists on the doc's meta. Returns True if set."""
	try:
		if doc.meta.get_field(fieldname):
			doc.set(fieldname, value)
			return True
	except Exception:
		pass
	return False


def _get_if(doc, fieldname, default=None):
	try:
		if doc.meta.get_field(fieldname):
			return doc.get(fieldname)
	except Exception:
		pass
	return default


def _log(transfer_name, action, detail=""):
	"""Best-effort audit line; never breaks the caller."""
	try:
		if not transfer_name or not _has_dt(_LOG_DT):
			return
		log = frappe.new_doc(_LOG_DT)
		_set_if(log, "transfer", transfer_name)
		_set_if(log, "action", action)
		_set_if(log, "detail", (detail or "")[:255])
		log.insert(ignore_permissions=True)
	except Exception:
		pass


def _as_json_str(obj):
	try:
		return json.dumps(obj, ensure_ascii=False, default=str)
	except Exception:
		return "{}"


def _parse_json_maybe(value, default=None):
	if value is None:
		return default
	if isinstance(value, (dict, list)):
		return value
	if isinstance(value, str) and value.strip():
		try:
			return json.loads(value)
		except Exception:
			return default
	return default


def _norm_root_names(root_names):
	return _closure._norm_roots(root_names)


def _manifest_of(payload):
	"""Relay manifest responses may be {manifest: {...}} or the manifest itself."""
	if isinstance(payload, dict) and isinstance(payload.get("manifest"), dict):
		return payload["manifest"]
	return payload if isinstance(payload, dict) else {}


def _rows_of(payload):
	"""Flatten relay rows responses into doc rows.

	Relay ``GET /rows`` returns chunk envelopes (``{seq, rows: [...]}``) and
	``total`` counts *envelopes*, not doc rows. Returns
	``(flat_rows, envelope_total, envelope_page_count)``; envelopes shaped
	``{doctype, name, ...}`` (no relay ever sends those, but tolerated) pass
	through as-is, bare lists are extended.
	"""
	if isinstance(payload, list):
		items, total = payload, len(payload)
	elif isinstance(payload, dict):
		items = payload.get("rows")
		if not isinstance(items, list):
			return [], 0, 0
		total = payload.get("total", payload.get("row_total", len(items)))
		try:
			total = int(total)
		except Exception:
			total = len(items)
	else:
		return [], 0, 0
	flat = []
	for item in items:
		if isinstance(item, dict) and isinstance(item.get("rows"), list) and not item.get("doctype"):
			flat.extend(r for r in item["rows"] if isinstance(r, dict))
		elif isinstance(item, list):
			flat.extend(r for r in item if isinstance(r, dict))
		elif isinstance(item, dict) and item.get("doctype"):
			flat.append(item)
	return flat, total, len(items)


def _find_transfer(code_or_name):
	"""Local Peer Transfer doc by name or by code field. Returns doc or None."""
	if not code_or_name or not _has_dt(_TRANSFER_DT):
		return None
	try:
		if frappe.db.exists(_TRANSFER_DT, code_or_name):
			return frappe.get_doc(_TRANSFER_DT, code_or_name)
	except Exception:
		pass
	try:
		meta = frappe.get_meta(_TRANSFER_DT)
	except Exception:
		return None
	for field in ("code", "transfer_code", "offer_code", "peer_code"):
		try:
			if not meta.get_field(field):
				continue
			hit = frappe.get_all(_TRANSFER_DT, filters={field: code_or_name}, pluck="name", limit_page_length=1)
			if hit:
				return frappe.get_doc(_TRANSFER_DT, hit[0])
		except Exception:
			continue
	return None


def _resolve_code(code_or_name):
	"""Relay code for a code-or-local-name reference."""
	doc = _find_transfer(code_or_name)
	if doc is not None:
		for field in ("code", "transfer_code", "offer_code", "peer_code"):
			val = _get_if(doc, field)
			if val:
				return val, doc
		return None, doc
	return code_or_name, None


def _store_transfer(direction, status, code, manifest, roots, action):
	"""Create or update the local Peer Transfer doc. Returns name (or in-memory id)."""
	if not _has_dt(_TRANSFER_DT):
		return "MEM-{0}".format((manifest or {}).get("offer_id") or code or uuid.uuid4().hex[:8])
	doc = None
	if code:
		doc = _find_transfer(code)
	try:
		if doc is None:
			doc = frappe.new_doc(_TRANSFER_DT)
		_set_if(doc, "code", code)
		_set_if(doc, "direction", direction)
		_set_if(doc, "status", status)
		_set_if(doc, "root_doctype", (manifest or {}).get("root_doctype") or "Customer")
		_set_if(doc, "root_names", _as_json_str(roots if roots is not None else (manifest or {}).get("roots") or []))
		_set_if(doc, "schema_hash", (manifest or {}).get("schema_hash"))
		_set_if(doc, "manifest_json", _as_json_str(manifest or {}))
		if doc.get("__islocal"):
			doc.insert(ignore_permissions=True)
		else:
			doc.save(ignore_permissions=True)
		_log(doc.name, action, "code={0}".format(code))
		return doc.name
	except Exception:
		return "MEM-{0}".format((manifest or {}).get("offer_id") or code or uuid.uuid4().hex[:8])


def _mark_status(doc, status, receipt=None, action=None):
	try:
		if doc is None:
			return
		_set_if(doc, "status", status)
		if receipt is not None:
			_set_if(doc, "receipt_json", _as_json_str(receipt))
		doc.save(ignore_permissions=True)
		if action:
			_log(doc.name, action, "status={0}".format(status))
	except Exception:
		pass


@frappe.whitelist()
def create_offer(root_doctype="Customer", root_names=None, ttl_secs=3600):
	"""Offer local Customers to a peer via the relay.

	Returns {code, name, counts}.
	"""
	_require_role()
	try:
		ttl_secs = int(ttl_secs or 3600)
	except Exception:
		ttl_secs = 3600
	ttl_secs = max(60, ttl_secs)

	built = _closure.build_closure(root_doctype=root_doctype or "Customer", root_names=_norm_root_names(root_names))
	rows, manifest = built["rows"], built["manifest"]

	# The relay code is the local doc's required unique key, so the Sent doc
	# can only be stored once the relay has issued the code (storing first
	# with a blank code fails validation and degrades to a MEM- id).
	data, err = _relay.create_offer(ttl_secs, manifest)
	if err or not (data or {}).get("code"):
		frappe.throw(_("Relay offer failed: {0}").format(err or "no code returned"))
	code = data["code"]

	# Rows as chunks seq 0..n (page size 200).
	for seq in range(0, max(1, (len(rows) + CHUNK_PAGE - 1) // CHUNK_PAGE if rows else 1)):
		chunk = rows[seq * CHUNK_PAGE : (seq + 1) * CHUNK_PAGE]
		if not chunk:
			break
		_, err = _relay.post_chunk(code, seq, chunk)
		if err:
			_store_transfer("Sent", ST_FAILED, code, manifest, manifest.get("roots"), "failed")
			frappe.throw(_("Relay chunk {0} failed: {1}").format(seq, err))

	name = _store_transfer("Sent", ST_OFFERED, code, manifest, manifest.get("roots"), "offered")
	_log(name, "offered", "code={0} rows={1}".format(code, len(rows)))
	return {"code": code, "name": name, "counts": manifest.get("counts") or {}}


@frappe.whitelist()
def connect_with_code(code):
	"""Pair with a relay offer code.

	Returns {name, manifest, schema_diff: {match, diffs}}.
	"""
	_require_role()
	if not code:
		frappe.throw(_("Offer code is required"))
	data, err = _relay.get_manifest(code)
	if err or data is None:
		frappe.throw(_("Relay manifest fetch failed: {0}").format(err or "empty response"))
	manifest = _manifest_of(data)
	if not manifest or not manifest.get("roots"):
		frappe.throw(_("Relay offer '{0}' not found or expired").format(code))

	sender_snapshot = manifest.get("schema_snapshot") or {}
	local_snapshot = _schema.snapshot(list(sender_snapshot.keys()) or None)
	diff = _schema.compare(sender_snapshot, local_snapshot)

	name = _store_transfer("Received", ST_PREVIEWED, code, manifest, manifest.get("roots"), "previewed")
	return {"name": name, "manifest": manifest, "schema_diff": diff}


@frappe.whitelist()
def get_preview_rows(code_or_name, offset=0, limit=50):
	"""Page relay rows for an offer. Returns {rows, total} (limit capped at 200)."""
	_require_role()
	if not code_or_name:
		frappe.throw(_("Offer code is required"))
	try:
		offset = max(0, int(offset or 0))
	except Exception:
		offset = 0
	try:
		limit = max(1, int(limit or 50))
	except Exception:
		limit = 50
	limit = min(limit, PREVIEW_CAP)
	code, _doc = _resolve_code(code_or_name)
	if not code:
		frappe.throw(_("Cannot resolve relay code for '{0}'").format(code_or_name))
	# Relay pages chunk envelopes, so a doc-row page is sliced locally after
	# flattening the whole offer (v1 offers are small; relay caps at 2000
	# envelopes per call).
	all_rows, _total = _fetch_all_rows(code)
	return {"rows": all_rows[offset : offset + limit], "total": len(all_rows)}


def _fetch_all_rows(code):
	"""Fetch every relay row for a code, unwrapping chunk envelopes.

	Relay ``offset``/``limit``/``total`` count envelopes, so the cursor here
	advances in envelopes while accumulating flattened doc rows.
	"""
	all_rows, total, offset = [], None, 0
	while True:
		data, err = _relay.get_rows(code, offset=offset, limit=2000)
		if err or data is None:
			frappe.throw(_("Relay rows fetch failed at offset {0}: {1}").format(offset, err or "empty response"))
		rows, total, n_env = _rows_of(data)
		all_rows.extend(rows)
		if not n_env or (total is not None and offset + n_env >= total):
			break
		offset += n_env
		if offset > 100000:
			break
	return all_rows, len(all_rows)


def _dry_run(rows):
	"""Pre-check link resolution. Returns {ok: [...rows...], failed: [{doctype, name, error}]}."""
	batch = set()
	for r in rows or []:
		if isinstance(r, dict) and r.get("doctype") and r.get("name"):
			batch.add((r["doctype"], r["name"]))
	failed, ok = [], []
	for r in rows or []:
		if not isinstance(r, dict) or not r.get("doctype") or not r.get("name"):
			failed.append({"doctype": (r or {}).get("doctype"), "name": (r or {}).get("name"), "error": "Malformed row"})
			continue
		dt, name, data = r["doctype"], r["name"], r.get("data") or {}
		problems = []
		if dt == "Customer":
			for field, target in (
				("customer_group", "Customer Group"),
				("territory", "Territory"),
				("default_price_list", "Price List"),
			):
				val = data.get(field)
				if not val:
					continue
				if (target, val) not in batch and not frappe.db.exists(target, val):
					problems.append("{0} '{1}' ({2}) not found".format(field, val, target))
		elif dt in ("Address", "Contact"):
			for link in data.get("links") or []:
				if not isinstance(link, dict):
					continue
				lt, ln = link.get("link_doctype"), link.get("link_name")
				if lt == "Customer" and ln and (lt, ln) not in batch and not frappe.db.exists(lt, ln):
					problems.append("link {0} '{1}' not found".format(lt, ln))
		if problems:
			failed.append({"doctype": dt, "name": name, "error": "; ".join(problems)})
		else:
			ok.append(r)
	return {"ok": ok, "failed": failed}


def _import_order_index(doctype):
	try:
		return _closure.IMPORT_ORDER.index(doctype)
	except ValueError:
		return len(_closure.IMPORT_ORDER)


def _insert_row(row):
	"""Insert one row (SKIP when the name already exists). Returns (outcome, detail)."""
	dt, name = row.get("doctype"), row.get("name")
	data = dict(row.get("data") or {})
	if not dt or not name:
		return "failed", "Malformed row"
	try:
		if frappe.db.exists(dt, name):
			return "skipped", "Already exists"
	except Exception as e:
		return "failed", "Exists-check failed: {0}".format(e)
	data["doctype"] = dt
	if not data.get("name"):
		data["name"] = name
	try:
		doc = frappe.get_doc(data)
		doc.insert(ignore_permissions=True)
		return "created", doc.name
	except Exception as e:
		return "failed", str(e)[:500]


@frappe.whitelist()
def accept_and_import(code_or_name):
	"""Verify schema, dry-run links, import parents-first. Returns receipt {created, skipped, failed}."""
	_require_role()
	if not code_or_name:
		frappe.throw(_("Offer code is required"))
	code, doc = _resolve_code(code_or_name)
	if not code:
		frappe.throw(_("Cannot resolve relay code for '{0}'").format(code_or_name))

	# 1. Manifest + schema gate (strict: must match).
	data, err = _relay.get_manifest(code)
	if err or data is None:
		frappe.throw(_("Relay manifest fetch failed: {0}").format(err or "empty response"))
	manifest = _manifest_of(data)
	if not manifest or not manifest.get("schema_snapshot"):
		frappe.throw(_("Relay offer '{0}' not found or expired").format(code))
	ok, hash_err = _schema.verify_manifest(manifest)
	if not ok:
		frappe.throw(_("Schema verification failed: {0}").format(hash_err))
	diff = _schema.compare(manifest.get("schema_snapshot") or {})
	if not diff.get("match"):
		first = (diff.get("diffs") or [{}])[0]
		frappe.throw(
			_("Schema mismatch: {0} ({1} diff(s), first: {2}.{3} {4})").format(
				"offer schema does not match local meta",
				len(diff.get("diffs") or []),
				first.get("doctype"),
				first.get("field"),
				first.get("issue"),
			)
		)

	# 2. Fetch all rows + dry-run link resolution.
	rows, _total = _fetch_all_rows(code)
	rows = sorted(rows, key=lambda r: (_import_order_index((r or {}).get("doctype")), (r or {}).get("name") or ""))
	pre = _dry_run(rows)

	# 3. Insert parents-first, one savepoint per record, SKIP on conflict.
	receipt = {"created": 0, "skipped": 0, "failed": list(pre.get("failed") or []),
			   "created_names": [], "skipped_names": []}
	frappe.flags.in_import = True
	try:
		for i, row in enumerate(pre.get("ok") or []):
			sp = "peer_tr_{0}".format(i)
			try:
				frappe.db.savepoint(sp)
			except Exception:
				sp = None
			outcome, detail = _insert_row(row)
			if outcome == "created":
				receipt["created"] += 1
				receipt["created_names"].append(detail)
			elif outcome == "skipped":
				receipt["skipped"] += 1
				receipt["skipped_names"].append(row.get("name"))
			else:
				if sp:
					try:
						frappe.db.rollback(save_point=sp)
					except Exception:
						pass
				receipt["failed"].append({"doctype": row.get("doctype"), "name": row.get("name"), "error": detail})
	finally:
		try:
			frappe.flags.in_import = False
		except Exception:
			pass

	_mark_status(doc, ST_IMPORTED if not receipt["failed"] else ST_ACCEPTED, receipt=receipt, action="imported")
	return receipt


@frappe.whitelist()
def reject_transfer(code_or_name):
	"""Mark a transfer Rejected; best-effort relay ack/consume. Returns {ok}."""
	_require_role()
	if not code_or_name:
		frappe.throw(_("Offer code is required"))
	code, doc = _resolve_code(code_or_name)
	_mark_status(doc, ST_REJECTED, action="rejected")
	if code:
		try:
			_relay.ack(code)
		except Exception:
			pass
	return {"ok": True}


@frappe.whitelist()
def transfer_status(code_or_name):
	"""Local + relay status for an offer. Returns {status, counts, receipt}."""
	_require_role()
	if not code_or_name:
		frappe.throw(_("Offer code is required"))
	doc = _find_transfer(code_or_name)
	if doc is not None:
		manifest = _parse_json_maybe(_get_if(doc, "manifest_json"), default={}) or {}
		return {
			"status": _get_if(doc, "status") or "Unknown",
			"counts": manifest.get("counts") or {},
			"receipt": _parse_json_maybe(_get_if(doc, "receipt_json"), default=None),
		}
	code, _d = _resolve_code(code_or_name)
	data, err = _relay.get_manifest(code) if code else (None, "no code")
	if err or data is None:
		return {"status": "Unknown", "counts": {}, "receipt": None}
	manifest = _manifest_of(data)
	return {"status": manifest.get("status") or ST_OFFERED, "counts": manifest.get("counts") or {}, "receipt": None}
