"""HTTP client for the peer relay service (stdlib urllib only).

Single place that knows the relay wire protocol. Every other module in this
app goes through :func:`request`. Mirrors the (json, error) tuple pattern from
``fast_entry_app.api.openwa``: never raises on transport/HTTP failures, never
imports ``requests``.

Relay endpoints (verified live):
    POST /v1/offers {ttl_secs, manifest}          -> {code, ...}
    POST /v1/offers/:code/chunks {seq, rows}     -> {...}
    GET  /v1/offers/:code/manifest               -> manifest (or {manifest: ...})
    GET  /v1/offers/:code/rows?offset&limit      -> {rows, total} (limit cap 2000)
        NOTE (verified live 2026-10-16): ``rows`` holds chunk envelopes
        ``{seq, rows: [...doc rows...]}`` and ``total`` counts envelopes, not
        doc rows -- callers must unwrap (see transfer._rows_of) and page the
        cursor in envelopes.
    POST /v1/offers/:code/ack {}                 -> {} (consume-delete)

Relay errors are JSON ``{"error": ...}`` with HTTP 404/400 -- never a traceback.
"""

import json
import urllib.error
import urllib.parse
import urllib.request

import frappe

DEFAULT_RELAY_URL = "http://140.245.23.32:8787"
REQUEST_TIMEOUT = 30
ROWS_HARD_CAP = 2000


def base_url():
	"""Relay base URL from site config, with the verified default."""
	try:
		return str(frappe.conf.get("peer_relay_url", DEFAULT_RELAY_URL) or DEFAULT_RELAY_URL).rstrip("/")
	except Exception:
		return DEFAULT_RELAY_URL


def request(path, method="GET", data=None, timeout=REQUEST_TIMEOUT):
	"""Call a relay API path. Returns (parsed_json, error_str); error is None on success.

	The error string is prefixed with the HTTP status (``HTTP 404: ...``) so
	callers can distinguish not-found/expired offers from transport failures.
	"""
	url = base_url() + path
	payload = None if data is None else json.dumps(data, ensure_ascii=False).encode("utf-8")
	req = urllib.request.Request(
		url,
		data=payload,
		headers={"Content-Type": "application/json"},
		method=method,
	)
	try:
		with urllib.request.urlopen(req, timeout=timeout) as resp:
			raw = resp.read().decode("utf-8")
			return (json.loads(raw) if raw.strip() else {}), None
	except urllib.error.HTTPError as e:
		detail = e.read().decode("utf-8", "replace")
		try:
			body = json.loads(detail) if detail.strip() else {}
			msg = body.get("error") or body.get("message") or detail
			if isinstance(msg, (dict, list)):
				msg = json.dumps(msg)
		except Exception:
			msg = detail
		return None, "HTTP {0}: {1}".format(e.code, str(msg)[:300])
	except Exception as e:
		return None, str(e)


def _code_path(code, suffix=""):
	return "/v1/offers/{0}{1}".format(urllib.parse.quote(str(code), safe=""), suffix)


def create_offer(ttl_secs, manifest, timeout=REQUEST_TIMEOUT):
	"""POST /v1/offers -> (data, error); data holds the relay ``code``."""
	return request("/v1/offers", method="POST", data={"ttl_secs": ttl_secs, "manifest": manifest}, timeout=timeout)


def post_chunk(code, seq, rows, timeout=REQUEST_TIMEOUT):
	"""POST one row chunk -> (data, error)."""
	return request(_code_path(code, "/chunks"), method="POST", data={"seq": seq, "rows": rows}, timeout=timeout)


def get_manifest(code, timeout=REQUEST_TIMEOUT):
	"""GET offer manifest -> (data, error)."""
	return request(_code_path(code, "/manifest"), method="GET", timeout=timeout)


def get_rows(code, offset=0, limit=200, timeout=REQUEST_TIMEOUT):
	"""GET offer rows paged -> (data, error). Relay caps limit at 2000."""
	try:
		offset = max(0, int(offset or 0))
	except Exception:
		offset = 0
	try:
		limit = max(1, int(limit or 200))
	except Exception:
		limit = 200
	limit = min(limit, ROWS_HARD_CAP)
	path = _code_path(code, "/rows") + "?" + urllib.parse.urlencode({"offset": offset, "limit": limit})
	return request(path, method="GET", timeout=timeout)


def ack(code, timeout=REQUEST_TIMEOUT):
	"""POST consume-delete ack -> (data, error). Best-effort by callers."""
	return request(_code_path(code, "/ack"), method="POST", data={}, timeout=timeout)
