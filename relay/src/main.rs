// peer-relay: tiny store-and-forward envelope relay for Frappe P2P transfer.
// Dumb store: manifest + ordered chunks under a numeric offer code.
// TTL enforced, consume-delete on ack. Errors are always JSON, never traceback.
use axum::{
    extract::{Path, Query, State},
    http::StatusCode,
    response::{IntoResponse, Json, Response},
    routing::{get, post},
    Router,
};
use rand::Rng;
use rusqlite::{params, Connection};
use serde::Deserialize;
use serde_json::{json, Value};
use std::collections::HashMap;
use std::env;
use std::sync::{Arc, Mutex};
use tower_http::limit::RequestBodyLimitLayer;

#[derive(Clone)]
struct AppState {
    db: Arc<Mutex<Connection>>,
    default_ttl: i64,
    max_ttl: i64,
}

fn now_secs() -> i64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs() as i64)
        .unwrap_or(0)
}

fn err(status: StatusCode, msg: impl Into<String>) -> Response {
    (status, Json(json!({ "error": msg.into() }))).into_response()
}

fn valid_code(code: &str) -> bool {
    code.len() == 6 && code.bytes().all(|b| b.is_ascii_digit())
}

async fn health() -> impl IntoResponse {
    Json(json!({ "ok": true, "service": "peer-relay", "version": "0.1.0" }))
}
fn init_db(conn: &Connection) {
    conn.execute_batch(
        "PRAGMA journal_mode = WAL;",
    )
    .expect("db init failed");
    conn.execute_batch(
        "CREATE TABLE IF NOT EXISTS offers (
           code TEXT PRIMARY KEY,
           manifest TEXT NOT NULL DEFAULT '{}',
           created_at INTEGER NOT NULL,
           expires_at INTEGER NOT NULL,
           consumed INTEGER NOT NULL DEFAULT 0
         );",
    )
    .expect("db init failed");
    conn.execute_batch(
        "CREATE TABLE IF NOT EXISTS chunks (
           code TEXT NOT NULL REFERENCES offers(code) ON DELETE CASCADE,
           seq INTEGER NOT NULL,
           body TEXT NOT NULL,
           PRIMARY KEY (code, seq)
         );",
    )
    .expect("db init failed");
    conn.execute_batch(
        "CREATE INDEX IF NOT EXISTS idx_offers_expires ON offers(expires_at);",
    )
    .expect("db init failed");
    conn.execute_batch("PRAGMA foreign_keys = ON;").expect("db init failed");
}

fn live_offer(conn: &Connection, code: &str) -> Option<(String, i64)> {
    let row: (String, i64) = conn
        .query_row(
            "SELECT manifest, expires_at FROM offers WHERE code = ?1",
            params![code],
            |r| Ok((r.get(0)?, r.get(1)?)),
        )
        .ok()?;
    let (manifest, expires_at) = row;
    if expires_at <= now_secs() {
        let _ = conn.execute("DELETE FROM offers WHERE code = ?1", params![code]);
        return None;
    }
    Some((manifest, expires_at))
}
#[derive(Deserialize)]
struct CreateOffer {
    ttl_secs: Option<i64>,
    manifest: Option<Value>,
}

async fn create_offer(State(st): State<AppState>, Json(req): Json<CreateOffer>) -> impl IntoResponse {
    let ttl = req.ttl_secs.unwrap_or(st.default_ttl).clamp(60, st.max_ttl);
    let manifest = req.manifest.unwrap_or(Value::Null);
    let mstr = serde_json::to_string(&manifest).unwrap_or_else(|_| "null".to_string());
    let now = now_secs();
    let exp = now + ttl;
    let db = match st.db.lock() {
        Ok(g) => g,
        Err(_) => return err(StatusCode::INTERNAL_SERVER_ERROR, "db busy"),
    };
    let mut rng = rand::thread_rng();
    let mut code = String::new();
    for _ in 0..25 {
        let cand = rng.gen_range(100_000..1_000_000).to_string();
        let exists: bool = db
            .query_row("SELECT 1 FROM offers WHERE code = ?1", params![cand], |_| Ok(true))
            .unwrap_or(false);
        if !exists {
            code = cand;
            break;
        }
    }
    if code.is_empty() {
        return err(StatusCode::SERVICE_UNAVAILABLE, "could not allocate offer code, retry");
    }
    match db.execute(
        "INSERT INTO offers (code, manifest, created_at, expires_at) VALUES (?1, ?2, ?3, ?4)",
        params![code, mstr, now, exp],
    ) {
        Ok(_) => Json(json!({ "code": code, "expires_at": exp, "ttl_secs": ttl })).into_response(),
        Err(_) => err(StatusCode::INTERNAL_SERVER_ERROR, "could not store offer"),
    }
}

#[derive(Deserialize)]
struct PutChunk {
    seq: Option<i64>,
    rows: Option<Value>,
}

async fn put_chunk(
    State(st): State<AppState>,
    Path(code): Path<String>,
    Json(req): Json<PutChunk>,
) -> impl IntoResponse {
    if !valid_code(&code) {
        return err(StatusCode::NOT_FOUND, "offer not found or expired");
    }
    let seq = match req.seq {
        Some(s) if s >= 0 => s,
        _ => return err(StatusCode::BAD_REQUEST, "field seq must be an integer >= 0"),
    };
    let body = match req.rows {
        Some(v) => serde_json::to_string(&v).unwrap_or_else(|_| "null".to_string()),
        None => return err(StatusCode::BAD_REQUEST, "field rows (JSON) is required"),
    };
    let db = match st.db.lock() {
        Ok(g) => g,
        Err(_) => return err(StatusCode::INTERNAL_SERVER_ERROR, "db busy"),
    };
    if live_offer(&db, &code).is_none() {
        return err(StatusCode::NOT_FOUND, "offer not found or expired");
    }
    let sql = "INSERT INTO chunks (code, seq, body) VALUES (?1, ?2, ?3)
         ON CONFLICT(code, seq) DO UPDATE SET body = excluded.body";
    match db.execute(sql, params![code, seq, body]) {
        Ok(_) => Json(json!({ "ok": true, "code": code, "seq": seq })).into_response(),
        Err(_) => err(StatusCode::INTERNAL_SERVER_ERROR, "could not store chunk"),
    }
}
async fn get_manifest(State(st): State<AppState>, Path(code): Path<String>) -> impl IntoResponse {
    if !valid_code(&code) {
        return err(StatusCode::NOT_FOUND, "offer not found or expired");
    }
    let db = match st.db.lock() {
        Ok(g) => g,
        Err(_) => return err(StatusCode::INTERNAL_SERVER_ERROR, "db busy"),
    };
    let (mstr, exp) = match live_offer(&db, &code) {
        Some(v) => v,
        None => return err(StatusCode::NOT_FOUND, "offer not found or expired"),
    };
    let n: i64 = db
        .query_row("SELECT COUNT(*) FROM chunks WHERE code = ?1", params![code], |r| r.get(0))
        .unwrap_or(0);
    let manifest: Value = serde_json::from_str(&mstr).unwrap_or(Value::Null);
    Json(json!({ "code": code, "manifest": manifest, "chunks": n, "expires_at": exp })).into_response()
}

async fn get_rows(
    State(st): State<AppState>,
    Path(code): Path<String>,
    Query(q): Query<HashMap<String, String>>,
) -> impl IntoResponse {
    if !valid_code(&code) {
        return err(StatusCode::NOT_FOUND, "offer not found or expired");
    }
    let offset: i64 = q.get("offset").and_then(|s| s.parse().ok()).unwrap_or(0).max(0);
    let limit: i64 = q.get("limit").and_then(|s| s.parse().ok()).unwrap_or(500).clamp(1, 2000);
    let db = match st.db.lock() {
        Ok(g) => g,
        Err(_) => return err(StatusCode::INTERNAL_SERVER_ERROR, "db busy"),
    };
    if live_offer(&db, &code).is_none() {
        return err(StatusCode::NOT_FOUND, "offer not found or expired");
    }
    let total: i64 = db
        .query_row("SELECT COUNT(*) FROM chunks WHERE code = ?1", params![code], |r| r.get(0))
        .unwrap_or(0);
    let mut stmt = match db.prepare(
        "SELECT seq, body FROM chunks WHERE code = ?1 ORDER BY seq ASC LIMIT ?2 OFFSET ?3",
    ) {
        Ok(s) => s,
        Err(_) => return err(StatusCode::INTERNAL_SERVER_ERROR, "could not read chunks"),
    };
    let mapped = stmt.query_map(params![code, limit, offset], |r| {
        let seq: i64 = r.get(0)?;
        let body: String = r.get(1)?;
        Ok((seq, body))
    });
    let mapped = match mapped {
        Ok(m) => m,
        Err(_) => return err(StatusCode::INTERNAL_SERVER_ERROR, "could not read chunks"),
    };
    let mut rows = Vec::new();
    for r in mapped.flatten() {
        let (seq, body) = r;
        let v: Value = serde_json::from_str(&body).unwrap_or(Value::Null);
        rows.push(json!({ "seq": seq, "rows": v }));
    }
    let resp = json!({ "code": code, "rows": rows, "total": total, "offset": offset, "limit": limit });
    Json(resp).into_response()
}
#[derive(Deserialize)]
struct Ack {
    delete: Option<bool>,
}

async fn ack(State(st): State<AppState>, Path(code): Path<String>, body: Option<Json<Ack>>) -> impl IntoResponse {
    if !valid_code(&code) {
        return err(StatusCode::NOT_FOUND, "offer not found or expired");
    }
    let delete = body.map(|b| b.delete.unwrap_or(true)).unwrap_or(true);
    let db = match st.db.lock() {
        Ok(g) => g,
        Err(_) => return err(StatusCode::INTERNAL_SERVER_ERROR, "db busy"),
    };
    if live_offer(&db, &code).is_none() {
        return err(StatusCode::NOT_FOUND, "offer not found or expired");
    }
    if delete {
        let _ = db.execute("DELETE FROM offers WHERE code = ?1", params![code]);
        Json(json!({ "ok": true, "code": code, "deleted": true })).into_response()
    } else {
        let _ = db.execute("UPDATE offers SET consumed = 1 WHERE code = ?1", params![code]);
        Json(json!({ "ok": true, "code": code, "deleted": false })).into_response()
    }
}

async fn sweep_loop(db: Arc<Mutex<Connection>>) {
    let mut t = tokio::time::interval(tokio::time::Duration::from_secs(60));
    loop {
        t.tick().await;
        if let Ok(conn) = db.lock() {
            let _ = conn.execute("DELETE FROM offers WHERE expires_at <= ?1", params![now_secs()]);
        }
    }
}

#[tokio::main]
async fn main() {
    let port: u16 = env::var("RELAY_PORT").ok().and_then(|s| s.parse().ok()).unwrap_or(8787);
    let db_path = env::var("RELAY_DB").unwrap_or_else(|_| "/data/relay.db".to_string());
    let default_ttl: i64 = env::var("RELAY_DEFAULT_TTL").ok().and_then(|s| s.parse().ok()).unwrap_or(3600);
    let max_ttl: i64 = env::var("RELAY_MAX_TTL").ok().and_then(|s| s.parse().ok()).unwrap_or(86400);
    if let Some(parent) = std::path::Path::new(&db_path).parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    let conn = Connection::open(&db_path).expect("open sqlite db");
    init_db(&conn);
    let db = Arc::new(Mutex::new(conn));
    let st = AppState { db: db.clone(), default_ttl, max_ttl };
    tokio::spawn(sweep_loop(db));
    let app = Router::new()
        .route("/health", get(health))
        .route("/v1/offers", post(create_offer))
        .route("/v1/offers/:code/chunks", post(put_chunk))
        .route("/v1/offers/:code/manifest", get(get_manifest))
        .route("/v1/offers/:code/rows", get(get_rows))
        .route("/v1/offers/:code/ack", post(ack))
        .layer(RequestBodyLimitLayer::new(32 * 1024 * 1024))
        .layer(axum::extract::DefaultBodyLimit::disable())
        .with_state(st);
    let addr = format!("0.0.0.0:{port}");
    let listener = tokio::net::TcpListener::bind(&addr).await.expect("bind");
    println!("peer-relay listening on {addr}, db={db_path}");
    axum::serve(listener, app).await.expect("serve");
}
