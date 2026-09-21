//! Activity client: GET /v1/runs/{run_id}/events (neutral safe SSE) on the
//! gateway. Emits `sani://activity` events with only observable, safe
//! metadata: timestamps, labels, states, durations. Never tool payloads,
//! prompts, or hidden reasoning.

use futures_util::StreamExt;
use serde_json::{json, Value};
use std::time::Duration;
use tauri::{AppHandle, Emitter};

use crate::app_state::settings;

/// Gateway path prefix for the safe activity stream.
const EVENTS_PATH: &str = "/v1/runs";

pub async fn watch(app: AppHandle, run_id: String) {
    let (base, key) = {
        let s = settings(&app).read().clone();
        (s.agent_base_url.trim_end_matches('/').to_string(), s.agent_api_key.clone())
    };
    let url = format!("{base}{EVENTS_PATH}/{run_id}/events");

    let mut request = reqwest::Client::builder()
        .timeout(Duration::from_secs(1200))
        .build()
        .expect("reqwest client")
        .get(&url)
        .header("Accept", "text/event-stream");
    if !key.trim().is_empty() {
        request = request.bearer_auth(key.trim());
    }

    let response = match request.send().await {
        Ok(resp) if resp.status().is_success() => resp,
        Ok(resp) => {
            log::warn!("activity stream http {}", resp.status());
            return;
        }
        Err(err) => {
            log::warn!("activity stream unavailable: {err}");
            return;
        }
    };

    let mut buffer = String::new();
    let mut stream = response.bytes_stream();
    while let Some(chunk) = stream.next().await {
        match chunk {
            Ok(bytes) => {
                buffer.push_str(&String::from_utf8_lossy(&bytes));
                while let Some(pos) = buffer.find("\n\n") {
                    let block: String = buffer.drain(..pos + 2).collect();
                    if let Some(event) = parse_event_block(&block) {
                        // FIX-01: only these three close the stream. `run.started`
                        // and `agent.processing` are NOT terminal — treating them
                        // as such cut off every tool/activity event that follows.
                        let terminal = event
                            .get("event_type")
                            .and_then(Value::as_str)
                            .map(|t| {
                                matches!(t, "run.completed" | "run.failed" | "run.cancelled")
                            })
                            .unwrap_or(false);
                        let _ = app.emit("sani://activity", event.clone());
                        log::info!(
                            "[activity] {} {}",
                            event.get("event_type").and_then(Value::as_str).unwrap_or("?"),
                            event.get("label").and_then(Value::as_str).unwrap_or("")
                        );
                        if terminal {
                            return;
                        }
                    }
                }
            }
            Err(err) => {
                log::warn!("activity stream error: {err}");
                return;
            }
        }
    }
}

fn parse_event_block(block: &str) -> Option<Value> {
    let mut event_type = String::new();
    let mut data = String::new();
    for line in block.lines() {
        if let Some(rest) = line.strip_prefix("event: ") {
            event_type = rest.trim().to_string();
        } else if let Some(rest) = line.strip_prefix("data: ") {
            data.push_str(rest);
        } else if let Some(rest) = line.strip_prefix("data:") {
            data.push_str(rest.trim_start());
        }
    }
    if data.is_empty() {
        return None;
    }
    let mut payload: Value = serde_json::from_str(&data).ok()?;
    if let Some(obj) = payload.as_object_mut() {
        if !event_type.is_empty() {
            obj.entry("event_type".to_string())
                .or_insert(json!(event_type));
        }
        obj.entry("status".to_string()).or_insert(json!("info"));
    }
    Some(payload)
}
