import {
  openPermissionSettings,
  requestAccessibility,
  requestScreenRecording,
  restartSani,
  type ComputerControlSnapshot,
} from "../lib/tauri";

export type ControlTone = "ok" | "warn" | "bad";

/**
 * The one place that decides how each computer-control state looks. The page and
 * the Settings card both render this component, because the two used to disagree
 * with each other about the same facts.
 */
const PRESENTATION: Record<string, { label: string; tone: ControlTone }> = {
  ready: { label: "Ready", tone: "ok" },
  restart_required: { label: "Restart Sani to finish", tone: "warn" },
  permission_required: { label: "Waiting for permission", tone: "warn" },
  driver_missing: { label: "Driver not installed", tone: "bad" },
  driver_stopped: { label: "Driver stopped", tone: "bad" },
  wrong_mode: { label: "Driver in the wrong mode", tone: "bad" },
  driver_permission_required: { label: "macOS refused the driver", tone: "bad" },
  policy_locked: { label: "Driver policy expired", tone: "bad" },
  unavailable: { label: "Assistant runtime unavailable", tone: "bad" },
};

export const controlPresentation = (status?: string): { label: string; tone: ControlTone } =>
  (status && PRESENTATION[status]) || { label: "Checking…", tone: "warn" };

const phrase = (value: string) => value.replaceAll("_", " ");

const permissionLabel = (value?: string) =>
  value === "granted" ? "Granted" : value === "denied" ? "Not granted" : value ? phrase(value) : "checking";

function Row({ label, value, title }: { label: string; value: string; title?: string }) {
  return (
    <div className="cc-row">
      <dt>{label}</dt>
      <dd title={title ?? value}>{value}</dd>
    </div>
  );
}

/**
 * Live status of macOS permissions, Sani's embedded driver and the assistant
 * runtime, with one actionable control per failure.
 *
 * Nothing here is inferred in the renderer: every value is read by Sani and
 * pushed or pulled. Sani's grants also cover the driver, because the driver runs
 * inside Sani's own macOS responsibility chain — so there is deliberately one
 * pair of permission rows, not two that can contradict each other.
 */
export default function ComputerControlStatus({
  snapshot,
  onRefresh,
  compact = false,
}: {
  snapshot: ComputerControlSnapshot | null;
  onRefresh: () => void;
  compact?: boolean;
}) {
  const { label, tone } = controlPresentation(snapshot?.status);
  const granted = snapshot ? snapshot.accessibility === "granted" && snapshot.screen_recording === "granted" : false;
  const endpoint = snapshot?.driver_endpoint || "";
  // Only the part that identifies the endpoint: the full path stays in the
  // tooltip, and a wrapped absolute path made the driver column ragged.
  const shortEndpoint = endpoint.split("/").filter(Boolean).slice(-2).join("/");

  return (
    <section className={`cc-card cc-tone-${tone}`} aria-label="Computer control status">
      <header className="cc-banner">
        <span className={`cc-pill cc-pill-${tone}`}>{label}</span>
        <p className="cc-message">
          {snapshot?.message ?? "Reading macOS permissions and Sani’s runtime status…"}
        </p>
        <button className="cc-refresh" onClick={onRefresh} type="button">
          Refresh
        </button>
      </header>

      <div className={`cc-grid${compact ? " cc-grid-compact" : ""}`}>
        <div className="cc-group">
          <h3>macOS permissions</h3>
          <dl>
            <Row label="Accessibility" value={permissionLabel(snapshot?.accessibility)} />
            <Row label="Screen Recording" value={permissionLabel(snapshot?.screen_recording)} />
          </dl>
          {snapshot && <p className="cc-note">{phrase(snapshot.permission_authority)}</p>}
        </div>

        <div className="cc-group">
          <h3>Driver</h3>
          <dl>
            <Row
              label="Process"
              value={snapshot ? (snapshot.driver_running ? `running · pid ${snapshot.driver_pid ?? "?"}` : "not running") : "checking"}
            />
            <Row label="Mode" value={snapshot ? phrase(snapshot.driver_mode) : "checking"} />
            <Row label="Endpoint" value={snapshot ? shortEndpoint || "—" : "checking"} title={endpoint} />
            <Row label="Driver probe" value={snapshot ? phrase(snapshot.driver_probe) : "checking"} />
            <Row
              label="Active sessions"
              value={
                snapshot
                  ? snapshot.active_sessions === null
                    ? "unknown"
                    : String(snapshot.active_sessions)
                  : "checking"
              }
            />
          </dl>
          {snapshot?.driver_detail && <code className="cc-detail">{snapshot.driver_detail}</code>}
        </div>

        <div className="cc-group">
          <h3>Assistant runtime</h3>
          <dl>
            <Row label="Status" value={snapshot ? phrase(snapshot.runtime) : "checking"} />
            <Row label="App being checked" value={snapshot?.app_path ?? "checking"} title={snapshot?.app_path} />
          </dl>
        </div>
      </div>

      <div className="cc-actions">
        {!granted && (
          <>
            <button type="button" onClick={() => void requestAccessibility().then(onRefresh)}>
              Request Accessibility
            </button>
            <button type="button" onClick={() => void requestScreenRecording().then(onRefresh)}>
              Request Screen Recording
            </button>
          </>
        )}
        <button type="button" onClick={() => openPermissionSettings("accessibility")}>
          Open Accessibility settings
        </button>
        <button type="button" onClick={() => openPermissionSettings("screen_recording")}>
          Open Screen Recording settings
        </button>
        {snapshot?.restart_required && (
          <button type="button" className="cc-primary" onClick={() => void restartSani()}>
            Restart Sani
          </button>
        )}
      </div>
      <p className="cc-hint">
        macOS grants permissions to this app by code identity, so a rebuilt Sani asks once again. The driver
        shares Sani’s grants and needs none of its own.
      </p>
    </section>
  );
}
