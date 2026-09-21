import type { ActivityEvent } from "../lib/tauri";

interface ActivityTimelineProps {
  events: ActivityEvent[];
  status: string;
  working: boolean;
}

const STATUS_COLOR: Record<string, string> = {
  running: "var(--running)",
  complete: "var(--success)",
  failed: "var(--error)",
  cancelled: "var(--warning)",
  unknown: "var(--warning)",
  info: "var(--text-dim)",
};

const EVENT_ICON: Record<string, string> = {
  running: "●",
  complete: "✓",
  failed: "✕",
  cancelled: "■",
  unknown: "!",
  info: "·",
};

function clock(ts: string): string {
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleTimeString(undefined, { hour12: false });
}

/**
 * Real observable activity rows (from the gateway's safe run-event stream)
 * plus the deterministic gateway status line while streaming.
 */
export default function ActivityTimeline({ events, status, working }: ActivityTimelineProps) {
  return (
    <div className="activity">
      <div className="activity-header">
        <span className="activity-spinner" />
        <span>{working ? status || "Agent working…" : "Activity"}</span>
      </div>
      {events.map((e) => (
        <div key={`${e.run_id}-${e.sequence}`} className="activity-row">
          <span className="activity-icon" style={{ color: STATUS_COLOR[e.status] ?? "var(--text-dim)" }}>
            {EVENT_ICON[e.status] ?? "·"}
          </span>
          <span className="activity-time">{clock(e.timestamp)}</span>
          <span className="activity-label">
            {e.label}
            {e.duration_ms ? <span className="activity-duration"> · {(e.duration_ms / 1000).toFixed(1)}s</span> : null}
            {e.detail ? <span className="activity-detail"> — {e.detail}</span> : null}
          </span>
        </div>
      ))}
    </div>
  );
}
