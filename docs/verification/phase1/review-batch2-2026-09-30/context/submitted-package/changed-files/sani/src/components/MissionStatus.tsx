/**
 * MissionStatus — the truthful mission status line (Jarvis Phase 1, T08).
 *
 * Renders the DURABLE mission status from sani-core, never a fabricated
 * completion: a turn whose reply arrived but whose mission is blocked or
 * waiting shows that fact. Unknown statuses render as "Unknown" explicitly.
 */

export type MissionStatusValue =
  | "PLANNED"
  | "RUNNING"
  | "WAITING_EXTERNAL"
  | "BLOCKED"
  | "NEEDS_APPROVAL"
  | "PAUSED"
  | "VERIFYING"
  | "COMPLETED"
  | "FAILED"
  | "CANCELLED"
  | "UNKNOWN";

export interface MissionApprovalView {
  approvalId: string;
  effectClass: string;
  targetSummary: string;
  scopeSummary: string;
  expiresAtMs: number;
}

interface MissionStatusProps {
  status: MissionStatusValue | string | null;
  verified?: boolean;
  missionId?: string | null;
  approval?: MissionApprovalView | null;
  onControl?: (kind: "PAUSE" | "RESUME" | "CANCEL") => void;
  /** D10: revision and priority travel the SAME host IPC control path; the
   * core validates the CAS and screens the revision text. */
  onRevise?: (revision: string) => void;
  onSetPriority?: (priority: number) => void;
}

const STATUS_LABEL: Record<string, string> = {
  PLANNED: "Planned",
  RUNNING: "Working",
  WAITING_EXTERNAL: "Waiting for an external result",
  BLOCKED: "Blocked",
  NEEDS_APPROVAL: "Needs your approval",
  PAUSED: "Paused",
  VERIFYING: "Verifying",
  COMPLETED: "Done — verified",
  FAILED: "Failed",
  CANCELLED: "Cancelled",
  UNKNOWN: "Unknown",
};

const STATUS_TONE: Record<string, "ok" | "busy" | "warn" | "bad" | "dim"> = {
  PLANNED: "dim",
  RUNNING: "busy",
  WAITING_EXTERNAL: "busy",
  VERIFYING: "busy",
  COMPLETED: "ok",
  FAILED: "bad",
  CANCELLED: "dim",
  BLOCKED: "warn",
  NEEDS_APPROVAL: "warn",
  PAUSED: "warn",
  UNKNOWN: "dim",
};

const CONTROL_WHEN: Record<string, Array<"PAUSE" | "RESUME" | "CANCEL">> = {
  PLANNED: ["PAUSE", "CANCEL"],
  RUNNING: ["PAUSE", "CANCEL"],
  WAITING_EXTERNAL: ["PAUSE", "CANCEL"],
  PAUSED: ["RESUME", "CANCEL"],
  NEEDS_APPROVAL: ["RESUME", "CANCEL"],
  BLOCKED: ["CANCEL"],
};

function labelFor(status: string): string {
  return STATUS_LABEL[status] ?? "Unknown";
}

function toneFor(status: string): string {
  const tone = STATUS_TONE[status] ?? "dim";
  return `mission-status mission-status--${tone}`;
}

/**
 * The one rule this component exists for: a delivered reply is not a
 * verified outcome. `verified` comes from the core result and only a
 * COMPLETED mission with passed checks sets it.
 */
export default function MissionStatus({
  status,
  verified = false,
  missionId = null,
  approval = null,
  onControl,
  onRevise,
  onSetPriority,
}: MissionStatusProps) {
  if (!status) return null;

  const normalized = (STATUS_LABEL[status] ? status : "UNKNOWN") as string;
  const controls = onControl ? CONTROL_WHEN[normalized] ?? [] : [];
  const revisable =
    onRevise !== undefined &&
    ["PLANNED", "RUNNING", "PAUSED", "NEEDS_APPROVAL", "WAITING_EXTERNAL"].includes(normalized);
  const priorityAdjustable = onSetPriority !== undefined && !["COMPLETED", "FAILED", "CANCELLED"].includes(normalized);

  return (
    <div className={toneFor(normalized)} data-mission-id={missionId ?? undefined}>
      <span className="mission-status__label">
        {labelFor(normalized)}
        {normalized === "COMPLETED" && !verified ? " (unverified)" : ""}
      </span>
      {approval && (
        <span className="mission-status__approval">
          {approval.effectClass} on {approval.targetSummary} · scope{" "}
          {approval.scopeSummary} · expires{" "}
          {new Date(approval.expiresAtMs).toLocaleTimeString()}
        </span>
      )}
      {controls.map((kind) => (
        <button
          key={kind}
          type="button"
          className="mission-status__control"
          onClick={() => onControl?.(kind)}
        >
          {kind === "PAUSE" ? "Pause" : kind === "RESUME" ? "Resume" : "Cancel"}
        </button>
      ))}
      {revisable && (
        <button
          type="button"
          className="mission-status__control"
          onClick={() => {
            const revision = window.prompt("Revise this task — tell Sani what changed:");
            const text = (revision ?? "").trim();
            if (text) onRevise?.(text);
          }}
        >
          Revise
        </button>
      )}
      {priorityAdjustable && (
        <select
          aria-label="Task priority"
          className="mission-status__control"
          defaultValue=""
          onChange={(event) => {
            const value = event.target.value;
            if (value === "") return;
            onSetPriority?.(Number(value));
            event.target.value = "";
          }}
        >
          <option value="" disabled>Priority…</option>
          {[0, 1, 2, 3, 4, 5, 6, 7, 8, 9].map((n) => (
            <option key={n} value={n}>{n}</option>
          ))}
        </select>
      )}
    </div>
  );
}
