//! Client for the `sani-core` Python sidecar over private framed-JSON IPC.
//!
//! Sani master doc 13/14: the Tauri host owns one `sani-core` process and
//! speaks 4-byte big-endian length-prefixed JSON frames over its private
//! stdin/stdout. There is deliberately no localhost web server anywhere in
//! this stack.
//!
//! Wire contract (mirrors `src/assistant/core/protocol.py`):
//!   request  `{"type":"request","id":"<id>","method":"<m>","params":{...}}`
//!   response `{"type":"response","id":"<same id>","ok":true|false,"result":...,"error":"..."}`
//!   event    `{"type":"event","run_id":"...","kind":"...","data":{...}}` (interleaved on stdout)
//! The Python side terminates the session on EOF and on any malformed frame.
//!
//! Milestone constraints: a single client, a single run at a time. The
//! Python side supports concurrent runs interleaving on one stdout, but
//! this host has a single reader, so [`SaniCoreClient::request`] (and
//! therefore [`SaniCoreClient::cancel_run`]) must not be used while
//! [`SaniCoreClient::start_run`] is streaming: the run loop owns the one
//! stdout until the run's Response arrives.

use serde_json::{json, Value};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Mutex;
use std::time::Duration;
use tauri::{AppHandle, Emitter, Manager};
use tokio::io::{AsyncRead, AsyncReadExt, AsyncWrite, AsyncWriteExt};
use tokio::process::{Child, ChildStdin, ChildStdout};

/// Maximum frame body in bytes — 1 MiB, matching the Python side exactly
/// (`assistant.core.protocol.MAX_FRAME_BYTES`).
pub const MAX_FRAME_BYTES: usize = 1024 * 1024;

/// Overall budget for one `run.start` streaming session before the host
/// stops reading the run.
const RUN_DEADLINE: Duration = Duration::from_secs(15 * 60);
/// Round-trip budget for simple request/response methods.
const REQUEST_TIMEOUT: Duration = Duration::from_secs(10);
/// How long [`SaniCoreClient::shutdown`] waits for a clean sidecar exit
/// before killing it.
const SHUTDOWN_GRACE: Duration = Duration::from_secs(5);

/// Frontend event carrying raw sani-core event frames (the whole
/// `{"type":"event",...}` object, unmodified).
pub const CORE_EVENT: &str = "sani://core-event";

/// Process-unique request id source. The Python contract only needs a
/// string, but unique-across-restarts ids make stray frames traceable.
static NEXT_REQUEST_ID: AtomicU64 = AtomicU64::new(1);

fn next_request_id() -> String {
    format!("req-{}", NEXT_REQUEST_ID.fetch_add(1, Ordering::Relaxed))
}

/// How to launch the sani-core sidecar.
///
/// The `Default` is the dev invocation (`python -m assistant.core`), which
/// requires `python` on PATH and the `assistant` package importable (repo
/// checkout with `src` on PYTHONPATH). This becomes a packaged-binary
/// setting later (bundled sidecar resource); the override hook is why the
/// fields are public and the config is a plain struct.
pub struct SaniCoreConfig {
    pub command: String,
    pub args: Vec<String>,
}

impl Default for SaniCoreConfig {
    fn default() -> Self {
        Self {
            command: "python".to_string(),
            args: vec!["-m".to_string(), "assistant.core".to_string()],
        }
    }
}

// ------------------------------------------------------------------- framing

/// Serialize one frame: 4-byte big-endian length prefix + UTF-8 JSON body.
///
/// Frames larger than [`MAX_FRAME_BYTES`] violate the wire contract — the
/// Python peer terminates the session on receipt — so callers must not send
/// payloads that large.
pub fn encode_frame(payload: &Value) -> Vec<u8> {
    let body = serde_json::to_vec(payload).expect("serde_json::Value always serializes");
    let mut frame = Vec::with_capacity(4 + body.len());
    frame.extend_from_slice(&(body.len() as u32).to_be_bytes());
    frame.extend_from_slice(&body);
    frame
}

/// Read one framed JSON object; `Ok(None)` only on clean EOF between frames
/// (0 bytes read where a frame header was expected).
///
/// Mirrors `assistant.core.protocol.read_frame`: a partially received
/// header or body, an over-1MiB length, a non-JSON body, or a non-object
/// frame is an error, because the Python side drops the session on any of
/// these and the two peers must agree on when a connection is dead.
pub async fn read_frame<R: AsyncRead + Unpin>(reader: &mut R) -> tokio::io::Result<Option<Value>> {
    let mut header = [0u8; 4];
    let mut filled = 0usize;
    while filled < header.len() {
        let read = reader.read(&mut header[filled..]).await?;
        if read == 0 {
            if filled == 0 {
                return Ok(None);
            }
            return Err(tokio::io::Error::new(
                tokio::io::ErrorKind::UnexpectedEof,
                format!("truncated frame header: got {filled} of 4 bytes"),
            ));
        }
        filled += read;
    }
    let length = u32::from_be_bytes(header) as usize;
    if length > MAX_FRAME_BYTES {
        return Err(tokio::io::Error::new(
            tokio::io::ErrorKind::InvalidData,
            format!("frame too large: {length} bytes > {MAX_FRAME_BYTES}"),
        ));
    }
    let mut body = vec![0u8; length];
    let mut filled = 0usize;
    while filled < body.len() {
        let read = reader.read(&mut body[filled..]).await?;
        if read == 0 {
            return Err(tokio::io::Error::new(
                tokio::io::ErrorKind::UnexpectedEof,
                format!("truncated frame body: got {filled} of {} bytes", body.len()),
            ));
        }
        filled += read;
    }
    let value: Value = serde_json::from_slice(&body).map_err(|err| {
        tokio::io::Error::new(
            tokio::io::ErrorKind::InvalidData,
            format!("invalid JSON frame: {err}"),
        )
    })?;
    if !value.is_object() {
        return Err(tokio::io::Error::new(
            tokio::io::ErrorKind::InvalidData,
            "frame must be a JSON object",
        ));
    }
    Ok(Some(value))
}

/// Response frame -> `Ok(result)` or `Err(error message)`.
fn decode_response(frame: Value) -> Result<Value, String> {
    if frame.get("ok").and_then(Value::as_bool) == Some(true) {
        Ok(frame.get("result").cloned().unwrap_or(Value::Null))
    } else {
        Err(frame
            .get("error")
            .and_then(Value::as_str)
            .filter(|message| !message.is_empty())
            .unwrap_or("sani-core returned an error without a message")
            .to_string())
    }
}

// ----------------------------------------------------------------- transport

/// Framed request/response demux over generic async streams.
///
/// Split from [`SaniCoreClient`] exactly so the demux logic is testable
/// against `tokio::io::duplex` fakes with a scripted peer; the real client
/// plugs in the child's stdout/stdin. The child-spawning wrapper around it
/// stays thin and untested.
struct CoreTransport<R: AsyncRead + Unpin, W: AsyncWrite + Unpin> {
    reader: R,
    writer: W,
}

impl<R: AsyncRead + Unpin, W: AsyncWrite + Unpin> CoreTransport<R, W> {
    fn new(reader: R, writer: W) -> Self {
        Self { reader, writer }
    }

    async fn send(&mut self, frame: &Value) -> Result<(), String> {
        let bytes = encode_frame(frame);
        self.writer
            .write_all(&bytes)
            .await
            .map_err(|err| format!("sani-core write failed: {err}"))?;
        self.writer
            .flush()
            .await
            .map_err(|err| format!("sani-core flush failed: {err}"))?;
        Ok(())
    }

    /// Next frame before `deadline`, with every failure mapped to the
    /// String error contract. Clean EOF is reported verbatim as
    /// "sani-core exited".
    async fn next_frame(&mut self, deadline: tokio::time::Instant) -> Result<Value, String> {
        match tokio::time::timeout_at(deadline, read_frame(&mut self.reader)).await {
            Ok(Ok(Some(frame))) => Ok(frame),
            Ok(Ok(None)) => Err("sani-core exited".to_string()),
            Ok(Err(err)) => Err(format!("sani-core stream error: {err}")),
            Err(_) => Err("sani-core did not respond in time".to_string()),
        }
    }

    /// One request/response round trip, dropping any interleaved frames
    /// (events, stale responses) with a debug log.
    ///
    /// Only safe while no run is streaming — the caller owns that policy.
    async fn request(
        &mut self,
        method: &str,
        params: Value,
        timeout: Duration,
    ) -> Result<Value, String> {
        let id = next_request_id();
        let request = json!({"type": "request", "id": id, "method": method, "params": params});
        self.send(&request).await?;
        let deadline = tokio::time::Instant::now() + timeout;
        loop {
            let frame = self.next_frame(deadline).await?;
            if frame.get("type").and_then(Value::as_str) == Some("response")
                && frame.get("id").and_then(Value::as_str) == Some(id.as_str())
            {
                return decode_response(frame);
            }
            log::debug!("sani-core: dropping frame while awaiting {method}: {frame}");
        }
    }

    /// `run.start`: stream this run's event frames to `on_event` (raw
    /// `{"type":"event",...}` frames, unmodified) and resolve with the
    /// run's final Response.
    async fn run_stream(
        &mut self,
        agent_id: &str,
        text: &str,
        mut on_event: impl FnMut(Value) + Send,
        deadline: tokio::time::Instant,
    ) -> Result<Value, String> {
        let id = next_request_id();
        let request = json!({
            "type": "request",
            "id": id,
            "method": "run.start",
            "params": {"agent_id": agent_id, "text": text},
        });
        self.send(&request).await?;
        loop {
            let frame = self.next_frame(deadline).await?;
            let frame_type = frame.get("type").and_then(Value::as_str);
            if frame_type == Some("event") {
                on_event(frame);
                continue;
            }
            if frame_type == Some("response")
                && frame.get("id").and_then(Value::as_str) == Some(id.as_str())
            {
                return decode_response(frame);
            }
            log::debug!("sani-core: dropping unexpected frame during run: {frame}");
        }
    }
}

// -------------------------------------------------------------------- client

/// One sani-core sidecar process and its framed connection.
///
/// Milestone: single client, one run at a time (see the module docs).
/// Dropping a client closes stdin (the sidecar exits on EOF) and, because
/// the child is spawned with `kill_on_drop`, never leaks the process.
pub struct SaniCoreClient {
    child: Child,
    transport: CoreTransport<ChildStdout, ChildStdin>,
    streaming: bool,
}

impl SaniCoreClient {
    /// Spawn the sidecar with piped stdio. No handshake: the Python side
    /// sends nothing unrequested, so the first frame on the wire is ours.
    pub async fn spawn(config: &SaniCoreConfig) -> Result<Self, String> {
        let mut child = tokio::process::Command::new(&config.command)
            .args(&config.args)
            .stdin(std::process::Stdio::piped())
            .stdout(std::process::Stdio::piped())
            // stdout is the IPC channel; sani-core logs to stderr only, so
            // inherit stderr to land its tracebacks in the app log.
            .stderr(std::process::Stdio::inherit())
            .kill_on_drop(true)
            .spawn()
            .map_err(|err| {
                format!(
                    "failed to spawn sani-core ({} {}): {err}",
                    config.command,
                    config.args.join(" ")
                )
            })?;
        let stdin = child
            .stdin
            .take()
            .ok_or("sani-core spawned without stdin")?;
        let stdout = child
            .stdout
            .take()
            .ok_or("sani-core spawned without stdout")?;
        Ok(Self {
            child,
            transport: CoreTransport::new(stdout, stdin),
            streaming: false,
        })
    }

    /// Generic framed request/response round trip.
    ///
    /// Constraint: while a run is streaming, the single stdout belongs to
    /// the run loop — any request here would steal its event frames and
    /// final Response — so this is refused outright.
    pub async fn request(
        &mut self,
        method: &str,
        params: Value,
        timeout: Duration,
    ) -> Result<Value, String> {
        if self.streaming {
            return Err("a run is streaming; cancel or wait".to_string());
        }
        self.transport.request(method, params, timeout).await
    }

    /// `agents.list` -> the sidecar registry's agent descriptors.
    pub async fn list_agents(&mut self) -> Result<Vec<Value>, String> {
        let result = self
            .request("agents.list", json!({}), REQUEST_TIMEOUT)
            .await?;
        let agents = result
            .get("agents")
            .and_then(Value::as_array)
            .cloned()
            .ok_or_else(|| format!("unexpected agents.list result: {result}"))?;
        Ok(agents)
    }

    /// `run.start`: spawn one agent run, forwarding every raw event frame
    /// to `on_event` as it arrives, and resolve with the final result.
    ///
    /// Blocks until the run's Response arrives (overall deadline: 15
    /// minutes). EOF from the sidecar — clean or crashed — is
    /// `Err("sani-core exited")`. Refused while another run is streaming.
    pub async fn start_run(
        &mut self,
        agent_id: &str,
        text: &str,
        on_event: impl FnMut(Value) + Send,
    ) -> Result<Value, String> {
        if self.streaming {
            return Err("a run is already streaming".to_string());
        }
        self.streaming = true;
        let result = self
            .transport
            .run_stream(
                agent_id,
                text,
                on_event,
                tokio::time::Instant::now() + RUN_DEADLINE,
            )
            .await;
        self.streaming = false;
        result
    }

    /// `run.cancel` -> `{"status":"cancelling"}`; the cancelled run then
    /// streams its own `cancelled` event and final `{"status":"cancelled"}`
    /// Response to whoever is reading (normally `start_run`).
    ///
    /// Must be called between runs: it goes through [`Self::request`],
    /// whose streaming guard refuses calls while `start_run` owns the
    /// stream. In this milestone a run is always owned by `core_run`, so
    /// cancels land after the run loop has returned (e.g. after a timeout).
    pub async fn cancel_run(&mut self, run_id: &str) -> Result<Value, String> {
        self.request("run.cancel", json!({"run_id": run_id}), REQUEST_TIMEOUT)
            .await
    }

    /// Close stdin — the Python side exits on EOF — then wait briefly and
    /// kill the child only if it is still alive past the grace period.
    pub async fn shutdown(mut self) {
        drop(self.transport); // dropping stdin closes the pipe: EOF for sani-core
        match tokio::time::timeout(SHUTDOWN_GRACE, self.child.wait()).await {
            Ok(Ok(status)) => log::debug!("sani-core exited cleanly: {status}"),
            Ok(Err(err)) => log::warn!("sani-core wait failed: {err}"),
            Err(_) => {
                log::warn!("sani-core did not exit within {SHUTDOWN_GRACE:?}; killing");
                let _ = self.child.kill().await;
                let _ = self.child.wait().await;
            }
        }
    }
}

// --------------------------------------------------------------------- state

/// Managed state: the optional live sidecar connection.
///
/// A std Mutex is fine here because no lock is ever held across an await:
/// commands `take()` the client out, await on it, then put it back.
pub struct SaniCoreState(Mutex<Option<SaniCoreClient>>);

impl Default for SaniCoreState {
    fn default() -> Self {
        Self(Mutex::new(None))
    }
}

fn take_client(app: &AppHandle) -> Result<Option<SaniCoreClient>, String> {
    let state = app.state::<SaniCoreState>();
    let mut guard = state
        .0
        .lock()
        .map_err(|err| format!("sani-core state poisoned: {err}"))?;
    Ok(guard.take())
}

fn restore_client(app: &AppHandle, client: SaniCoreClient) {
    let state = app.state::<SaniCoreState>();
    if let Ok(mut guard) = state.0.lock() {
        *guard = Some(client);
    };
}

fn running_client(app: &AppHandle) -> Result<SaniCoreClient, String> {
    take_client(app)?.ok_or_else(|| "sani-core is not running".to_string())
}

// ------------------------------------------------------------------ commands

/// Spawn the sani-core sidecar and remember it. If one is already running
/// it is shut down cleanly and replaced (the sidecar is single-owner).
#[tauri::command]
pub async fn core_start(app: AppHandle) -> Result<Value, String> {
    let client = SaniCoreClient::spawn(&SaniCoreConfig::default()).await?;
    let state = app.state::<SaniCoreState>();
    let previous = {
        let mut guard = state
            .0
            .lock()
            .map_err(|err| format!("sani-core state poisoned: {err}"))?;
        guard.replace(client)
    };
    if let Some(previous) = previous {
        log::warn!("core_start while sani-core was already running; restarting it");
        previous.shutdown().await;
    }
    Ok(json!({"status": "started"}))
}

/// Shut the sidecar down and forget it. Safe to call when not running.
#[tauri::command]
pub async fn core_stop(app: AppHandle) -> Result<Value, String> {
    if let Some(client) = take_client(&app)? {
        client.shutdown().await;
    }
    Ok(json!({"status": "stopped"}))
}

/// List the sidecar's registered agents.
#[tauri::command]
pub async fn core_agents(app: AppHandle) -> Result<Value, String> {
    let mut client = running_client(&app)?;
    let result = client
        .list_agents()
        .await
        .map(|agents| json!({ "agents": agents }));
    restore_client(&app, client);
    result
}

/// Start one agent run. Streams each raw event frame to the frontend as
/// `sani://core-event` and resolves with the run's final result.
#[tauri::command]
pub async fn core_run(app: AppHandle, agent_id: String, text: String) -> Result<Value, String> {
    let mut client = running_client(&app)?;
    let emitter = app.clone();
    let result = client
        .start_run(&agent_id, &text, move |frame| {
            if let Err(err) = emitter.emit(CORE_EVENT, frame) {
                log::warn!("sani-core: emit {CORE_EVENT} failed: {err}");
            }
        })
        .await;
    restore_client(&app, client);
    result
}

/// Cancel a run by id. Only meaningful between runs in this milestone (see
/// [`SaniCoreClient::cancel_run`]).
#[tauri::command]
pub async fn core_cancel(app: AppHandle, run_id: String) -> Result<Value, String> {
    let mut client = running_client(&app)?;
    let result = client.cancel_run(&run_id).await;
    restore_client(&app, client);
    result
}

/// Health check: one `agents.list` round trip against the live sidecar.
#[tauri::command]
pub async fn core_ping(app: AppHandle) -> Result<Value, String> {
    let mut client = running_client(&app)?;
    let result = client
        .list_agents()
        .await
        .map(|agents| json!({"status": "ok", "agents": agents.len()}));
    restore_client(&app, client);
    result
}

// --------------------------------------------------------------------- tests
//
// The demux logic is tested against `tokio::io::duplex` with a scripted
// fake peer; the Child-spawning wrapper (`SaniCoreClient::spawn`) is thin
// and exercised only by the integration of a real sidecar later.

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Cursor;
    use tokio::io::duplex;
    use tokio::io::split;

    const FAKE_TIMEOUT: Duration = Duration::from_secs(5);

    async fn write_frame_to<W: AsyncWrite + Unpin>(writer: &mut W, payload: &Value) {
        writer.write_all(&encode_frame(payload)).await.unwrap();
        writer.flush().await.unwrap();
    }

    #[test]
    fn encode_frame_prefixes_big_endian_length() {
        let payload = json!({"hello": "world"});
        let frame = encode_frame(&payload);
        let (length, body) = frame.split_at(4);
        let expected = u32::from_be_bytes([length[0], length[1], length[2], length[3]]) as usize;
        assert_eq!(expected, frame.len() - 4);
        assert_eq!(serde_json::from_slice::<Value>(body).unwrap(), payload);
    }

    #[tokio::test]
    async fn read_frame_roundtrips_then_reports_eof() {
        let mut buffer = Vec::new();
        buffer.extend_from_slice(&encode_frame(&json!({"n": 1})));
        buffer.extend_from_slice(&encode_frame(&json!({"n": 2})));
        let mut reader = Cursor::new(buffer);
        assert_eq!(
            read_frame(&mut reader).await.unwrap().unwrap(),
            json!({"n": 1})
        );
        assert_eq!(
            read_frame(&mut reader).await.unwrap().unwrap(),
            json!({"n": 2})
        );
        assert!(read_frame(&mut reader).await.unwrap().is_none());
    }

    #[tokio::test]
    async fn read_frame_rejects_truncated_header() {
        let err = read_frame(&mut Cursor::new(vec![0, 0])).await.unwrap_err();
        assert_eq!(err.kind(), tokio::io::ErrorKind::UnexpectedEof);
    }

    #[tokio::test]
    async fn read_frame_rejects_truncated_body() {
        let mut buffer = Vec::new();
        buffer.extend_from_slice(&10u32.to_be_bytes());
        buffer.extend_from_slice(b"abc");
        let err = read_frame(&mut Cursor::new(buffer)).await.unwrap_err();
        assert_eq!(err.kind(), tokio::io::ErrorKind::UnexpectedEof);
    }

    #[tokio::test]
    async fn read_frame_rejects_oversize_length() {
        let mut buffer = Vec::new();
        buffer.extend_from_slice(&((MAX_FRAME_BYTES + 1) as u32).to_be_bytes());
        buffer.extend_from_slice(b"junk");
        let err = read_frame(&mut Cursor::new(buffer)).await.unwrap_err();
        assert_eq!(err.kind(), tokio::io::ErrorKind::InvalidData);
    }

    #[tokio::test]
    async fn read_frame_rejects_non_json_body() {
        let body = b"not json";
        let mut buffer = Vec::new();
        buffer.extend_from_slice(&(body.len() as u32).to_be_bytes());
        buffer.extend_from_slice(body);
        let err = read_frame(&mut Cursor::new(buffer)).await.unwrap_err();
        assert_eq!(err.kind(), tokio::io::ErrorKind::InvalidData);
    }

    #[tokio::test]
    async fn request_roundtrips_against_fake_peer() {
        let (client_io, mut server_io) = duplex(64 * 1024);
        let (client_read, client_write) = split(client_io);
        let mut transport = CoreTransport::new(client_read, client_write);
        let server = tokio::spawn(async move {
            let request = read_frame(&mut server_io).await.unwrap().unwrap();
            assert_eq!(request["method"], "agents.list");
            write_frame_to(
                &mut server_io,
                &json!({
                    "type": "response",
                    "id": request["id"],
                    "ok": true,
                    "result": {"agents": [{"id": "deep"}]},
                    "error": ""
                }),
            )
            .await;
        });
        let result = transport
            .request("agents.list", json!({}), FAKE_TIMEOUT)
            .await
            .unwrap();
        assert_eq!(result["agents"][0]["id"], "deep");
        server.await.unwrap();
    }

    #[tokio::test]
    async fn request_drops_interleaved_event_frames() {
        let (client_io, mut server_io) = duplex(64 * 1024);
        let (client_read, client_write) = split(client_io);
        let mut transport = CoreTransport::new(client_read, client_write);
        let server = tokio::spawn(async move {
            let request = read_frame(&mut server_io).await.unwrap().unwrap();
            write_frame_to(
                &mut server_io,
                &json!({"type": "event", "run_id": "r", "kind": "token", "data": {}}),
            )
            .await;
            write_frame_to(
                &mut server_io,
                &json!({
                    "type": "response",
                    "id": request["id"],
                    "ok": true,
                    "result": {"status": "cancelling"},
                    "error": ""
                }),
            )
            .await;
        });
        let result = transport
            .request("run.cancel", json!({"run_id": "r"}), FAKE_TIMEOUT)
            .await
            .unwrap();
        assert_eq!(result["status"], "cancelling");
        server.await.unwrap();
    }

    #[tokio::test]
    async fn request_maps_error_response_to_err() {
        let (client_io, mut server_io) = duplex(64 * 1024);
        let (client_read, client_write) = split(client_io);
        let mut transport = CoreTransport::new(client_read, client_write);
        let server = tokio::spawn(async move {
            let request = read_frame(&mut server_io).await.unwrap().unwrap();
            write_frame_to(
                &mut server_io,
                &json!({
                    "type": "response",
                    "id": request["id"],
                    "ok": false,
                    "result": null,
                    "error": "unknown method"
                }),
            )
            .await;
        });
        let err = transport
            .request("nope", json!({}), FAKE_TIMEOUT)
            .await
            .unwrap_err();
        assert!(err.contains("unknown method"));
        server.await.unwrap();
    }

    #[tokio::test]
    async fn request_reports_eof_when_sidecar_exits() {
        let (client_io, mut server_io) = duplex(64 * 1024);
        let (client_read, client_write) = split(client_io);
        let mut transport = CoreTransport::new(client_read, client_write);
        let server = tokio::spawn(async move {
            // Consume the request, then drop the peer: clean EOF downstream.
            let _ = read_frame(&mut server_io).await;
        });
        let err = transport
            .request("agents.list", json!({}), FAKE_TIMEOUT)
            .await
            .unwrap_err();
        assert_eq!(err, "sani-core exited");
        server.await.unwrap();
    }

    #[tokio::test]
    async fn run_stream_forwards_events_and_returns_final_result() {
        let (client_io, mut server_io) = duplex(64 * 1024);
        let (client_read, client_write) = split(client_io);
        let mut transport = CoreTransport::new(client_read, client_write);
        let server = tokio::spawn(async move {
            let request = read_frame(&mut server_io).await.unwrap().unwrap();
            assert_eq!(request["method"], "run.start");
            assert_eq!(request["params"]["agent_id"], "deep");
            assert_eq!(request["params"]["text"], "hello");
            write_frame_to(
                &mut server_io,
                &json!({"type": "event", "run_id": "run-1", "kind": "token", "data": {"text": "hi"}}),
            )
            .await;
            write_frame_to(
                &mut server_io,
                &json!({"type": "event", "run_id": "run-1", "kind": "cancelled", "data": {}}),
            )
            .await;
            write_frame_to(
                &mut server_io,
                &json!({
                    "type": "response",
                    "id": request["id"],
                    "ok": true,
                    "result": {"status": "cancelled"},
                    "error": ""
                }),
            )
            .await;
        });
        let mut events = Vec::new();
        let result = transport
            .run_stream(
                "deep",
                "hello",
                |frame| events.push(frame),
                tokio::time::Instant::now() + FAKE_TIMEOUT,
            )
            .await
            .unwrap();
        assert_eq!(result["status"], "cancelled");
        assert_eq!(events.len(), 2);
        assert_eq!(events[0]["kind"], "token");
        assert_eq!(events[1]["kind"], "cancelled");
        server.await.unwrap();
    }

    #[tokio::test]
    async fn run_stream_times_out_at_deadline() {
        let (client_io, _server_io) = duplex(64 * 1024); // nobody answers
        let (client_read, client_write) = split(client_io);
        let mut transport = CoreTransport::new(client_read, client_write);
        // The deadline is already spent: the read loop gives up immediately.
        let err = transport
            .run_stream("deep", "hello", |_| {}, tokio::time::Instant::now())
            .await
            .unwrap_err();
        assert!(err.contains("did not respond in time"));
    }
}
