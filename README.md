# Peer Transfer — Frappe-to-Frappe P2P Data Transfer

A general-purpose **peer-to-peer data transfer system for Frappe v16**: move master
data (starting with **Customer + its full Address/Contact trail**) from any Frappe
site to any other — localhost-to-localhost, localhost-to-cloud, cloud-to-cloud —
with **zero manual CSV work**, gated by **schema compatibility**, **human preview +
accept**, and a full **audit receipt**.

No TURN/STUN, no VPN required, no inbound ports on either laptop. Both sites only
ever make **outbound HTTPS** calls: directly to each other when reachable, or via
the included lightweight relay when NAT'd.

## How it works

```
Site A (sender)                                    Site B (receiver)
───────────────                                    ─────────────────
Peer Transfer page → Send tab
  select customers → closure built
  → schema snapshot+hash → POST offer ──▶ Relay (VPS): stores opaque
  → shows 6-digit NUMBER (e.g. 482917)      envelope {manifest + chunks}
                                                    │
Site B: Receive tab → enter NUMBER ── GET manifest ─┘
  → schema VALIDATION (target side, before anything moves)
  → grouped PREVIEW (customer → addresses → contacts)
  → Accept & Commit ── pull rows in chunks ──▶ parents-first import
  → receipt {created, skipped, failed} on both sides
```

- **Number ceremony:** single-use 6-digit code, TTL (default 1h, max 24h),
  consume-on-accept, clean 404 after expiry. Wrong code → "not found or expired",
  never a traceback.
- **Schema gate:** protocol version → installed-app versions → per-field
  `fieldtype/options/reqd` compare over every DocType in the closure. Offer carries
  a SHA-256 `schema_hash`, re-verified at commit. **Send/Accept stay disabled
  until MATCH**; DIFF blocks with a field-level table.
- **Commit rules:** dry-run link resolution → parents-first insert
  (groups → territory → price list → Customer → Address/Contact) with per-record
  savepoints → default conflict = **skip + report** (receiver-wins) → idempotent
  re-accept (`offer_id + content_hash`, double-click safe) → resume from last ack.
- **Audit:** every state change lands in `Peer Transfer Log`
  (paired/offered/previewed/accepted/rejected/imported/failed + actor + timestamp).

## Repo layout

```
peer_transfer/          # the Frappe app (bench get-app this repo root)
  peer_transfer/
    hooks.py            # app_name, required_apps=["frappe"], install hooks
    install.py          # try/except-safe after_install/after_migrate
    api/
      relay.py          # relay HTTP client (stdlib urllib, (json, error) tuples)
      closure.py        # Customer closure: reverse links[] + parent chains
      schema.py         # snapshot / hash / compare over closure doctypes
      transfer.py       # 6 whitelisted endpoints (role-gated)
    peer_transfer/
      doctype/          # Peer Transfer, Peer Transfer Log (+ controllers)
      page/             # peer-transfer-page Desk page (Send/Receive/Log)
relay/                  # Rust envelope relay (axum + SQLite, ~2.3MiB RSS)
  src/main.rs           # 6 endpoints: health, offers, chunks, manifest, rows, ack
  Dockerfile            # multi-stage, ARM64-native build
  docker-compose.yml    # relay on :8787 + named volume
  Caddyfile.example     # future TLS path (subdomain → reverse_proxy)
```

Closure definition (v1, Customer root): explicit names or filtered discovery →
reverse `Address.links[] → Customer` + `Contact.links[] → Customer` (phone rows
ride inside Contact) → forward Customer Group/Territory parent chains + default
Price List. Customer display caches (`*_html`, fetch-from mobile/email) are
recomputed, never transported.

## Install

```bash
bench get-app https://github.com/<you>/peer-transfer
bench --site <site> install-app peer_transfer
bench --site <site> migrate
```

Page: `/app/peer-transfer-page` (the `/app/peer-transfer` route belongs to the
DocType list — same-slug collision is why the page lives at `-page`).
Config: relay base URL defaults to the test relay; override per site via
`peer_relay_url` in `site_config.json`. Viewing user needs System Manager **plus**
a master-read role (e.g. Sales Manager) or the Send search returns 403.

## Relay deploy (VPS)

```bash
cd relay && docker compose up -d --build
curl http://<vps>:8787/health   # {"ok":true,"service":"peer-relay",...}
```

Endpoints: `POST /v1/offers {ttl_secs, manifest} → {code}`,
`POST /v1/offers/:code/chunks {seq, rows}` (upsert per seq),
`GET …/manifest`, `GET …/rows?offset&limit` (cap 2000),
`POST …/ack {}` (consume-delete). Bodies capped at 32MB; all errors are
`{"error":…}` JSON (400/404/413/422, never a traceback). SQLite WAL + 60s expiry
sweeper + `unless-stopped`.

> **Production TLS:** put a subdomain (e.g. `relay.<domain>`) on the VPS behind
> Traefik/Caddy (`Caddyfile.example`), then point `peer_relay_url` at
> `https://…`. Until then, `:8787` traffic is unencrypted — fine for testing,
> not for customer PII.

## API (whitelisted, role-gated)

- `peer_transfer.api.transfer.create_offer(root_doctype, root_names, ttl_secs)`
- `peer_transfer.api.transfer.connect_with_code(code)` → manifest + schema_diff
- `peer_transfer.api.transfer.get_preview_rows(code_or_name, offset, limit)`
- `peer_transfer.api.transfer.accept_and_import(code_or_name)` → receipt
- `peer_transfer.api.transfer.reject_transfer(code_or_name)`
- `peer_transfer.api.transfer.transfer_status(code_or_name)`

## Verified state

- Relay: lifecycle/TLL/upsert/paging/errors/concurrency/restart-persistence all
  PASS live; 2MiB-shadow-cap bug found and fixed (single enforced 32MB limit).
- App: schema self-compare + tamper detection, exact closure, created-path
  (3 created / 0 failed), idempotent re-accept, real-data loopback byte-identical,
  role gate enforced — all PASS, zero residue.
- Browser: Send → code → Receive → MATCH → grouped preview → Accept/Reject →
  Log, **0 pageerrors**.

## Roadmap

- Per-offer end-to-end encryption (relay stores opaque bytes only)
- Per-record conflict choice (today: skip + report)
- Supplier/Item roots on the same closure engine
- Relay token auth + rate hardening; background jobs for 100k+ bulk moves
- Transactions (submitted-doc + GL safety design — separate track)

## License

See `license.txt` (MIT stub; intended final: GPL-3.0 for the Frappe app,
MIT/Apache-2.0 for the relay).
