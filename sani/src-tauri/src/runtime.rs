//! Dispatch one user turn through the sani-core sidecar.
//!
//! Replaces the old HTTP gateway client: the assistant is a private child
//! process now, so a turn is a framed `run.start` and its answer arrives as
//! event frames. Nothing here decides *which* agent runs -- it reports who was
//! asked and who answered, and the sidecar's registry is the only source of
//! agent identity.
//!
//! Event mapping (core frame -> frontend):
//!   agent.started    sani://agent-start   names the agent that took the turn
//!   agent.token      sani://agent-chunk   kind=text
//!   agent.progress   sani://agent-chunk   kind=status, and sani://activity
//! plus every frame relayed verbatim on sani://core-event for the agent UI.

use serde_json::{json, Value};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use tauri::{AppHandle, Emitter};

use crate::app_state;

/// Monotonic activity sequence, so the timeline renders in arrival order.
static ACTIVITY_SEQUENCE: AtomicU64 = AtomicU64::new(1);

fn next_sequence() -> u64 {
    ACTIVITY_SEQUENCE.fetch_add(1, Ordering::Relaxed)
}

/// What the live run has produced so far. Shared with the event sink because
/// the sink is consumed by `run_turn`, and the caller still needs the answer
/// text and the run id afterwards.
#[derive(Clone, Default)]
struct Streamed {
    text: String,
    run_id: String,
}

/// One core event frame, reduced to the pieces the UI needs.
struct CoreEvent {
    kind: String,
    agent_id: String,
    run_id: String,
    message: Option<String>,
    token: Option<String>,
}

fn parse(frame: &Value) -> Option<CoreEvent> {
    if frame.get("type").and_then(Value::as_str) != Some("event") {
        return None;
    }
    let data = frame.get("data")?;
    Some(CoreEvent {
        kind: frame.get("kind").and_then(Value::as_str)?.to_string(),
        agent_id: frame
            .get("agent_id")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string(),
        run_id: frame
            .get("run_id")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string(),
        message: data
            .get("message")
            .and_then(Value::as_str)
            .map(String::from),
        token: data.get("text").and_then(Value::as_str).map(String::from),
    })
}

/// Terminal outcome of a finished run, mapped onto the status strings the UI
/// renders. Velo reports a structured loop status rather than "done", and its
/// non-DONE statuses are results the user must see, not successes.
fn outcome_of(result: &Value) -> (bool, &'static str) {
    match result.get("status").and_then(Value::as_str).unwrap_or("") {
        "done" | "DONE" | "ASK_USER" => (true, "completed"),
        "cancelled" | "STOPPED" => (false, "cancelled"),
        _ => (false, "failed"),
    }
}

/// Prefer the sidecar's authoritative answer, falling back to what streamed.
/// Deep Agent streams its reply token by token; Velo produces one at the end.
fn answer_from(result: &Value, streamed: &str) -> String {
    match result
        .get("response")
        .and_then(Value::as_str)
        .map(str::trim)
    {
        Some(text) if !text.is_empty() => text.to_string(),
        _ => streamed.to_string(),
    }
}

/// Run one turn to completion. Always calls `app_state::agent_finished`, which
/// persists the exchange (with its producing agent) and closes the state
/// machine.
pub async fn stream_turn(
    app: AppHandle,
    message_id: String,
    agent_id: String,
    agent_name: String,
    text: String,
    thread_id: String,
) {
    let live = Arc::new(Mutex::new(Streamed::default()));
    let emitter = app.clone();

    let sink = {
        let live = live.clone();
        let message_id = message_id.clone();
        let agent_name = agent_name.clone();
        move |frame: Value| {
            let _ = emitter.emit("sani://core-event", frame.clone());
            let Some(event) = parse(&frame) else { return };
            let mut stream = live.lock().unwrap_or_else(|poisoned| poisoned.into_inner());
            if event.kind == "agent.started" && stream.run_id.is_empty() {
                stream.run_id = event.run_id.clone();
                let _ = emit_start(
                    &emitter,
                    &message_id,
                    &stream.run_id,
                    &event.agent_id,
                    &agent_name,
                );
            }
            match event.kind.as_str() {
                "agent.token" => {
                    if let Some(delta) = &event.token {
                        stream.text.push_str(delta);
                        emit_chunk(&emitter, &message_id, "text", delta, &event.agent_id);
                    }
                }
                "agent.progress" => {
                    let line = event
                        .message
                        .clone()
                        .unwrap_or_else(|| "Working…".to_string());
                    emit_chunk(&emitter, &message_id, "status", &line, &event.agent_id);
                    emit_activity(&emitter, &event.run_id, &event.agent_id, &line);
                }
                _ => {}
            }
        }
    };

    let result = crate::sani_core::run_turn(&app, &agent_id, &text, &thread_id, sink).await;

    // The sink was consumed with the run, so this read is uncontended.
    let streamed = live
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner())
        .clone();

    let (answer, ok, status, error) = match &result {
        Ok(value) => {
            let (ok, status) = outcome_of(value);
            let error = if ok {
                String::new()
            } else {
                value
                    .get("reason")
                    .and_then(Value::as_str)
                    .filter(|reason| !reason.trim().is_empty())
                    .unwrap_or("The agent could not finish this run.")
                    .to_string()
            };
            (answer_from(value, &streamed.text), ok, status, error)
        }
        Err(err) => {
            // Whatever already streamed stays in the transcript: a failed run
            // is reported as failed, never silently dropped.
            log::error!("sani-core run failed: {err}");
            (streamed.text.clone(), false, "failed", err.clone())
        }
    };

    app_state::agent_finished(
        &app,
        &message_id,
        &streamed.run_id,
        &answer,
        ok,
        status,
        error,
        &agent_id,
        &agent_name,
    );
}

fn emit_start(
    emitter: &AppHandle,
    message_id: &str,
    run_id: &str,
    agent_id: &str,
    agent_name: &str,
) -> Result<(), tauri::Error> {
    emitter.emit(
        "sani://agent-start",
        json!({
            "message_id": message_id,
            "run_id": run_id,
            "agent_id": agent_id,
            "agent_name": agent_name,
        }),
    )
}

fn emit_chunk(emitter: &AppHandle, message_id: &str, kind: &str, delta: &str, agent_id: &str) {
    let _ = emitter.emit(
        "sani://agent-chunk",
        json!({
            "message_id": message_id,
            "kind": kind,
            "delta": delta,
            "agent_id": agent_id,
        }),
    );
}

fn emit_activity(emitter: &AppHandle, run_id: &str, agent_id: &str, label: &str) {
    let _ = emitter.emit(
        "sani://activity",
        json!({
            "sequence": next_sequence(),
            "run_id": run_id,
            "agent_id": agent_id,
            "event_type": "agent.progress",
            "timestamp": app_state::now_ms(),
            "label": label,
            "status": "info",
        }),
    );
}

// ---------------------------------------------------------- agent selection

fn field(agent: &Value, key: &str) -> Option<String> {
    agent.get(key).and_then(Value::as_str).map(String::from)
}

fn capability_list(agent: &Value) -> Vec<String> {
    agent
        .get("capabilities")
        .and_then(Value::as_array)
        .map(|items| {
            items
                .iter()
                .filter_map(|item| item.as_str().map(String::from))
                .collect()
        })
        .unwrap_or_default()
}

/// The sidecar registry's descriptors. The frontend may cache this, but it is
/// never the origin of who exists.
pub async fn agents(app: &AppHandle) -> Result<Vec<Value>, String> {
    let value = crate::sani_core::core_agents(app.clone()).await?;
    Ok(value
        .get("agents")
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default())
}

/// Pick the agent that takes this turn, as `(id, name)`.
///
/// "auto" is an execution mode, not an agent: it resolves to the registered
/// agent that reasons, so new specialists need no code change here. A stored
/// id the registry no longer knows falls back to auto rather than failing the
/// turn, because the setting is older than the runtime, not the other way round.
pub fn select_agent(mode: &str, roster: &[Value]) -> Option<(String, String)> {
    if mode != "auto" {
        if let Some(chosen) = roster
            .iter()
            .find(|agent| field(agent, "id").as_deref() == Some(mode))
        {
            return Some((mode.to_string(), field(chosen, "name").unwrap_or_default()));
        }
    }
    let general = roster
        .iter()
        .find(|agent| capability_list(agent).iter().any(|c| c == "reasoning"))
        .or_else(|| roster.first())?;
    Some((
        field(general, "id")?,
        field(general, "name").unwrap_or_default(),
    ))
}

/// `auto` is retained only for older explicit settings. Every new selectable
/// value must be an id reported by the currently running core registry.
pub fn selectable_agent_mode(mode: &str, roster: &[Value]) -> bool {
    mode == "auto"
        || roster
            .iter()
            .any(|agent| field(agent, "id").as_deref() == Some(mode))
}

/// Which registered agent takes this turn.
pub async fn resolve_agent(app: &AppHandle) -> Option<(String, String)> {
    let mode = app_state::settings(app).read().agent_mode.clone();
    select_agent(&mode, &agents(app).await.ok()?)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn roster() -> Vec<Value> {
        vec![
            json!({"id":"velo","name":"Velo","capabilities":["computer-control"]}),
            json!({"id":"deep","name":"Deep Agent","capabilities":["reasoning"]}),
        ]
    }

    #[test]
    fn explicit_agent_modes_select_the_matching_registry_entry() {
        assert_eq!(select_agent("velo", &roster()), Some(("velo".into(), "Velo".into())));
        assert_eq!(select_agent("deep", &roster()), Some(("deep".into(), "Deep Agent".into())));
    }

    #[test]
    fn legacy_auto_is_compatibility_selection_not_a_new_agent() {
        assert_eq!(select_agent("auto", &roster()), Some(("deep".into(), "Deep Agent".into())));
    }

    #[test]
    fn only_registry_ids_or_legacy_auto_are_selectable() {
        assert!(selectable_agent_mode("velo", &roster()));
        assert!(selectable_agent_mode("deep", &roster()));
        assert!(selectable_agent_mode("auto", &roster()));
        assert!(!selectable_agent_mode("invented", &roster()));
    }
}
