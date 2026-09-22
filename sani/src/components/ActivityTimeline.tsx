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

function clock(ts: number): string {
  if (!ts) return "";
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleTimeString(undefined, { hour12: false });
}

/**
 * Observable activity rows from the sani-core event stream, each carrying the
 * id of the agent it came from.
 */
export default function ActivityTimeline({ events, status, working }: ActivityTimelineProps) {
  return (
    <div className="activity">
      <div className="activity-header">
        <span className="activity-spinner" />
        <span>{working ? status || "Thinking…" : "Activity"}</span>
      </div>
      <div className="activity-rows">
        {events.map((e) => (
          <div key={`${e.run_id}-${e.sequence}`} className="activity-row">
            <span
              className="activity-icon"
              style={{ color: STATUS_COLOR[e.status] ?? "var(--text-dim)" }}
            >
              {EVENT_ICON[e.status] ?? "·"}
            </span>
            <span className="activity-time">{clock(e.timestamp)}</span>
            <span className="activity-label">
              {e.label}
              {e.duration_ms ? (
                <span className="activity-duration"> · {(e.duration_ms / 1000).toFixed(1)}s</span>
              ) : null}
              {e.detail ? <span className="activity-detail"> — {e.detail}</span> : null}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
