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
use std::path::PathBuf;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Mutex;
use std::time::Duration;
use tauri::{AppHandle, Emitter, Manager};
use tokio::io::{AsyncRead, AsyncReadExt, AsyncWrite, AsyncWriteExt};
use tokio::process::{Child, ChildStdin, ChildStdout};
use tokio::sync::watch;

/// Maximum frame body in bytes — 1 MiB, matching the Python side exactly
/// (`assistant.core.protocol.MAX_FRAME_BYTES`).
pub const MAX_FRAME_BYTES: usize = 1024 * 1024;

/// Overall budget for one `run.start` streaming session before the host
/// stops reading the run.
const RUN_DEADLINE: Duration = Duration::from_secs(15 * 60);
/// Round-trip budget for simple request/response methods.
const REQUEST_TIMEOUT: Duration = Duration::from_secs(10);
/// `system.status` probes the CUA daemon and the database, so it gets longer
/// than a plain round trip rather than reporting a healthy runtime as dead.
const STATUS_TIMEOUT: Duration = Duration::from_secs(20);
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

/// How to launch the sani-core sidecar, and the environment it runs in.
///
/// The sidecar is not a web server and has no login of its own: everything it
/// needs -- the one cloud credential, which database file, where the reviewed
/// CUA manifest lives -- arrives as environment variables the host resolves
/// from its own settings and the OS credential store.
pub struct SaniCoreConfig {
    pub command: PathBuf,
    pub args: Vec<String>,
    pub env: Vec<(String, String)>,
    pub working_dir: Option<PathBuf>,
}

/// The providers `assistant.settings.Settings` accepts. A value an older build
/// stored falls back to the one-key Sani story rather than killing startup.
fn model_provider(stored: &str) -> String {
    match stored {
        "openai" | "generic_openai_compatible" => stored.to_string(),
        _ => "openrouter".to_string(),
    }
}

impl SaniCoreConfig {
    /// Build the launch configuration from live app state. Errors only when
    /// there is no runtime to launch at all.
    pub fn resolve(app: &AppHandle) -> Result<Self, String> {
        let python = crate::setup::core_python()
            .ok_or_else(|| "sani-core runtime unavailable: no bundled interpreter".to_string())?;
        let data_dir = app
            .path()
            .app_data_dir()
            .map_err(|err| format!("no application-data directory: {err}"))?;
        std::fs::create_dir_all(&data_dir).map_err(|err| err.to_string())?;

        let mut env: Vec<(String, String)> = Vec::new();
        if let Some(root) = crate::setup::find_repo_root() {
            env.push((
                "PYTHONPATH".to_string(),
                root.join("src").to_string_lossy().into_owned(),
            ));
        }
        // Embedded SQLite only: the shipping path has no server to reach.
        env.push(("MEMORY_BACKEND".to_string(), "sqlite".to_string()));
        env.push((
            "SANI_DATA_DIR".to_string(),
            data_dir.to_string_lossy().into_owned(),
        ));
        env.push(("APP_ENV".to_string(), "development".to_string()));
        env.push(("LOG_LEVEL".to_string(), "INFO".to_string()));

        let artifacts = data_dir.join("artifacts");
        let _ = std::fs::create_dir_all(&artifacts);
        env.push((
            "CUA_ARTIFACT_DIR".to_string(),
            artifacts.to_string_lossy().into_owned(),
        ));
        // A missing manifest must not take the whole assistant down: the
        // sidecar would refuse to start. Computer control switches off and
        // says so loudly instead, where reasoning keeps working.
        match crate::setup::resource_path(app, "config/cua-capabilities.yaml") {
            Some(manifest) => {
                env.push(("CUA_ENABLED".to_string(), "true".to_string()));
                env.push((
                    "CUA_CAPABILITY_MANIFEST_PATH".to_string(),
                    manifest.to_string_lossy().into_owned(),
                ));
            }
            None => {
                log::error!(
                    "CUA capability manifest not found; starting sani-core without computer control"
                );
                env.push(("CUA_ENABLED".to_string(), "false".to_string()));
            }
        }

        let settings = crate::app_state::settings(app).read().clone();
        env.push((
            "MODEL_PROVIDER".to_string(),
            model_provider(&settings.reasoning_provider),
        ));
        if !settings.reasoning_model.trim().is_empty() {
            env.push(("MODEL_NAME".to_string(), settings.reasoning_model.clone()));
        }
        // Enabled deliberately: the sidecar then validates the JEV credential
        // at startup instead of failing on the first Velo turn.
        env.push(("VELO_ENABLED".to_string(), "true".to_string()));
        env.push(("VELO_PROVIDER".to_string(), settings.velo_provider.clone()));
        env.push(("VELO_JEV_MODEL".to_string(), settings.velo_model.clone()));

        // Credentials go to the child's environment only -- never argv, never
        // the log. Only the variable names are ever written out.
        for (service, variable) in [
            (
                crate::onboarding::OPENROUTER_KEY_SERVICE,
                "OPENROUTER_API_KEY",
            ),
            (crate::onboarding::TYPESAFE_KEY_SERVICE, "TYPESAFE_API_KEY"),
        ] {
            if let Some(key) = crate::settings::secret_read(service) {
                if !key.trim().is_empty() {
                    env.push((variable.to_string(), key));
                }
            }
        }
        log::info!(
            "sani-core env: {}",
            env.iter()
                .map(|(k, _)| k.as_str())
                .collect::<Vec<_>>()
                .join(",")
        );

        Ok(Self {
            command: python,
            args: vec!["-m".to_string(), "assistant.core".to_string()],
            env,
            // A deterministic working dir: the sidecar must not quietly pick up
            // a developer's `.env` and disagree with what the app configured.
            working_dir: Some(data_dir),
        })
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

    /// Resolves once cancellation has been requested. Stays pending forever
    /// once the control is gone, so a dropped run can never spin this loop.
    async fn wait_for_cancel(cancel: &mut watch::Receiver<bool>) {
        loop {
            if *cancel.borrow_and_update() {
                return;
            }
            if cancel.changed().await.is_err() {
                std::future::pending::<()>().await;
            }
        }
    }

    /// `run.start`: stream this run's event frames to `on_event` (raw
    /// `{"type":"event",...}` frames, unmodified) and resolve with the
    /// run's final Response.
    ///
    /// Cancellation is sent from here rather than from [`Self::request`]:
    /// this loop owns the writer while the run is live, so it is the only
    /// place that may legally write to stdin. The ack for the cancel request
    /// arrives on stdout and is dropped by the frame filter below; the run's
    /// own `cancelled` event and final Response are what end this loop.
    #[allow(clippy::too_many_arguments)]
    async fn run_stream(
        &mut self,
        agent_id: &str,
        text: &str,
        run_id: &str,
        thread_id: &str,
        mut on_event: impl FnMut(Value) + Send,
        deadline: tokio::time::Instant,
        mut cancel: watch::Receiver<bool>,
    ) -> Result<Value, String> {
        let id = next_request_id();
        let request = json!({
            "type": "request",
            "id": id,
            "method": "run.start",
            "params": {
                "agent_id": agent_id,
                "text": text,
                "run_id": run_id,
                "thread_id": thread_id,
            },
        });
        self.send(&request).await?;
        let mut cancel_sent = false;
        loop {
            tokio::select! {
                frame = self.next_frame(deadline) => {
                    let frame = frame?;
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
                _ = Self::wait_for_cancel(&mut cancel), if !cancel_sent => {
                    cancel_sent = true;
                    self.send(&json!({
                        "type": "request",
                        "id": next_request_id(),
                        "method": "run.cancel",
                        "params": {"run_id": run_id},
                    }))
                    .await?;
                }
            }
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
        let mut command = tokio::process::Command::new(&config.command);
        command
            .args(&config.args)
            .stdin(std::process::Stdio::piped())
            .stdout(std::process::Stdio::piped())
            // stdout is the IPC channel; sani-core logs to stderr only, so
            // inherit stderr to land its tracebacks in the app log.
            .stderr(std::process::Stdio::inherit())
            .kill_on_drop(true);
        for (key, value) in &config.env {
            command.env(key, value);
        }
        if let Some(dir) = &config.working_dir {
            command.current_dir(dir);
        }
        let mut child = command.spawn().map_err(|err| {
            format!(
                "failed to spawn sani-core ({} {}): {err}",
                config.command.display(),
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
    ///
    /// `cancel` is polled by the read loop itself: Esc during a live run has
    /// to reach the sidecar, and this is the only holder of stdin at that time.
    pub async fn start_run(
        &mut self,
        agent_id: &str,
        text: &str,
        run_id: &str,
        thread_id: &str,
        on_event: impl FnMut(Value) + Send,
        cancel: watch::Receiver<bool>,
    ) -> Result<Value, String> {
        if self.streaming {
            return Err("a run is already streaming".to_string());
        }
        // Guard rather than a trailing assignment: a future that is dropped
        // mid-run (task abort, panic) would otherwise leave the client
        // claiming a run is streaming forever.
        let _guard = StreamingGuard::new(&mut self.streaming);
        self.transport
            .run_stream(
                agent_id,
                text,
                run_id,
                thread_id,
                on_event,
                tokio::time::Instant::now() + RUN_DEADLINE,
                cancel,
            )
            .await
    }

    /// `run.cancel` -> `{"status":"cancelling"}`; the cancelled run then
    /// streams its own `cancelled` event and final `{"status":"cancelled"}`
    /// Response to whoever is reading (normally `start_run`).
    ///
    /// Only reachable between runs; a live run is cancelled through its
    /// [`RunControl`], which this client hands out when the run starts.
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

// -------------------------------------------------------------------- guard

/// Clears the client's streaming flag on drop, so a run that ends by panic or
/// by its task being aborted cannot strand the sidecar as permanently busy.
struct StreamingGuard<'a>(&'a mut bool);

impl<'a> StreamingGuard<'a> {
    /// Claim the flag; the guard releases it on drop, however the run ends.
    fn new(flag: &'a mut bool) -> Self {
        *flag = true;
        Self(flag)
    }
}

impl Drop for StreamingGuard<'_> {
    fn drop(&mut self) {
        *self.0 = false;
    }
}

// --------------------------------------------------------------- run control

/// A run the host may cancel while it is still streaming.
///
/// The run id is chosen by the host and handed to the sidecar, so this exists
/// from the moment the run starts rather than only after its first frame.
#[derive(Clone)]
pub struct RunControl {
    run_id: String,
    cancel: watch::Sender<bool>,
}

impl RunControl {
    pub fn run_id(&self) -> &str {
        &self.run_id
    }

    fn new(run_id: String) -> (Self, watch::Receiver<bool>) {
        let (cancel, receiver) = watch::channel(false);
        (Self { run_id, cancel }, receiver)
    }

    /// Ask the live run to stop. The run's own read loop writes the framed
    /// request; this only signals it to.
    pub fn request_cancel(&self) {
        self.cancel.send_replace(true);
    }
}

// --------------------------------------------------------------------- state

/// Managed state: the optional live sidecar connection and its current run.
///
/// A std Mutex is fine here because no lock is ever held across an await:
/// commands `take()` the client out, await on it, then put it back.
#[derive(Default)]
pub struct SaniCoreState {
    client: Mutex<Option<SaniCoreClient>>,
    live: Mutex<Option<RunControl>>,
}

fn take_client(app: &AppHandle) -> Result<Option<SaniCoreClient>, String> {
    let state = app.state::<SaniCoreState>();
    let mut guard = state
        .client
        .lock()
        .map_err(|err| format!("sani-core state poisoned: {err}"))?;
    Ok(guard.take())
}

fn restore_client(app: &AppHandle, client: SaniCoreClient) {
    let state = app.state::<SaniCoreState>();
    if let Ok(mut guard) = state.client.lock() {
        *guard = Some(client);
    };
}

fn running_client(app: &AppHandle) -> Result<SaniCoreClient, String> {
    take_client(app)?.ok_or_else(|| "sani-core is not running".to_string())
}

fn live_control(app: &AppHandle) -> Option<RunControl> {
    app.state::<SaniCoreState>()
        .live
        .lock()
        .ok()
        .and_then(|guard| guard.clone())
}

// ------------------------------------------------------------------ commands

/// Spawn the sani-core sidecar and remember it. If one is already running
/// it is shut down cleanly and replaced (the sidecar is single-owner).
#[tauri::command]
pub async fn core_start(app: AppHandle) -> Result<Value, String> {
    let config = SaniCoreConfig::resolve(&app)?;
    let client = SaniCoreClient::spawn(&config).await?;
    let state = app.state::<SaniCoreState>();
    let previous = {
        let mut guard = state
            .client
            .lock()
            .map_err(|err| format!("sani-core state poisoned: {err}"))?;
        guard.replace(client)
    };
    if let Some(previous) = previous {
        log::warn!("core_start while sani-core was already running; restarting it");
        previous.shutdown().await;
    }
    log::info!("sani-core sidecar started");
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

/// `system.status`: the sidecar's own view of its subsystems.
#[tauri::command]
pub async fn core_status(app: AppHandle) -> Result<Value, String> {
    let mut client = running_client(&app)?;
    let result = client
        .request(
            "system.status",
            json!({}),
            STATUS_TIMEOUT.max(REQUEST_TIMEOUT),
        )
        .await;
    restore_client(&app, client);
    result
}

/// Run one agent turn, forwarding every raw event frame to the frontend as
/// `sani://core-event`, and resolve with the run's final result.
///
/// Shared by the `core_run` command and the voice/text dispatch in
/// `app_state`, so both drive the sidecar the same way.
pub async fn run_turn(
    app: &AppHandle,
    agent_id: &str,
    text: &str,
    thread_id: &str,
    mut on_event: impl FnMut(Value) + Send,
) -> Result<Value, String> {
    let mut client = running_client(app)?;
    let run_id = uuid::Uuid::new_v4().simple().to_string();
    let (control, cancel) = RunControl::new(run_id.clone());
    {
        let state = app.state::<SaniCoreState>();
        let mut guard = state
            .live
            .lock()
            .map_err(|_| "sani-core run control poisoned".to_string())?;
        if guard.is_some() {
            restore_client(app, client);
            return Err("a run is already streaming".to_string());
        }
        *guard = Some(control);
    }
    let result = client
        .start_run(
            agent_id,
            text,
            &run_id,
            thread_id,
            move |frame| {
                on_event(frame);
            },
            cancel,
        )
        .await;
    if let Ok(mut guard) = app.state::<SaniCoreState>().live.lock() {
        *guard = None;
    }
    restore_client(app, client);
    result
}

/// Start one agent run, emitting raw event frames to the frontend.
#[tauri::command]
pub async fn core_run(
    app: AppHandle,
    agent_id: String,
    text: String,
    thread_id: Option<String>,
) -> Result<Value, String> {
    let emitter = app.clone();
    run_turn(
        &app,
        &agent_id,
        &text,
        &thread_id.unwrap_or_default(),
        move |frame| {
            if let Err(err) = emitter.emit(CORE_EVENT, frame) {
                log::warn!("sani-core: emit {CORE_EVENT} failed: {err}");
            }
        },
    )
    .await
}

/// Cancel a run. Works while the run is still streaming: the live control
/// signals its read loop, which owns the writer. Falls back to a plain
/// `run.cancel` round trip for a run id the sidecar knows but the host has
/// already stopped reading.
#[tauri::command]
pub async fn core_cancel(app: AppHandle, run_id: String) -> Result<Value, String> {
    if let Some(control) = live_control(&app) {
        if control.run_id() == run_id {
            control.request_cancel();
            return Ok(json!({"status": "cancelling", "run_id": run_id}));
        }
    }
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

/// Apply persisted launch inputs to sani-core. The caller must already have
/// rejected active runs: replacing a child process while it streams would
/// break a user turn. This function does not attempt any provider fallback.
pub async fn reload_for_settings(app: AppHandle) -> Result<(), String> {
    if is_run_live(&app) {
        return Err("a run is active".into());
    }
    core_stop(app.clone()).await?;
    core_start(app.clone()).await?;
    core_ping(app).await.map(|_| ())
}

// --------------------------------------------------------------- supervision

/// Frontend event carrying whether the assistant runtime is reachable.
pub const RUNTIME_STATUS: &str = "sani://agent-status";

/// How often the supervisor checks the sidecar.
const SUPERVISOR_INTERVAL: Duration = Duration::from_secs(20);

fn has_client(app: &AppHandle) -> bool {
    app.state::<SaniCoreState>()
        .client
        .lock()
        .map(|guard| guard.is_some())
        .unwrap_or(false)
}

/// True while a run is streaming.
pub fn is_run_live(app: &AppHandle) -> bool {
    live_control(app).is_some()
}

/// Cancel the currently running turn, if any. Returns false when nothing is
/// live, which is how the caller distinguishes "stopped" from "nothing to stop".
pub fn cancel_current_run(app: &AppHandle) -> bool {
    match live_control(app) {
        Some(control) => {
            control.request_cancel();
            true
        }
        None => false,
    }
}

/// Start the sidecar for a normal session and keep it running.
///
/// Replaces the old localhost health probe: the assistant runtime is a private
/// child process now, so "reachable" means spawned and answering a round trip.
/// A live run is never disturbed -- the client is deliberately parked while its
/// run streams, so probing then would falsely report the runtime as dead and
/// spawn a second sidecar.
pub fn spawn_supervisor(app: AppHandle) {
    std::thread::spawn(move || {
        let mut last_online: Option<bool> = None;
        loop {
            std::thread::sleep(SUPERVISOR_INTERVAL);
            if is_run_live(&app) {
                continue;
            }
            let online = match tauri::async_runtime::block_on(ensure_running(app.clone())) {
                Ok(()) => true,
                Err(err) => {
                    log::warn!("sani-core supervisor: {err}");
                    false
                }
            };
            if last_online != Some(online) {
                last_online = Some(online);
                let _ = app.emit(RUNTIME_STATUS, online);
            }
        }
    });
}

async fn ensure_running(app: AppHandle) -> Result<(), String> {
    if !has_client(&app) {
        log::warn!("sani-core is not running; starting it");
        core_start(app.clone()).await?;
    }
    core_ping(app).await.map(|_| ())
}

/// Spawn the sidecar at startup, logging rather than failing the launch: the
/// overlays must still come up so the user can see what is wrong.
pub fn start_at_startup(app: &AppHandle) {
    let handle = app.clone();
    tauri::async_runtime::spawn(async move {
        match core_start(handle.clone()).await {
            Ok(_) => spawn_supervisor(handle),
            Err(err) => {
                log::error!("sani-core could not start: {err}");
                let _ = handle.emit(RUNTIME_STATUS, false);
                spawn_supervisor(handle);
            }
        }
    });
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
            // The host names the run and supplies the conversation thread.
            assert_eq!(request["params"]["run_id"], "run-1");
            assert_eq!(request["params"]["thread_id"], "conv-9");
            write_frame_to(
                &mut server_io,
                &json!({"type": "event", "run_id": "run-1", "agent_id": "deep", "kind": "agent.token", "data": {"text": "hi"}}),
            )
            .await;
            write_frame_to(
                &mut server_io,
                &json!({"type": "event", "run_id": "run-1", "agent_id": "deep", "kind": "agent.cancelled", "data": {}}),
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
        let (_cancel_tx, cancel_rx) = watch::channel(false);
        let result = transport
            .run_stream(
                "deep",
                "hello",
                "run-1",
                "conv-9",
                |frame| events.push(frame),
                tokio::time::Instant::now() + FAKE_TIMEOUT,
                cancel_rx,
            )
            .await
            .unwrap();
        assert_eq!(result["status"], "cancelled");
        assert_eq!(events.len(), 2);
        assert_eq!(events[0]["kind"], "agent.token");
        assert_eq!(events[0]["agent_id"], "deep");
        assert_eq!(events[1]["kind"], "agent.cancelled");
        server.await.unwrap();
    }

    /// A cancel requested mid-stream must reach the sidecar as a `run.cancel`
    /// frame, and the loop must keep reading until the run's own Response --
    /// that is what makes Esc work during a live run.
    #[tokio::test]
    async fn run_stream_sends_cancel_and_keeps_reading_until_the_final_response() {
        let (client_io, mut server_io) = duplex(64 * 1024);
        let (client_read, client_write) = split(client_io);
        let mut transport = CoreTransport::new(client_read, client_write);
        let server = tokio::spawn(async move {
            let start = read_frame(&mut server_io).await.unwrap().unwrap();
            // The peer waits until it sees the cancellation it was asked for.
            let cancel = tokio::time::timeout(FAKE_TIMEOUT, read_frame(&mut server_io))
                .await
                .expect("no run.cancel arrived")
                .unwrap()
                .unwrap();
            assert_eq!(cancel["method"], "run.cancel");
            assert_eq!(cancel["params"]["run_id"], "run-9");
            write_frame_to(
                &mut server_io,
                &json!({"type": "response", "id": cancel["id"], "ok": true,
                        "result": {"status": "cancelling"}, "error": ""}),
            )
            .await;
            write_frame_to(
                &mut server_io,
                &json!({"type": "event", "run_id": "run-9", "agent_id": "velo",
                        "kind": "agent.cancelled", "data": {}}),
            )
            .await;
            write_frame_to(
                &mut server_io,
                &json!({"type": "response", "id": start["id"], "ok": true,
                        "result": {"status": "cancelled"}, "error": ""}),
            )
            .await;
        });
        let (cancel_tx, cancel_rx) = watch::channel(false);
        cancel_tx.send_replace(true);
        let mut events = Vec::new();
        let result = transport
            .run_stream(
                "velo",
                "open chrome",
                "run-9",
                "",
                |frame| events.push(frame),
                tokio::time::Instant::now() + FAKE_TIMEOUT,
                cancel_rx,
            )
            .await
            .unwrap();
        assert_eq!(result["status"], "cancelled");
        // The cancel ack (a response for a different id) is filtered out; only
        // the run's own event frames are forwarded.
        assert_eq!(events.len(), 1);
        assert_eq!(events[0]["kind"], "agent.cancelled");
        server.await.unwrap();
    }

    #[tokio::test]
    async fn run_stream_times_out_at_deadline() {
        let (client_io, _server_io) = duplex(64 * 1024); // nobody answers
        let (client_read, client_write) = split(client_io);
        let mut transport = CoreTransport::new(client_read, client_write);
        let (_cancel_tx, cancel_rx) = watch::channel(false);
        // The deadline is already spent: the read loop gives up immediately.
        let err = transport
            .run_stream(
                "deep",
                "hello",
                "r",
                "",
                |_| {},
                tokio::time::Instant::now(),
                cancel_rx,
            )
            .await
            .unwrap_err();
        assert!(err.contains("did not respond in time"));
    }
}
