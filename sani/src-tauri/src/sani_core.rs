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
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
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
/// A frozen Python sidecar must unpack and import its private runtime before
/// it can answer its very first frame. This is deliberately bounded, but is
/// longer than a warm request so launch is not misclassified as failure.
const REGISTRY_BOOT_TIMEOUT: Duration = Duration::from_secs(30);
/// `system.status` probes the CUA daemon and the database, so it gets longer
/// than a plain round trip rather than reporting a healthy runtime as dead.
const STATUS_TIMEOUT: Duration = Duration::from_secs(20);
/// How long [`SaniCoreClient::shutdown`] waits for a clean sidecar exit
/// before killing it.
const SHUTDOWN_GRACE: Duration = Duration::from_secs(5);
/// A packaged CUA driver must create its private endpoint promptly.  This is
/// a startup bound, not permission approval: denied macOS permissions leave
/// the daemon alive and are reported honestly by the driver.
const CUA_BOOT_TIMEOUT: Duration = Duration::from_secs(10);
/// Ceiling on the read-only permission probe, so a wedged daemon cannot stall
/// the Computer Control page while Sani waits for an answer.
const DRIVER_PERMISSION_TIMEOUT: Duration = Duration::from_secs(3);
/// How long a foreign daemon is given to shut down politely before Sani
/// signals it directly.
const DRIVER_STOP_TIMEOUT: Duration = Duration::from_secs(4);
/// A driver respawn storm is a symptom, never a recovery: two bounded restarts
/// inside this window means the fault is not a stale child, and the second one
/// is refused so the failing reason reaches the UI instead of a herd of
/// sidecars. See `sani-core env` in the app log: 40 spawns inside 5 seconds.
const RECOVERY_COOLDOWN: Duration = Duration::from_secs(30);

/// How the embedded daemon is authorized, decided once per spawn.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum CuaPermissionMode {
    /// The reviewed `config/cua-capabilities.yaml` ceiling.
    Bounded,
    /// Approved architecture D1: no manifest ceiling, with the deterministic
    /// action-class gate owned by Sani instead of by the driver policy file.
    Standard,
}

impl CuaPermissionMode {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Bounded => "bounded",
            Self::Standard => "standard",
        }
    }
}

/// Staged activation: bounded mode stays in force until the batched install
/// that is allowed to consume a fresh macOS grant flips the setting. Reading it
/// only here keeps the decision single-sourced and impossible to change
/// mid-session, which would leave a running daemon on one mode and the UI
/// describing another.
pub fn staged_permission_mode(app: &AppHandle) -> CuaPermissionMode {
    if crate::app_state::settings(app)
        .read()
        .computer_control_standard_mode
    {
        CuaPermissionMode::Standard
    } else {
        CuaPermissionMode::Bounded
    }
}

/// A `ps` row reduced to what socket ownership can be judged from.
#[derive(Clone, Debug, PartialEq, Eq)]
struct ProcessRow {
    pid: u32,
    ppid: u32,
    args: String,
}

/// Parse `ps -eo pid=,ppid=,args=` output. Blank and header lines are skipped;
/// anything without two leading numbers is not a process row.
fn parse_process_rows(output: &str) -> Vec<ProcessRow> {
    output
        .lines()
        .filter_map(|line| {
            let mut fields = line.split_whitespace();
            let pid = fields.next()?.parse::<u32>().ok()?;
            let ppid = fields.next()?.parse::<u32>().ok()?;
            Some(ProcessRow {
                pid,
                ppid,
                args: line.split_whitespace().skip(2).collect::<Vec<_>>().join(" "),
            })
        })
        .collect()
}

/// Live daemons bound to *this* private socket that Sani did not spawn.
///
/// A driver outlives its host whenever Sani is killed rather than quit: `kill_on_drop`
/// only fires when the child handle is dropped, and macOS then reparents the
/// daemon to launchd while it keeps listening on the unlinked inode of the
/// socket path. Sani's next launch unlinks the path, spawns a second daemon, and
/// both answer -- the older one carries the previous build's code identity.
/// Only `serve` rows match, so the sidecar's own `mcp`/`call` clients are never
/// mistaken for a daemon.
fn foreign_driver_pids(rows: &[ProcessRow], socket: &Path, owned: Option<u32>) -> Vec<u32> {
    let socket_path = socket.to_string_lossy();
    let me = std::process::id();
    rows.iter()
        .filter(|row| {
            row.pid != me
                && Some(row.pid) != owned
                && row.args.contains("cua-driver")
                && row.args.contains("serve")
                && row.args.contains("--embedded")
                && row.args.contains(socket_path.as_ref())
        })
        .map(|row| row.pid)
        .collect()
}

/// Ask the daemon to stop, then signal anything still alive, then clear the
/// endpoint. Returns the pids that were gone by the end.
///
/// `polite_stop` must be false when Sani's own daemon shares the socket: the
/// driver's `stop` verb addresses the endpoint, not a pid, so using it there
/// would shut down the healthy generation Sani is running.
async fn reclaim_socket_endpoint(
    command: &Path,
    socket: &Path,
    pids: &[u32],
    polite_stop: bool,
    clear_endpoint: bool,
) -> (Vec<u32>, Vec<u32>) {
    if pids.is_empty() {
        if clear_endpoint {
            let _ = std::fs::remove_file(socket);
        }
        return (Vec::new(), Vec::new());
    }
    log::warn!(
        "reclaiming Sani's CUA socket from {} foreign daemon(s): {:?}",
        pids.len(),
        pids
    );
    if polite_stop {
        let stop = tokio::time::timeout(
            DRIVER_STOP_TIMEOUT,
            tokio::process::Command::new(command)
                .args(["stop", "--socket", socket.to_string_lossy().as_ref()])
                .stdin(std::process::Stdio::null())
                .stdout(std::process::Stdio::null())
                .stderr(std::process::Stdio::null())
                .output(),
        )
        .await;
        if let Err(err) = stop {
            log::debug!("polite CUA daemon stop did not finish in time: {err}");
        }
    }
    let mut reclaimed = Vec::new();
    let mut surviving = Vec::new();
    for pid in pids {
        if wait_for_process_to_exit(*pid, Duration::from_millis(800)).await {
            reclaimed.push(*pid);
            continue;
        }
        signal_process(*pid, "TERM");
        if wait_for_process_to_exit(*pid, Duration::from_secs(1)).await {
            reclaimed.push(*pid);
            continue;
        }
        signal_process(*pid, "KILL");
        if wait_for_process_to_exit(*pid, Duration::from_secs(1)).await {
            reclaimed.push(*pid);
        } else {
            surviving.push(*pid);
        }
    }
    if clear_endpoint {
        let _ = std::fs::remove_file(socket);
    }
    (reclaimed, surviving)
}

fn signal_process(pid: u32, signal: &str) {
    match std::process::Command::new("/bin/kill")
        .arg(format!("-{signal}"))
        .arg(pid.to_string())
        .output()
    {
        Ok(_) => {}
        Err(err) => log::debug!("kill -{signal} {pid} could not be run: {err}"),
    }
}

async fn wait_for_process_to_exit(pid: u32, budget: Duration) -> bool {
    let deadline = tokio::time::Instant::now() + budget;
    loop {
        if !process_is_alive(pid) {
            return true;
        }
        if tokio::time::Instant::now() >= deadline {
            return false;
        }
        tokio::time::sleep(Duration::from_millis(100)).await;
    }
}

fn process_is_alive(pid: u32) -> bool {
    std::process::Command::new("/bin/kill")
        .arg("-0")
        .arg(pid.to_string())
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .status()
        .map(|status| status.success())
        .unwrap_or(false)
}

fn live_driver_pids() -> Vec<ProcessRow> {
    let output = std::process::Command::new("/bin/ps")
        .args(["-eo", "pid=,ppid=,args="])
        .output();
    match output {
        Ok(output) => parse_process_rows(&String::from_utf8_lossy(&output.stdout)),
        Err(err) => {
            log::warn!("could not enumerate processes to claim the CUA socket: {err}");
            Vec::new()
        }
    }
}


/// A filesystem entry at the private endpoint is not enough: a crashed CUA
/// daemon can leave its Unix socket behind.  Treat the driver as live only
/// after the endpoint accepts a local connection.
async fn embedded_driver_socket_ready(socket: &Path) -> bool {
    matches!(
        tokio::time::timeout(
            Duration::from_secs(1),
            tokio::net::UnixStream::connect(socket),
        )
        .await,
        Ok(Ok(_))
    )
}

fn embedded_driver_needs_recovery(child_running: bool, socket_ready: bool) -> bool {
    !child_running || !socket_ready
}

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
    cua: Option<EmbeddedCuaConfig>,
}

/// A CUA daemon embedded in Sani's own macOS responsibility chain.
///
/// The driver must be a **direct child** of Sani. In embedded mode macOS
/// attributes Accessibility and Screen Recording through the host's
/// responsibility chain — `check_permissions` reports the *host's* grants and
/// states plainly that "no separate driver grant exists or is needed".
/// Launching it through LaunchServices instead reparents it to launchd, which
/// severs that chain and leaves an unattributable daemon, so `open` must not be
/// used here.
///
/// Sani ships the vendor's `CuaDriver.app` (Developer ID signed) rather than a
/// loose binary so the helper also carries the cursor-theme resource and a real
/// bundle, but it is still spawned as a child, with `--socket` naming Sani's
/// private endpoint so no global or standard-mode daemon is ever revived.
#[derive(Clone)]
struct EmbeddedCuaConfig {
    command: PathBuf,
    socket: PathBuf,
    manifest: PathBuf,
    mode: CuaPermissionMode,
}

struct EmbeddedCuaDriver {
    child: Child,
    socket: PathBuf,
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
        let bundled_core = packaged_core(app);
        let is_bundled_core = bundled_core.is_some();
        let python = if bundled_core.is_none() && !running_from_bundle() {
            crate::setup::core_python()
        } else {
            None
        };
        let command = bundled_core.or(python).ok_or_else(|| {
            "sani-core runtime unavailable: bundled core component missing".to_string()
        })?;
        let data_dir = app
            .path()
            .app_data_dir()
            .map_err(|err| format!("no application-data directory: {err}"))?;
        std::fs::create_dir_all(&data_dir).map_err(|err| err.to_string())?;

        let mut env: Vec<(String, String)> = Vec::new();
        if !is_bundled_core {
            if let Some(root) = crate::setup::find_repo_root() {
                env.push((
                    "PYTHONPATH".to_string(),
                    root.join("src").to_string_lossy().into_owned(),
                ));
            }
        }
        // Embedded SQLite only: the shipping path has no server to reach.
        env.push(("MEMORY_BACKEND".to_string(), "sqlite".to_string()));
        env.push((
            "SANI_DATA_DIR".to_string(),
            data_dir.to_string_lossy().into_owned(),
        ));
        env.push(("APP_ENV".to_string(), "development".to_string()));
        env.push(("LOG_LEVEL".to_string(), "INFO".to_string()));
        // The embedded CUA daemon inherits the GUI host's TCC identity, while
        // the frozen Python core is a separate executable.  Pass the native
        // host's read-only preflight so the core does not mistake its own
        // child identity for the user's Sani grant.
        env.push((
            "SANI_HOST_ACCESSIBILITY_PERMISSION".to_string(),
            crate::system_permissions::accessibility()
                .as_str()
                .to_string(),
        ));
        env.push((
            "SANI_HOST_SCREEN_RECORDING_PERMISSION".to_string(),
            crate::system_permissions::screen_recording()
                .as_str()
                .to_string(),
        ));

        let artifacts = data_dir.join("artifacts");
        let _ = std::fs::create_dir_all(&artifacts);
        env.push((
            "CUA_ARTIFACT_DIR".to_string(),
            artifacts.to_string_lossy().into_owned(),
        ));
        let mode = staged_permission_mode(app);
        let cua = match packaged_cua_driver(app) {
            None => {
                log::error!(
                    "embedded CUA driver missing; starting sani-core without computer control"
                );
                None
            }
            Some(command) => {
                let manifest = crate::setup::resource_path(app, "config/cua-capabilities.yaml");
                match manifest {
                    Some(manifest) => Some(EmbeddedCuaConfig {
                        command,
                        socket: data_dir.join("cua-driver.sock"),
                        manifest,
                        mode,
                    }),
                    // Standard mode needs no manifest: the policy file *is* the
                    // bounded ceiling, and approved architecture D1 moves that
                    // authority into Sani's own action-class gate.
                    None if mode == CuaPermissionMode::Standard => Some(EmbeddedCuaConfig {
                        command,
                        socket: data_dir.join("cua-driver.sock"),
                        manifest: PathBuf::new(),
                        mode,
                    }),
                    None => {
                        log::error!(
                            "CUA capability manifest missing; starting sani-core without computer control"
                        );
                        None
                    }
                }
            }
        };

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
            command,
            args: if is_bundled_core {
                Vec::new()
            } else {
                vec!["-m".to_string(), "assistant.core".to_string()]
            },
            env,
            // A deterministic working dir: the sidecar must not quietly pick up
            // a developer's `.env` and disagree with what the app configured.
            working_dir: Some(data_dir),
            cua,
        })
    }

    fn configure_cua(&mut self, cua: Option<&EmbeddedCuaConfig>) {
        match cua {
            Some(cua) => {
                self.env
                    .push(("CUA_ENABLED".to_string(), "true".to_string()));
                // The sidecar's posture check must describe the mode the host
                // actually started, or a standard-mode daemon is reported as
                // policy-invalid and the UI contradicts itself.
                self.env.push((
                    "CUA_PERMISSION_MODE".to_string(),
                    cua.mode.as_str().to_string(),
                ));
                if !cua.manifest.as_os_str().is_empty() {
                    self.env.push((
                        "CUA_CAPABILITY_MANIFEST_PATH".to_string(),
                        cua.manifest.to_string_lossy().into_owned(),
                    ));
                }
                self.env.push((
                    "CUA_COMMAND".to_string(),
                    cua.command.to_string_lossy().into_owned(),
                ));
                self.env.push((
                    "CUA_SOCKET".to_string(),
                    cua.socket.to_string_lossy().into_owned(),
                ));
            }
            None => self
                .env
                .push(("CUA_ENABLED".to_string(), "false".to_string())),
        }
    }
}

/// Resolve only the exact, architecture-compatible core bundled alongside the
/// Tauri executable. Development discovery remains an explicit fallback for
/// unbundled builds; a release never borrows a checkout interpreter.
pub(crate) fn running_from_bundle() -> bool {
    std::env::current_exe().ok().is_some_and(|path| {
        path.ancestors()
            .any(|parent| parent.extension().is_some_and(|ext| ext == "app"))
    })
}

fn packaged_core(app: &AppHandle) -> Option<PathBuf> {
    // Frozen Python is a directory runtime on macOS so every dylib can be
    // signed inside Sani.app.  Keep the older external-binary lookup as a
    // development/upgrade fallback only.
    crate::setup::resource_path(app, "sani-core-runtime/sani-core")
        .filter(|path| path.is_file())
        .or_else(|| packaged_external_bin("sani-core"))
}

/// What Sani learned about the daemon's authority, keeping "denied" separate
/// from "could not ask".
///
/// Collapsing those two was the reported bug: the bounded manifest idles out on
/// wall-clock time, and every call -- including this read-only probe -- then
/// answers `Policy loading error: capability manifest idle timeout exceeded`.
/// That text is not a permission payload, so the probe returned `None`, which
/// the UI rendered as `accessibility: unknown` plus `Runtime: not_authorized`
/// while macOS had both switches on. A policy lockout, a dead socket and a real
/// denial are three different faults with three different fixes.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum DriverProbe {
    Answered {
        accessibility: bool,
        screen_recording: bool,
    },
    /// The daemon refused on policy grounds before it evaluated permissions.
    PolicyLocked { detail: String },
    /// Nothing answered: not started, socket dead, or it exited mid-probe.
    Unreachable { detail: String },
    Timeout,
    Malformed { detail: String },
}

impl DriverProbe {
    /// One word for the UI, never a guess about a state Sani could not read.
    pub fn label(&self) -> &'static str {
        match self {
            Self::Answered {
                accessibility: true,
                screen_recording: true,
            } => "granted",
            Self::Answered { .. } => "denied",
            Self::PolicyLocked { .. } => "policy_locked",
            Self::Unreachable { .. } => "unreachable",
            Self::Timeout => "unanswered",
            Self::Malformed { .. } => "unrecognized",
        }
    }
}

/// The embedded daemon's authority, read over Sani's private socket.
///
/// This is a cross-check, not the gate. Embedded mode attributes the daemon to
/// its host: `check_permissions` reports the *host app's* TCC grants and says so
/// itself ("No separate driver grant exists or is needed"), which Phase 0
/// confirmed live. Sani's own `AXIsProcessTrusted` /
/// `CGPreflightScreenCaptureAccess` read is therefore authoritative for the
/// daemon too, and an unanswered probe must never be reported as a denial.
pub async fn driver_probe(app: AppHandle) -> DriverProbe {
    tokio::task::spawn_blocking(move || read_driver_probe(&app))
        .await
        .unwrap_or_else(|_| DriverProbe::Unreachable {
            detail: "the permission probe task did not run".to_string(),
        })
}

fn read_driver_probe(app: &AppHandle) -> DriverProbe {
    let Some(cua) = SaniCoreConfig::resolve(app).ok().and_then(|config| config.cua) else {
        return DriverProbe::Unreachable {
            detail: "Sani's packaged computer-control driver is missing".to_string(),
        };
    };
    let child = match std::process::Command::new(&cua.command)
        .args([
            "call",
            "check_permissions",
            "{\"prompt\": false}",
            "--socket",
            cua.socket.to_string_lossy().as_ref(),
        ])
        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::piped())
        .spawn()
    {
        Ok(child) => child,
        Err(err) => {
            return DriverProbe::Unreachable {
                detail: format!("the permission probe could not be run: {err}"),
            }
        }
    };
    let output = match wait_with_timeout(child, DRIVER_PERMISSION_TIMEOUT) {
        Some(Ok(output)) => output,
        Some(Err(err)) => {
            return DriverProbe::Unreachable {
                detail: format!("the permission probe failed: {err}"),
            }
        }
        None => return DriverProbe::Timeout,
    };
    let stdout = String::from_utf8_lossy(&output.stdout).to_string();
    let stderr = String::from_utf8_lossy(&output.stderr).to_string();
    classify_probe_output(&stdout, &stderr, output.status.code().unwrap_or(-1))
}

/// Turn a finished probe's output into a classified answer.
///
/// The JSON path is tried first and alone: a real payload is never
/// second-guessed by the text heuristics below, which exist only to name the
/// faults that arrive where a payload should have been.
fn classify_probe_output(stdout: &str, stderr: &str, code: i32) -> DriverProbe {
    if let Ok(value) = serde_json::from_str::<Value>(stdout.trim()) {
        if let (Some(accessibility), Some(screen_recording)) = (
            value.get("accessibility").and_then(Value::as_bool),
            value.get("screen_recording").and_then(Value::as_bool),
        ) {
            return DriverProbe::Answered {
                accessibility,
                screen_recording,
            };
        }
    }
    let text = format!("{stdout} {stderr}");
    let lowered = text.to_lowercase();
    if lowered.contains("policy loading error")
        || lowered.contains("idle timeout exceeded")
        || lowered.contains("capability manifest")
    {
        return DriverProbe::PolicyLocked {
            detail: clip(&text, 200),
        };
    }
    if lowered.contains("no daemon")
        || lowered.contains("connection refused")
        || lowered.contains("not running")
        || lowered.contains("cannot connect")
        || lowered.contains("no such file")
    {
        return DriverProbe::Unreachable {
            detail: clip(&text, 200),
        };
    }
    DriverProbe::Malformed {
        detail: clip(
            &if code == 0 {
                text
            } else {
                format!("exit {code}: {text}")
            },
            200,
        ),
    }
}

fn clip(text: &str, limit: usize) -> String {
    let trimmed = text.trim();
    if trimmed.len() <= limit {
        return trimmed.to_string();
    }
    let mut end = limit;
    while !trimmed.is_char_boundary(end) {
        end -= 1;
    }
    trimmed[..end].to_string()
}

/// Wait for a child to finish, killing it if the budget runs out.
fn wait_with_timeout(
    mut child: std::process::Child,
    budget: Duration,
) -> Option<std::io::Result<std::process::Output>> {
    let deadline = std::time::Instant::now() + budget;
    loop {
        match child.try_wait() {
            Ok(Some(_)) => return Some(child.wait_with_output()),
            Ok(None) => {}
            Err(err) => {
                let _ = child.kill();
                let _ = child.wait();
                return Some(Err(err));
            }
        }
        if std::time::Instant::now() >= deadline {
            let _ = child.kill();
            let _ = child.wait();
            return None;
        }
        std::thread::sleep(Duration::from_millis(50));
    }
}

fn packaged_cua_driver(app: &AppHandle) -> Option<PathBuf> {
    let command = crate::setup::resource_path(app, "CuaDriver.app/Contents/MacOS/cua-driver")?;
    command.is_file().then_some(command)
}

/// Resolve an architecture-qualified Tauri external binary beside Sani.
fn packaged_external_bin(stem: &str) -> Option<PathBuf> {
    let exe = std::env::current_exe().ok()?;
    let dir = exe.parent()?;
    let target = match std::env::consts::ARCH {
        "aarch64" => format!("{stem}-aarch64-apple-darwin"),
        "x86_64" => format!("{stem}-x86_64-apple-darwin"),
        _ => return None,
    };
    [target, stem.to_string()]
        .into_iter()
        .map(|name| dir.join(name))
        .find(|path| path.is_file())
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
    registry_ready: bool,
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
            registry_ready: false,
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
        let timeout = registry_timeout(self.registry_ready);
        let result = self.request("agents.list", json!({}), timeout).await?;
        let agents = result
            .get("agents")
            .and_then(Value::as_array)
            .cloned()
            .ok_or_else(|| format!("unexpected agents.list result: {result}"))?;
        self.registry_ready = true;
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
    /// Sani owns exactly one embedded CUA daemon generation.  It is not a
    /// global background service and is stopped when Sani stops.
    cua_driver: Mutex<Option<EmbeddedCuaDriver>>,
    /// Whether the sidecar currently running was launched with computer control
    /// wired in. A driver that missed its boot window used to leave
    /// `CUA_ENABLED=false` latched for the whole session -- the daemon came
    /// back, the core never noticed. Recovery re-launches the core when (and
    /// only when) it is actually running without CUA.
    core_has_cua: std::sync::atomic::AtomicBool,
    /// All ownership-changing IPC operations are serialized. Three WebViews
    /// ask for the registry during launch; without this gate, one could take
    /// the client while another concluded it had died and spawned a second
    /// core process.
    operation: Arc<tokio::sync::Mutex<()>>,
}

async fn operation_guard(app: &AppHandle) -> tokio::sync::OwnedMutexGuard<()> {
    app.state::<SaniCoreState>()
        .operation
        .clone()
        .lock_owned()
        .await
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

fn live_control(app: &AppHandle) -> Option<RunControl> {
    app.state::<SaniCoreState>()
        .live
        .lock()
        .ok()
        .and_then(|guard| guard.clone())
}

async fn stop_embedded_cua_driver(driver: EmbeddedCuaDriver) {
    let socket = driver.socket.clone();
    let mut child = driver.child;
    if let Err(err) = child.kill().await {
        log::debug!("embedded CUA driver was already stopped: {err}");
    }
    let _ = child.wait().await;
    if let Err(err) = std::fs::remove_file(&socket) {
        if err.kind() != std::io::ErrorKind::NotFound {
            log::warn!(
                "could not remove stale CUA socket {}: {err}",
                socket.display()
            );
        }
    }
}

/// The daemon's launch arguments for one mode.
///
/// Split out from spawning so both modes are testable without starting a
/// daemon: the argument list *is* the security posture, and a dropped
/// `--approve-capability-manifest` or a stray manifest in standard mode would
/// otherwise only surface on a live Mac.
fn driver_serve_args(config: &EmbeddedCuaConfig) -> Vec<String> {
    let mut args: Vec<String> = vec![
        "serve".to_string(),
        "--embedded".to_string(),
        "--socket".to_string(),
        config.socket.to_string_lossy().into_owned(),
        "--permission-mode".to_string(),
        config.mode.as_str().to_string(),
    ];
    if config.mode == CuaPermissionMode::Bounded {
        args.push("--capability-manifest".to_string());
        args.push(config.manifest.to_string_lossy().into_owned());
        args.push("--approve-capability-manifest".to_string());
    }
    args
}

async fn spawn_embedded_cua_driver(
    config: &EmbeddedCuaConfig,
    owned: Option<u32>,
) -> Result<EmbeddedCuaDriver, String> {
    // The socket is owned below Sani's data directory.  A previous crashed
    // child can leave this one exact endpoint behind; do not touch any global
    // Cua Driver socket or user-managed daemon.
    let foreign = foreign_driver_pids(&live_driver_pids(), &config.socket, owned);
    if !foreign.is_empty() {
        let (reclaimed, surviving) =
            reclaim_socket_endpoint(&config.command, &config.socket, &foreign, true, true).await;
        if !surviving.is_empty() {
            return Err(format!(
                "a previous CUA driver is still holding Sani's private socket: {:?}",
                surviving
            ));
        }
        log::info!(
            "reclaimed CUA socket endpoint from stale driver(s) {:?}",
            reclaimed
        );
    } else if let Err(err) = std::fs::remove_file(&config.socket) {
        if err.kind() != std::io::ErrorKind::NotFound {
            return Err(format!(
                "cannot clear Sani CUA socket {}: {err}",
                config.socket.display()
            ));
        }
    }
    let args = driver_serve_args(config);
    let mut command = tokio::process::Command::new(&config.command);
    command
        .args(&args)
        // Embedded mode reports the *host's* TCC grants, which is only true
        // while this process stays a child in Sani's responsibility chain.
        .env("CUA_DRIVER_EMBEDDED", "1")
        .env("CUA_DRIVER_HOST_BUNDLE_ID", "app.sani.local")
        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::inherit())
        .kill_on_drop(true);
    let mut child = command.spawn().map_err(|err| {
        format!(
            "could not start embedded CUA driver {}: {err}",
            config.command.display()
        )
    })?;
    log::info!(
        "embedded CUA driver spawning in {} mode: {args:?}",
        config.mode.as_str()
    );
    let deadline = tokio::time::Instant::now() + CUA_BOOT_TIMEOUT;
    loop {
        if embedded_driver_socket_ready(&config.socket).await {
            return Ok(EmbeddedCuaDriver {
                child,
                socket: config.socket.clone(),
            });
        }
        if let Some(status) = child.try_wait().map_err(|err| err.to_string())? {
            return Err(format!(
                "embedded CUA driver exited during startup: {status}"
            ));
        }
        if tokio::time::Instant::now() >= deadline {
            let _ = child.kill().await;
            let _ = child.wait().await;
            return Err(format!(
                "embedded CUA driver did not create its private socket within {CUA_BOOT_TIMEOUT:?}"
            ));
        }
        tokio::time::sleep(Duration::from_millis(50)).await;
    }
}

/// The pid of the daemon generation Sani actually owns, if any.
fn owned_driver_pid(app: &AppHandle) -> Option<u32> {
    app.state::<SaniCoreState>()
        .cua_driver
        .lock()
        .ok()
        .and_then(|guard| guard.as_ref().and_then(|driver| driver.child.id()))
}

/// Start at most one driver for the current Sani process.  A dead generation
/// is replaced once at the next core launch; a healthy generation is reused.
async fn ensure_embedded_cua_driver(
    app: &AppHandle,
    config: &EmbeddedCuaConfig,
) -> Result<(), String> {
    let child_running = {
        let state = app.state::<SaniCoreState>();
        let mut guard = state
            .cua_driver
            .lock()
            .map_err(|_| "embedded CUA driver state poisoned".to_string())?;
        guard.as_mut().is_some_and(|driver| {
            driver.socket == config.socket && matches!(driver.child.try_wait(), Ok(None))
        })
    };
    let socket_ready = child_running && embedded_driver_socket_ready(&config.socket).await;
    if !embedded_driver_needs_recovery(child_running, socket_ready) {
        // Our own generation is live, but a previous one may still be listening
        // on an unlinked inode of the same path -- it answers races for the
        // endpoint after the next restart and carries the old code identity.
        // Signal it directly: Sani's daemon shares this socket, so a polite
        // endpoint-level `stop` would tear down the healthy generation instead.
        let foreign = foreign_driver_pids(&live_driver_pids(), &config.socket, owned_driver_pid(app));
        if !foreign.is_empty() {
            let (reclaimed, surviving) =
                reclaim_socket_endpoint(&config.command, &config.socket, &foreign, false, false)
                    .await;
            if !surviving.is_empty() {
                log::error!("CUA driver(s) {surviving:?} could not be reclaimed");
            } else {
                log::info!("removed stale CUA driver generation(s) {reclaimed:?}");
            }
        }
        return Ok(());
    }
    let stale = {
        let state = app.state::<SaniCoreState>();
        let stale = state
            .cua_driver
            .lock()
            .map_err(|_| "embedded CUA driver state poisoned".to_string())?
            .take();
        stale
    };
    if let Some(driver) = stale {
        stop_embedded_cua_driver(driver).await;
    }
    let driver = spawn_embedded_cua_driver(config, None).await?;
    let state = app.state::<SaniCoreState>();
    state
        .cua_driver
        .lock()
        .map_err(|_| "embedded CUA driver state poisoned".to_string())?
        .replace(driver);
    log::info!(
        "embedded {} CUA driver started",
        config.mode.as_str()
    );
    Ok(())
}

/// Repair only Sani's private CUA service when it has stopped accepting
/// connections.  This deliberately leaves the sidecar, conversations, and
/// persisted user settings untouched.
pub async fn recover_embedded_cua_driver(app: &AppHandle) -> Result<(), String> {
    let config = SaniCoreConfig::resolve(app)?;
    let cua = config
        .cua
        .as_ref()
        .ok_or_else(|| "Sani’s packaged computer-control driver is missing".to_string())?;
    let driver_was_live = owned_driver_pid(app).is_some()
        && embedded_driver_socket_ready(&cua.socket).await;
    ensure_embedded_cua_driver(app, cua).await?;
    // Un-latch the boot failure. A daemon that missed its 10-second window at
    // login used to leave the sidecar running with CUA disabled forever: this
    // recovery rebuilt the daemon and never told the core. Only when a run is
    // not live, so no streaming turn is disturbed.
    if !driver_was_live
        && !is_run_live(app)
        && !app
            .state::<SaniCoreState>()
            .core_has_cua
            .load(Ordering::Relaxed)
    {
        log::warn!("restarting sani-core so it runs with the recovered CUA driver");
        start_unlocked(app).await?;
    }
    Ok(())
}

/// Best-effort orderly cleanup on Sani exit.  The child is also configured to
/// die with its host, but closing it here removes the private socket promptly.
pub fn shutdown_embedded_cua_driver(app: &AppHandle) {
    let driver = app
        .state::<SaniCoreState>()
        .cua_driver
        .lock()
        .ok()
        .and_then(|mut guard| guard.take());
    if let Some(driver) = driver {
        tauri::async_runtime::spawn(stop_embedded_cua_driver(driver));
    }
}

// ------------------------------------------------------------------ commands

/// Spawn the sani-core sidecar and remember it. If one is already running
/// it is shut down cleanly and replaced (the sidecar is single-owner).
#[tauri::command]
pub async fn core_start(app: AppHandle) -> Result<Value, String> {
    let _operation = operation_guard(&app).await;
    start_unlocked(&app).await
}

async fn start_unlocked(app: &AppHandle) -> Result<Value, String> {
    let mut config = SaniCoreConfig::resolve(&app)?;
    let cua = config.cua.clone();
    let mut core_has_cua = false;
    match cua.as_ref() {
        Some(cua) => match ensure_embedded_cua_driver(app, cua).await {
            Ok(()) => {
                config.configure_cua(Some(cua));
                core_has_cua = true;
            }
            Err(err) => {
                // A denied OS permission or failed driver must not make
                // ordinary text/voice reasoning disappear.  The core reports
                // Computer Control unavailable through its real status path.
                log::error!("embedded CUA unavailable: {err}");
                config.configure_cua(None);
            }
        },
        None => config.configure_cua(None),
    }
    app.state::<SaniCoreState>()
        .core_has_cua
        .store(core_has_cua, Ordering::Relaxed);
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
    let _operation = operation_guard(&app).await;
    stop_unlocked(&app).await;
    Ok(json!({"status": "stopped"}))
}

async fn stop_unlocked(app: &AppHandle) {
    if let Ok(Some(client)) = take_client(app) {
        client.shutdown().await;
    }
}

/// List the sidecar's registered agents.
#[tauri::command]
pub async fn core_agents(app: AppHandle) -> Result<Value, String> {
    let _operation = operation_guard(&app).await;
    list_agents_with_recovery(&app)
        .await
        .map(|agents| json!({ "agents": agents }))
}

async fn list_agents_once(app: &AppHandle) -> Result<Vec<Value>, String> {
    match take_client(app)? {
        Some(client) => {
            // Put the ownership probe back: the operation gate guarantees no
            // other request can observe this short handoff as a dead core.
            restore_client(app, client);
        }
        None => {
            log::warn!("sani-core is not running; starting it");
            start_unlocked(app).await?;
        }
    }
    let mut client = take_client(app)?.ok_or_else(|| "sani-core did not start".to_string())?;
    let result = client.list_agents().await;
    restore_client(app, client);
    result
}

/// One bounded restart is enough to recover a stale child or launch race. A
/// second failure is surfaced to the user; no retry loop may create a herd of
/// hidden sidecars or leave the UI permanently on an old unavailable result.
///
/// "One bounded restart per call" was not a bound: `core_agents`, `core_ping`,
/// agent resolution on every turn and the supervisor each restarted the sidecar
/// on their own failure, so a persistent fault produced 40 spawns inside 5
/// seconds in one app log (`sani-core env` counts one line per spawn). The
/// ledger below makes the bound process-wide.
async fn list_agents_with_recovery(app: &AppHandle) -> Result<Vec<Value>, String> {
    let first = list_agents_once(app).await;
    if !registry_needs_recovery(&first) {
        RECOVERY.lock().succeeded();
        return first;
    }
    let first_error = first
        .err()
        .unwrap_or_else(|| "sani-core registry returned no agents".into());
    if !allow_recovery() {
        return Err(format!(
            "sani-core is failing to start and Sani is not restarting it again yet: {first_error}"
        ));
    }
    log::warn!("sani-core registry failed; performing one bounded restart: {first_error}");
    stop_unlocked(app).await;
    start_unlocked(app).await?;
    match list_agents_once(app).await {
        Ok(agents) if agents.is_empty() => Err("sani-core registry returned no agents after recovery".into()),
        Ok(agents) => {
            RECOVERY.lock().succeeded();
            Ok(agents)
        }
        Err(second_error) => Err(format!(
            "sani-core registry unavailable after one recovery: {second_error}"
        )),
    }
}

/// Process-wide record of sidecar restart attempts.
#[derive(Default)]
struct RecoveryLedger {
    attempts: u32,
    last_attempt: Option<std::time::Instant>,
}

impl RecoveryLedger {
    /// True when a restart may run now. A recovery attempted inside the
    /// cooldown of a previous one is a fault that restarting cannot fix.
    fn allow(&mut self, now: std::time::Instant, cooldown: Duration) -> bool {
        if self.attempts > 0
            && self
                .last_attempt
                .is_some_and(|previous| now.duration_since(previous) < cooldown)
        {
            return false;
        }
        self.attempts = self.attempts.saturating_add(1);
        self.last_attempt = Some(now);
        true
    }

    fn succeeded(&mut self) {
        self.attempts = 0;
        self.last_attempt = None;
    }
}

static RECOVERY: parking_lot::Mutex<RecoveryLedger> = parking_lot::Mutex::new(RecoveryLedger {
    attempts: 0,
    last_attempt: None,
});

fn allow_recovery() -> bool {
    RECOVERY
        .lock()
        .allow(std::time::Instant::now(), RECOVERY_COOLDOWN)
}

fn registry_needs_recovery(result: &Result<Vec<Value>, String>) -> bool {
    match result {
        Err(_) => true,
        Ok(agents) => agents.is_empty(),
    }
}

fn registry_timeout(registry_ready: bool) -> Duration {
    if registry_ready {
        REQUEST_TIMEOUT
    } else {
        REGISTRY_BOOT_TIMEOUT
    }
}

/// `system.status`: the sidecar's own view of its subsystems.
#[tauri::command]
pub async fn core_status(app: AppHandle) -> Result<Value, String> {
    let _operation = operation_guard(&app).await;
    match take_client(&app)? {
        Some(client) => restore_client(&app, client),
        None => {
            log::warn!("sani-core is not running; starting it");
            start_unlocked(&app).await?;
        }
    }
    let mut client = take_client(&app)?.ok_or_else(|| "sani-core did not start".to_string())?;
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
    let _operation = operation_guard(app).await;
    let mut client = take_client(app)?.ok_or_else(|| "sani-core is not running".to_string())?;
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
    let _operation = operation_guard(&app).await;
    let mut client = take_client(&app)?.ok_or_else(|| "sani-core is not running".to_string())?;
    let result = client.cancel_run(&run_id).await;
    restore_client(&app, client);
    result
}

/// Health check: one `agents.list` round trip against the live sidecar.
#[tauri::command]
pub async fn core_ping(app: AppHandle) -> Result<Value, String> {
    let _operation = operation_guard(&app).await;
    list_agents_with_recovery(&app)
        .await
        .map(|agents| json!({"status": "ok", "agents": agents.len()}))
}

/// Apply persisted launch inputs to sani-core. The caller must already have
/// rejected active runs: replacing a child process while it streams would
/// break a user turn. This function does not attempt any provider fallback.
pub async fn reload_for_settings(app: AppHandle) -> Result<(), String> {
    if is_run_live(&app) {
        return Err("a run is active".into());
    }
    let _operation = operation_guard(&app).await;
    stop_unlocked(&app).await;
    start_unlocked(&app).await?;
    list_agents_with_recovery(&app).await.map(|_| ())
}

// --------------------------------------------------------------- supervision

/// Frontend event carrying whether the assistant runtime is reachable.
pub const RUNTIME_STATUS: &str = "sani://agent-status";

/// How often the supervisor checks the sidecar.
const SUPERVISOR_INTERVAL: Duration = Duration::from_secs(20);

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
    core_ping(app).await.map(|_| ())
}

/// Spawn the sidecar at startup, logging rather than failing the launch: the
/// overlays must still come up so the user can see what is wrong.
pub fn start_at_startup(app: &AppHandle) {
    let handle = app.clone();
    tauri::async_runtime::spawn(async move {
        match core_start(handle.clone()).await {
            Ok(_) => {
                crate::onboarding::set_runtime_status(
                    &handle,
                    crate::onboarding::ApplyStatus::Ready,
                );
                spawn_supervisor(handle)
            }
            Err(err) => {
                log::error!("sani-core could not start: {err}");
                crate::onboarding::set_runtime_status(
                    &handle,
                    crate::onboarding::ApplyStatus::FailedToApply,
                );
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
    use std::time::{SystemTime, UNIX_EPOCH};
    use tokio::io::duplex;
    use tokio::io::split;
    use tokio::net::UnixListener;

    const FAKE_TIMEOUT: Duration = Duration::from_secs(5);

    fn test_socket_path(label: &str) -> PathBuf {
        PathBuf::from("/private/tmp").join(format!(
            "sani-{label}-{}-{}.sock",
            std::process::id(),
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .expect("clock before Unix epoch")
                .as_nanos()
        ))
    }

    #[tokio::test]
    async fn embedded_driver_socket_must_accept_connections_before_ready() {
        let stale = test_socket_path("stale-driver");
        std::fs::write(&stale, b"not a socket").unwrap();
        assert!(!embedded_driver_socket_ready(&stale).await);
        std::fs::remove_file(&stale).unwrap();

        let live = test_socket_path("live-driver");
        let listener = UnixListener::bind(&live).unwrap();
        assert!(embedded_driver_socket_ready(&live).await);
        drop(listener);
        std::fs::remove_file(&live).unwrap();
    }

    #[test]
    fn embedded_driver_recovery_requires_a_live_child_and_live_socket() {
        assert!(embedded_driver_needs_recovery(false, false));
        assert!(embedded_driver_needs_recovery(false, true));
        assert!(embedded_driver_needs_recovery(true, false));
        assert!(!embedded_driver_needs_recovery(true, true));
    }

    /// The exact `ps` shape of the fault this must catch: a driver from the
    /// previous Sani generation, reparented to launchd, still carrying Sani's
    /// private socket path in its arguments.
    const PS_SAMPLE: &str = "    1     0 /sbin/launchd\n39336     1 /Applications/Sani.app/Contents/MacOS/sani\n38668     1 /Applications/Sani.app/Contents/Resources/CuaDriver.app/Contents/MacOS/cua-driver serve --embedded --socket /Users/sayan/Library/Application Support/app.sani.local/cua-driver.sock --permission-mode bounded --capability-manifest /Applications/Sani.app/Contents/Resources/config/cua-capabilities.yaml --approve-capability-manifest\n39344 39336 /Applications/Sani.app/Contents/Resources/CuaDriver.app/Contents/MacOS/cua-driver serve --embedded --socket /Users/sayan/Library/Application Support/app.sani.local/cua-driver.sock --permission-mode bounded\n39500 39349 /Applications/Sani.app/Contents/Resources/CuaDriver.app/Contents/MacOS/cua-driver mcp --embedded --socket /Users/sayan/Library/Application Support/app.sani.local/cua-driver.sock\n";

    fn sample_socket() -> PathBuf {
        PathBuf::from("/Users/sayan/Library/Application Support/app.sani.local/cua-driver.sock")
    }

    #[test]
    fn process_rows_parse_pid_ppid_and_the_full_argument_line() {
        let rows = parse_process_rows(PS_SAMPLE);
        assert_eq!(rows.len(), 5);
        assert_eq!(rows[2].pid, 38668);
        assert_eq!(rows[2].ppid, 1);
        assert!(rows[2].args.contains("--approve-capability-manifest"));
        assert_eq!(parse_process_rows("PID PPID ARGS\n\n").len(), 0);
    }

    #[test]
    fn an_orphaned_driver_on_the_same_socket_is_detected_as_foreign() {
        let foreign = foreign_driver_pids(&parse_process_rows(PS_SAMPLE), &sample_socket(), Some(39344));
        assert_eq!(foreign, vec![38668]);
    }

    #[test]
    fn the_sidecars_own_mcp_client_is_never_mistaken_for_a_daemon() {
        // It carries the same socket path; killing it would break computer
        // control mid-run rather than clean anything up.
        let rows = vec![ProcessRow {
            pid: 39500,
            ppid: 39349,
            args: "/x/cua-driver mcp --embedded --socket /Users/sayan/Library/Application Support/app.sani.local/cua-driver.sock".to_string(),
        }];
        assert!(foreign_driver_pids(&rows, &sample_socket(), None).is_empty());
    }

    #[test]
    fn a_daemon_on_another_socket_is_left_alone() {
        let rows = parse_process_rows(PS_SAMPLE);
        let other = PathBuf::from("/Users/sayan/Library/Caches/cua-driver/cua-driver.sock");
        assert!(foreign_driver_pids(&rows, &other, None).is_empty());
    }

    #[test]
    fn permission_probe_keeps_a_policy_lockout_apart_from_a_denial() {
        let locked = read_probe_text("Policy loading error: capability manifest idle timeout exceeded\n", "", 2);
        assert!(matches!(locked, DriverProbe::PolicyLocked { .. }));
        let dead = read_probe_text("", "connect to /x/cua-driver.sock: No such file\n", 1);
        assert!(matches!(dead, DriverProbe::Unreachable { .. }));
        let answered = read_probe_text(
            "{\"accessibility\": true, \"screen_recording\": false}\n",
            "",
            0,
        );
        assert_eq!(
            answered,
            DriverProbe::Answered {
                accessibility: true,
                screen_recording: false
            }
        );
        // A payload that is JSON but not the expected shape must not be guessed.
        assert!(matches!(
            read_probe_text("{\"unexpected\": 1}", "", 0),
            DriverProbe::Malformed { .. }
        ));
    }

    fn driver_config(mode: CuaPermissionMode) -> EmbeddedCuaConfig {
        EmbeddedCuaConfig {
            command: PathBuf::from("/Applications/Sani.app/Contents/Resources/CuaDriver.app/Contents/MacOS/cua-driver"),
            socket: sample_socket(),
            manifest: PathBuf::from("/Applications/Sani.app/Contents/Resources/config/cua-capabilities.yaml"),
            mode,
        }
    }

    #[test]
    fn bounded_launch_keeps_the_reviewed_manifest_and_its_approval_flag() {
        let args = driver_serve_args(&driver_config(CuaPermissionMode::Bounded));
        assert!(args.contains(&"--permission-mode".to_string()));
        assert!(args.contains(&"bounded".to_string()));
        assert!(args.contains(&"--capability-manifest".to_string()));
        assert!(args.contains(&"--approve-capability-manifest".to_string()));
        assert!(args.contains(&"--embedded".to_string()));
    }

    #[test]
    fn standard_launch_asks_for_no_manifest_at_all() {
        // A manifest left in the arguments would re-impose the ceiling that
        // architecture D1 moved into Sani's own action-class gate.
        let args = driver_serve_args(&driver_config(CuaPermissionMode::Standard));
        assert!(args.contains(&"standard".to_string()));
        assert!(!args.iter().any(|a| a.contains("manifest")));
    }

    #[test]
    fn recovery_inside_the_cooldown_is_refused_so_no_herd_starts() {
        let started = std::time::Instant::now();
        let mut ledger = RecoveryLedger::default();
        assert!(ledger.allow(started, RECOVERY_COOLDOWN));
        assert!(!ledger.allow(started + Duration::from_secs(1), RECOVERY_COOLDOWN));
        ledger.succeeded();
        assert!(ledger.allow(started + Duration::from_secs(2), RECOVERY_COOLDOWN));
        // Past the cooldown a fresh attempt is allowed: the breaker throttles a
        // restart storm, it never disables recovery permanently.
        let mut stalled = RecoveryLedger::default();
        assert!(stalled.allow(started, RECOVERY_COOLDOWN));
        assert!(stalled.allow(started + RECOVERY_COOLDOWN, RECOVERY_COOLDOWN));
    }

    #[test]
    #[ignore = "reads the live process table: asserts Sani can find its own orphan"]
    fn finds_the_real_orphaned_driver_running_on_this_mac() {
        let socket = std::env::var("SANI_TEST_CUA_SOCKET").expect("SANI_TEST_CUA_SOCKET");
        let owned: Option<u32> = std::env::var("SANI_TEST_OWNED_PID")
            .ok()
            .and_then(|value| value.parse().ok());
        let foreign = foreign_driver_pids(&live_driver_pids(), &PathBuf::from(socket), owned);
        println!("foreign driver pids: {foreign:?}");
        assert!(
            !foreign.is_empty(),
            "expected the retained orphan to be detected"
        );
    }

    /// End-to-end proof of the reclaim path against a real orphaned daemon.
    ///
    /// The daemon is started through `nohup … &` so it is reparented away from
    /// this test process -- the exact shape of the field fault, where nothing
    /// holds the child handle and `kill_on_drop` can never fire.
    #[tokio::test]
    #[ignore = "starts and stops a real throwaway CUA daemon on a temp socket"]
    async fn a_detached_daemon_is_found_and_reclaimed_from_its_socket() {
        let binary = std::env::var("SANI_TEST_CUA_BINARY").expect("SANI_TEST_CUA_BINARY");
        let socket = test_socket_path("reclaim-driver");
        let script = format!(
            "nohup '{binary}' serve --embedded --permission-mode standard --socket '{socket}' >/dev/null 2>&1 &",
            socket = socket.display()
        );
        std::process::Command::new("/bin/sh")
            .args(["-c", &script])
            .status()
            .expect("sh should launch the throwaway daemon");
        let deadline = tokio::time::Instant::now() + Duration::from_secs(15);
        while !embedded_driver_socket_ready(&socket).await && tokio::time::Instant::now() < deadline
        {
            tokio::time::sleep(Duration::from_millis(100)).await;
        }
        assert!(
            embedded_driver_socket_ready(&socket).await,
            "the throwaway daemon never came up"
        );

        let foreign = foreign_driver_pids(&live_driver_pids(), &socket, None);
        assert_eq!(foreign.len(), 1, "expected exactly the detached daemon");

        let config = EmbeddedCuaConfig {
            command: PathBuf::from(&binary),
            socket: socket.clone(),
            manifest: PathBuf::new(),
            mode: CuaPermissionMode::Standard,
        };
        let (reclaimed, surviving) =
            reclaim_socket_endpoint(&config.command, &socket, &foreign, true, true).await;
        assert_eq!(reclaimed, foreign);
        assert!(surviving.is_empty(), "a daemon survived every shutdown rung");
        assert!(
            !embedded_driver_socket_ready(&socket).await,
            "the endpoint should be gone after reclaim"
        );
    }

    /// The classification half of `read_driver_probe`, on captured output.
    fn read_probe_text(stdout: &str, stderr: &str, code: i32) -> DriverProbe {
        classify_probe_output(stdout, stderr, code)
    }

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

    #[test]
    fn unavailable_or_empty_registry_gets_exactly_one_recovery_attempt() {
        assert!(registry_needs_recovery(&Err("sidecar exited".into())));
        assert!(registry_needs_recovery(&Ok(Vec::new())));
        assert!(!registry_needs_recovery(&Ok(vec![json!({"id": "velo"})])));
    }

    #[test]
    fn cold_registry_handshake_has_a_bounded_boot_budget() {
        assert_eq!(registry_timeout(false), REGISTRY_BOOT_TIMEOUT);
        assert_eq!(registry_timeout(true), REQUEST_TIMEOUT);
        assert!(REGISTRY_BOOT_TIMEOUT > REQUEST_TIMEOUT);
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
