# peer-relay

Tiny store-and-forward envelope relay for Frappe-to-Frappe P2P document
transfer. The Frappe app speaks plain HTTPS+JSON (urllib); this server is a
dumb envelope store: numeric offer code -> manifest + ordered chunks, with TTL
and consume-on-ack delete. Single static-ish binary (~5MB), embedded SQLite.

## Run

docker compose up -d --build

Env: RELAY_PORT (8787), RELAY_DB (/data/relay.db),
RELAY_DEFAULT_TTL (3600), RELAY_MAX_TTL (86400). Min TTL 60s.

## Endpoints (all JSON; errors are {"error": msg}, never tracebacks)

- GET  /health
- POST /v1/offers {"ttl_secs":3600,"manifest":{...}} -> {"code":"482913",...}
- POST /v1/offers/{code}/chunks {"seq":0,"rows":[...]} -> {"ok":true,...}
- GET  /v1/offers/{code}/manifest -> {"code","manifest","chunks","expires_at"}
- GET  /v1/offers/{code}/rows?offset=0&limit=500 -> paged chunks
- POST /v1/offers/{code}/ack {"delete":true} -> consume-delete

## Why not iroh-relay / TURN here

iroh-relay speaks the iroh/QUIC wire protocol: a Python/Frappe client using
urllib cannot call it without a local sidecar daemon + FFI on every bench.
turn-rs/STUN is the wrong layer entirely (UDP NAT traversal for realtime
media, not HTTPS document transfer). This relay is 6 plain JSON endpoints
the Frappe app can call directly. If true NAT-traversal P2P is needed later,
revisit iroh with a sidecar per site.
