//! Mission truth for the host UI (Jarvis Phase 1, T08).
//!
//! The core's turn-delivery status and the mission's durable status are two
//! different facts (file 03 §3). `agent.completed` with an ASK_USER or
//! blocked mission is "your turn was delivered", never "your task is done"
//! (RF-07). This module owns the honest mapping, the control builders, the
//! approval expiry gate, and sequence-based event dedup so the UI can
//! project mission state without ever fabricating completion.

use serde_json::{json, Value};

/// The mission statuses the UI knows, mirrored from `jarvis.v1`.
pub const MISSION_STATUSES: [&str; 10] = [
    "PLANNED", "RUNNING", "WAITING_EXTERNAL", "BLOCKED", "NEEDS_APPROVAL", "PAUSED",
    "VERIFYING", "COMPLETED", "FAILED", "CANCELLED",
];

/// What the turn delivered vs. what the mission did, as UI-facing text.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct MissionTruth {
    /// True only when the mission is COMPLETED with verified checks.
    pub verified: bool,
    /// The honest mission status (unknown stays explicit).
    pub status: String,
    /// True when the core's result carried a mission_status field at all.
    pub is_mission: bool,
}

/// Map a core result payload to UI truth.
///
/// A result without `mission_status` is a legacy/chat turn: delivered, but
/// never claimed as a verified mission.
pub fn mission_truth(result: &Value) -> MissionTruth {
    let Some(status) = result.get("mission_status").and_then(Value::as_str) else {
        return MissionTruth {
            verified: false,
            status: "UNKNOWN".to_string(),
            is_mission: false,
        };
    };
    MissionTruth {
        verified: result
            .get("verified")
            .and_then(Value::as_bool)
            .unwrap_or(false),
        status: normalize_status(status),
        is_mission: true,
    }
}

/// Normalize the status so "unknown" stays explicit rather than collapsing
/// into a success-shaped default.
pub fn normalize_status(status: &str) -> String {
    if MISSION_STATUSES.contains(&status) {
        status.to_string()
    } else {
        "UNKNOWN".to_string()
    }
}

/// Build a mission.control payload with compare-and-swap expectations.
///
/// The renderer may REQUEST a control; the core still validates it against
/// the durable plan/epoch (the renderer cannot forge authority).
pub fn control_payload(
    mission_id: &str,
    kind: &str,
    expected_plan_version: i64,
    expected_control_epoch: i64,
    reason: &str,
) -> Value {
    json!({
        "control_id": uuid::Uuid::new_v4().to_string(),
        "mission_id": mission_id,
        "expected_plan_version": expected_plan_version,
        "expected_control_epoch": expected_control_epoch,
        "kind": kind,
        "reason": reason,
    })
}

/// An approval the UI is showing the user for confirmation.
#[derive(Debug, Clone, PartialEq)]
pub struct ApprovalView {
    pub approval_id: String,
    pub action_digest: String,
    pub effect_class: String,
    pub expires_at_ms: i64,
}

/// A stale UI approval can never be submitted to the core: expiry is
/// checked on the host side as well as at the core's consume gate.
pub fn approval_is_current(view: &ApprovalView, now_ms: i64) -> bool {
    view.expires_at_ms > now_ms && !view.approval_id.is_empty()
}

/// Project mission events into the UI with sequence-based dedup.
///
/// Returns the events that are new (sequence > cursor), plus the new cursor.
pub fn project_events(events: &[Value], cursor: i64) -> (Vec<Value>, i64) {
    let mut projected: Vec<Value> = Vec::new();
    let mut new_cursor = cursor;
    for event in events {
        let Some(sequence) = event.get("sequence").and_then(Value::as_i64) else {
            continue;
        };
        if sequence <= cursor {
            continue;
        }
        projected.push(event.clone());
        if sequence > new_cursor {
            new_cursor = sequence;
        }
    }
    (projected, new_cursor)
}

#[cfg(test)]
mod tests {
    use super::*;

    // RF-07: agent.completed arrives with ASK_USER/UNKNOWN; the host renders
    // turn delivered and mission waiting/blocked, never completed.
    #[test]
    fn completed_turn_with_ask_user_mission_is_not_completed() {
        let result = json!({
            "status": "ASK_USER",
            "mission_status": "NEEDS_APPROVAL",
            "verified": false,
        });
        let truth = mission_truth(&result);
        assert!(truth.is_mission);
        assert_eq!(truth.status, "NEEDS_APPROVAL");
        assert!(!truth.verified);
    }

    #[test]
    fn completed_turn_with_blocked_mission_is_not_completed() {
        let result = json!({"status": "blocked", "mission_status": "BLOCKED"});
        let truth = mission_truth(&result);
        assert_eq!(truth.status, "BLOCKED");
        assert!(!truth.verified);
    }

    #[test]
    fn verified_completion_is_verified() {
        let result = json!({
            "status": "done",
            "mission_status": "COMPLETED",
            "verified": true,
        });
        let truth = mission_truth(&result);
        assert_eq!(truth.status, "COMPLETED");
        assert!(truth.verified);
    }

    #[test]
    fn unknown_mission_status_stays_explicit() {
        let truth = mission_truth(&json!({"mission_status": "SOMETHING_NEW"}));
        assert_eq!(truth.status, "UNKNOWN");
        // An unstatused result is not a mission at all.
        let truth = mission_truth(&json!({"status": "done"}));
        assert!(!truth.is_mission);
        assert!(!truth.verified);
    }

    #[test]
    fn control_payloads_carry_cas_fields() {
        let payload = control_payload("m-1", "PAUSE", 3, 5, "user pause");
        assert_eq!(payload["mission_id"], "m-1");
        assert_eq!(payload["expected_plan_version"], 3);
        assert_eq!(payload["expected_control_epoch"], 5);
        assert!(payload["control_id"].as_str().unwrap().len() == 36);
    }

    #[test]
    fn stale_ui_approval_never_passes() {
        let view = ApprovalView {
            approval_id: "a-1".to_string(),
            action_digest: "d".to_string(),
            effect_class: "EXTERNAL_WRITE".to_string(),
            expires_at_ms: 1_000,
        };
        assert!(!approval_is_current(&view, 1_001));
        assert!(approval_is_current(&view, 999));
        let empty = ApprovalView {
            approval_id: String::new(),
            action_digest: "d".to_string(),
            effect_class: "EXTERNAL_WRITE".to_string(),
            expires_at_ms: 5_000,
        };
        assert!(!approval_is_current(&empty, 1_000));
    }

    #[test]
    fn event_projection_dedups_by_sequence() {
        let events = vec![
            json!({"sequence": 1, "event_id": "e1"}),
            json!({"sequence": 2, "event_id": "e2"}),
            json!({"sequence": 3, "event_id": "e3"}),
        ];
        let (new_events, cursor) = project_events(&events, 2);
        assert_eq!(new_events.len(), 1, "only events past the cursor project");
        assert_eq!(new_events[0]["event_id"], "e3");
        assert_eq!(cursor, 3);
        // Replaying the same batch with the new cursor yields nothing.
        let (again, cursor) = project_events(&events, cursor);
        assert!(again.is_empty());
        assert_eq!(cursor, 3);
    }
}
