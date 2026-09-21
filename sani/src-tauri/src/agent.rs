//! Agent client: POST /v1/chat/completions with streaming against the
//! existing Personal Assistant gateway, preserving its identity, thread and
//! dedup contract. Never a second agent implementation -- only transport.

use futures_util::StreamExt;
use serde_json::{json, Value};
use std::time::Duration;
use tauri::{AppHandle, Emitter};

use crate::app_state::settings;

pub const CHAT_PATH: &str = "/v1/chat/completions";
pub const STOP_PATH: &str = "/v1/runs";
const MODEL_ID: &str = "personal-assistant-v1";

fn http_client() -> reqwest::Client {
    reqwest::Client::builder()
        .timeout(Duration::from_secs(300))
        .build()
        .expect("reqwest client")
}

/// GET /healthz of the gateway.
pub async fn health(app: &AppHandle) -> bool {
    let base = settings(&app).read().agent_base_url.trim_end_matches('/').to_string();
    let url = format!("{base}/healthz");
    matches!(
        reqwest::get(&url).await,
        Ok(resp) if resp.status().is_success()
    )
}

/// Stream one assistant turn. Emits:
///   sani://agent-start  {message_id, run_id}
///   sani://agent-chunk  {message_id, kind: "text"|"status", delta}
/// and, on every terminal path, calls app_state::agent_finished (which
/// persists the exchange and emits sani://agent-done).
/// The run id is parsed from the OpenAI completion id (`chatcmpl-<run_id>`),
/// which the gateway derives from its durable run registry.
pub async fn stream_chat(app: AppHandle, message_id: String, text: String) {
    let (base, key, user_id, chat_id) = {
        let s = settings(&app).read().clone();
        (
            s.agent_base_url.trim_end_matches('/').to_string(),
            s.agent_api_key.clone(),
            "local-user".to_string(),
            s.active_conversation_id.clone(),
        )
    };
    let url = format!("{base}{CHAT_PATH}");

    let mut request = http_client()
        .post(&url)
        .header("Content-Type", "application/json")
        .header("X-Assistant-User-Id", &user_id)
        .header("X-Assistant-Chat-Id", &chat_id)
        .header("X-Assistant-Message-Id", &message_id)
        .json(&json!({
            "model": MODEL_ID,
            "messages": [{"role": "user", "content": text}],
            "stream": true
        }));
    if !key.trim().is_empty() {
        request = request.bearer_auth(key.trim());
    }

    let finish = |app: &AppHandle, message_id: &str, run_id: &str, text: &str, ok: bool, error: String| {
        crate::app_state::agent_finished(app, message_id, run_id, text, ok, error);
    };

    let response = match request.send().await {
        Ok(resp) if resp.status().is_success() => resp,
        Ok(resp) => {
            let status = resp.status();
            let body = resp.text().await.unwrap_or_default();
            log::error!("agent http {status}: {}", truncate(&body, 300));
            finish(&app, &message_id, "", "", false, format!("Agent error ({status})"));
            return;
        }
        Err(err) => {
            log::error!("agent unreachable: {err}");
            finish(&app, &message_id, "", "", false, "Agent offline".to_string());
            return;
        }
    };

    let mut stream = response.bytes_stream();
    let mut buffer = String::new();
    let mut run_id = String::new();
    let mut got_any = false;
    let mut accumulated = String::new();

    loop {
        tokio::select! {
            chunk = stream.next() => {
                match chunk {
                    Some(Ok(bytes)) => {
                        buffer.push_str(&String::from_utf8_lossy(&bytes));
                        while let Some(pos) = buffer.find("\n\n") {
                            let block: String = buffer.drain(..pos + 2).collect();
                            for event in parse_sse_block(&block) {
                                if event == "[DONE]" {
                                    finish(&app, &message_id, &run_id, &accumulated, true, String::new());
                                    return;
                                }
                                if let Ok(payload) = serde_json::from_str::<Value>(&event) {
                                    if run_id.is_empty() {
                                        if let Some(id) = payload.get("id").and_then(Value::as_str) {
                                            run_id = id
                                                .strip_prefix("chatcmpl-")
                                                .unwrap_or(id)
                                                .to_string();
                                            let _ = app.emit(
                                                "sani://agent-start",
                                                json!({"message_id": message_id, "run_id": run_id}),
                                            );
                                            // Activity is a separate stream.
                                            tauri::async_runtime::spawn(crate::activity::watch(app.clone(), run_id.clone()));
                                        }
                                    }
                                    let Some(delta) = payload
                                        .pointer("/choices/0/delta")
                                        .and_then(Value::as_object) else { continue };
                                    if let Some(content) = delta.get("content").and_then(Value::as_str) {
                                        // The gateway's deterministic status lines
                                        // ("[working] ", "[waiting for the model…]")
                                        // route to the activity UI, never into text.
                                        if content.starts_with("[working]") {
                                            let _ = app.emit("sani://agent-chunk",
                                                json!({"message_id": message_id, "kind": "status", "delta": "Agent working…"}));
                                            got_any = true;
                                        } else if content.starts_with("[waiting") {
                                            let _ = app.emit("sani://agent-chunk",
                                                json!({"message_id": message_id, "kind": "status", "delta": "Waiting for the model…"}));
                                            got_any = true;
                                        } else if !content.is_empty() {
                                            accumulated.push_str(content);
                                            let _ = app.emit("sani://agent-chunk",
                                                json!({"message_id": message_id, "kind": "text", "delta": content}));
                                            got_any = true;
                                        }
                                    }
                                }
                            }
                        }
                    }
                    Some(Err(err)) => {
                        log::error!("agent stream error: {err}");
                        let error = if got_any {
                            "Stream interrupted; partial answer kept.".to_string()
                        } else {
                            "Agent stream failed.".to_string()
                        };
                        finish(&app, &message_id, &run_id, &accumulated, got_any, error);
                        return;
                    }
                    None => {
                        // Server closed without [DONE]: treat as ended, keep text.
                        finish(&app, &message_id, &run_id, &accumulated, true, String::new());
                        return;
                    }
                }
            }
            _ = cancel_requested(&app, &message_id) => {
                log::info!("agent stream cancelled locally");
                let error = if got_any {
                    "Stopped; partial answer kept.".to_string()
                } else {
                    "Cancelled.".to_string()
                };
                finish(&app, &message_id, &run_id, &accumulated, got_any, error);
                if !run_id.is_empty() {
                    stop_run(&app, &run_id).await;
                }
                return;
            }
        }
    }
}

/// cancellation polling handle: resolves when the app_state in-flight run
/// for this message is flagged cancelled.
async fn cancel_requested(app: &AppHandle, message_id: &str) {
    loop {
        if crate::app_state::is_cancelled(app, message_id) {
            return;
        }
        tokio::time::sleep(Duration::from_millis(150)).await;
    }
}

/// POST /v1/runs/{run_id}/stop on the gateway (no second cancel system).
pub async fn stop_run(app: &AppHandle, run_id: &str) {
    let (base, key) = {
        let s = settings(&app).read().clone();
        (s.agent_base_url.trim_end_matches('/').to_string(), s.agent_api_key.clone())
    };
    let url = format!("{base}{STOP_PATH}/{run_id}/stop");
    let mut request = http_client().post(&url);
    if !key.trim().is_empty() {
        request = request.bearer_auth(key.trim());
    }
    match request.send().await {
        Ok(resp) => log::info!("run stop requested: {run_id} -> {}", resp.status()),
        Err(err) => log::warn!("run stop failed: {err}"),
    }
}

fn parse_sse_block(block: &str) -> Vec<String> {
    let mut data_lines: Vec<String> = Vec::new();
    for line in block.lines() {
        if let Some(data) = line.strip_prefix("data: ") {
            data_lines.push(data.to_string());
        } else if let Some(data) = line.strip_prefix("data:") {
            data_lines.push(data.trim_start().to_string());
        }
    }
    if data_lines.is_empty() {
        Vec::new()
    } else {
        vec![data_lines.join("\n")]
    }
}

fn truncate(s: &str, max: usize) -> &str {
    match s.char_indices().nth(max) {
        Some((idx, _)) => &s[..idx],
        None => s,
    }
}
