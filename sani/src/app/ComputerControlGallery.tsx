import ComputerControlStatus from "./ComputerControlStatus";
import type { ComputerControlSnapshot } from "../lib/tauri";

/**
 * Every computer-control state Sani can report, rendered at once.
 *
 * The status page used to be verified by reading its code, which is how it kept
 * showing macOS permissions as granted while the runtime sat permanently at
 * `not_authorized`. This view exists so each state can be looked at instead.
 */
const AUTHORITY =
  "Sani.app — the embedded driver runs inside Sani’s own responsibility chain, so it shares these two grants and has no switches of its own";

const fixture = (
  status: ComputerControlSnapshot["status"],
  runtime: string,
  over: Partial<ComputerControlSnapshot> = {},
): ComputerControlSnapshot => ({
  status,
  message: `Fixture for the ${status.replaceAll("_", " ")} state.`,
  accessibility: "granted",
  screen_recording: "granted",
  permission_authority: AUTHORITY,
  driver_running: true,
  driver_pid: 4242,
  driver_endpoint: "/Users/sayan/Library/Application Support/app.sani.local/cua-driver.sock",
  driver_mode: "standard",
  driver_probe: "granted",
  driver_detail: "",
  active_sessions: 0,
  restart_required: false,
  runtime,
  app_path: "/Applications/Sani.app",
  ...over,
});

const STATES: ComputerControlSnapshot[] = [
  fixture("ready", "connected"),
  fixture("permission_required", "waiting_for_permission", {
    accessibility: "denied",
    screen_recording: "denied",
  }),
  fixture("restart_required", "restart_required", {
    accessibility: "granted",
    screen_recording: "granted",
    restart_required: true,
  }),
  fixture("driver_permission_required", "not_authorized", { driver_probe: "denied" }),
  fixture("policy_locked", "policy_locked", {
    driver_mode: "bounded",
    driver_probe: "policy_locked",
    driver_detail: "Policy loading error: capability manifest idle timeout exceeded",
  }),
  fixture("driver_missing", "missing", {
    driver_running: false,
    driver_pid: null,
    driver_endpoint: "",
    driver_probe: "unreachable",
    driver_detail: "Sani’s packaged computer-control driver is missing",
    active_sessions: null,
  }),
  fixture("driver_stopped", "disconnected", {
    driver_running: false,
    driver_probe: "unanswered",
    driver_detail: "the driver did not answer the read-only probe within its budget",
    active_sessions: null,
  }),
  fixture("wrong_mode", "wrong_mode", {
    driver_mode: "standard",
    driver_probe: "unanswered",
    driver_detail:
      "CuaDriver daemon is not in standard mode (permission mode: bounded (trusted_startup_configuration))",
  }),
  fixture("unavailable", "unavailable", {
    driver_running: false,
    driver_pid: null,
    driver_probe: "unreachable",
    active_sessions: null,
  }),
];

export default function ComputerControlGallery() {
  // `?control-states=1&full=1` renders the wide three-group layout the standalone
  // page uses; the default shows the compact Settings variant.
  const compact = !new URLSearchParams(window.location.search).has("full");
  return (
    <div className="cc-gallery">
      <header>
        <h1>Computer control states</h1>
        <p>
          Every state Sani’s status model can produce, rendered by the same component the page uses
          {compact ? " in its compact Settings variant." : " in its full-page variant."}
        </p>
      </header>
      {/* The state before the first read lands -- what the page shows if Sani's
          status call has not answered yet. */}
      <ComputerControlStatus snapshot={null} onRefresh={() => undefined} compact={compact} />
      {STATES.map((snapshot) => (
        <ComputerControlStatus
          key={snapshot.status}
          snapshot={snapshot}
          onRefresh={() => undefined}
          compact={compact}
        />
      ))}
    </div>
  );
}
