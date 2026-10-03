import { CheckCircle2, CircleAlert, Lock, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { SettingsGroup, SettingsRow } from "@/components/settings-rows";
import {
  openPermissionSettings,
  requestAccessibility,
  requestScreenRecording,
  restartSani,
  type ComputerControlSnapshot,
} from "@/lib/tauri";

export type ControlTone = "ok" | "warn" | "bad";

/**
 * The one place that decides how each computer-control state looks. The page and
 * Settings both render this component, because the two used to disagree with
 * each other about the same facts. Waiting states are neutral with a lock;
 * red is reserved for real failures (DESIGN C5).
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

const phrase = (value: string) => {
  const text = value.replaceAll("_", " ");
  return text.charAt(0).toUpperCase() + text.slice(1);
};

const permissionLabel = (value?: string) =>
  value === "granted" ? "Granted" : value === "denied" ? "Not granted" : value ? phrase(value) : "Checking…";

/**
 * Live status of macOS permissions, Sani's embedded driver and the assistant
 * runtime, with one actionable control per failure.
 *
 * Nothing here is inferred in the renderer: every value is read by Sani and
 * pushed or pulled. Sani's grants also cover the driver, because the driver runs
 * inside Sani's own macOS responsibility chain, so there is deliberately one
 * pair of permission rows, not two that can contradict each other.
 */
export default function ComputerControlStatus({
  snapshot,
  onRefresh,
}: {
  snapshot: ComputerControlSnapshot | null;
  onRefresh: () => void;
  /** Kept for existing callers; the layout is already compact. */
  compact?: boolean;
}) {
  const { label, tone } = controlPresentation(snapshot?.status);
  const granted = snapshot ? snapshot.accessibility === "granted" && snapshot.screen_recording === "granted" : false;
  const endpoint = snapshot?.driver_endpoint || "";
  const shortEndpoint = endpoint.split("/").filter(Boolean).slice(-2).join("/");
  const Icon = tone === "ok" ? CheckCircle2 : tone === "warn" ? Lock : CircleAlert;

  return (
    <div aria-label="Computer control status" className="flex flex-col">
      <div className="mb-5 flex items-start gap-3 rounded-xl border border-border bg-card px-3.5 py-3">
        <Icon
          className={tone === "bad" ? "mt-0.5 size-4 shrink-0 text-destructive" : "mt-0.5 size-4 shrink-0 text-muted-foreground"}
          aria-hidden="true"
        />
        <div className="min-w-0 flex-1">
          <div className="font-medium text-foreground">{label}</div>
          <p className="text-sm text-muted-foreground">
            {snapshot?.message ?? "Reading macOS permissions and Sani’s runtime status…"}
          </p>
        </div>
        <Button type="button" variant="ghost" size="sm" onClick={onRefresh} className="shrink-0 text-muted-foreground">
          <RefreshCw className="size-3.5" aria-hidden="true" />
          Refresh
        </Button>
      </div>

      <SettingsGroup title="macOS permissions">
        <SettingsRow label="Accessibility" state={permissionLabel(snapshot?.accessibility)}>
          {snapshot?.accessibility !== "granted" ? (
            <Button size="sm" variant="outline" onClick={() => void requestAccessibility().then(onRefresh)}>
              Request access
            </Button>
          ) : null}
          <Button size="sm" variant="ghost" onClick={() => void openPermissionSettings("accessibility")}>
            Open settings
          </Button>
        </SettingsRow>
        <SettingsRow label="Screen Recording" state={permissionLabel(snapshot?.screen_recording)}>
          {snapshot?.screen_recording !== "granted" ? (
            <Button size="sm" variant="outline" onClick={() => void requestScreenRecording().then(onRefresh)}>
              Request access
            </Button>
          ) : null}
          <Button size="sm" variant="ghost" onClick={() => void openPermissionSettings("screen_recording")}>
            Open settings
          </Button>
        </SettingsRow>
      </SettingsGroup>

      <SettingsGroup title="Driver">
        <SettingsRow
          label="Process"
          state={snapshot ? (snapshot.driver_running ? `Running · pid ${snapshot.driver_pid ?? "?"}` : "Not running") : "Checking…"}
        />
        <SettingsRow label="Mode" state={snapshot ? phrase(snapshot.driver_mode) : "Checking…"} />
        <SettingsRow label="Endpoint" state={snapshot ? shortEndpoint || "—" : "Checking…"} />
        <SettingsRow label="Driver probe" state={snapshot ? phrase(snapshot.driver_probe) : "Checking…"} />
        <SettingsRow
          label="Active sessions"
          state={snapshot ? (snapshot.active_sessions === null ? "Unknown" : String(snapshot.active_sessions)) : "Checking…"}
        />
        {snapshot?.driver_detail ? (
          <div className="px-3.5 py-2.5">
            <code className="block font-mono text-xs break-words text-muted-foreground select-text">
              {snapshot.driver_detail}
            </code>
          </div>
        ) : null}
      </SettingsGroup>

      <SettingsGroup title="Assistant runtime">
        <SettingsRow label="Status" state={snapshot ? phrase(snapshot.runtime) : "Checking…"} />
        <SettingsRow label="App being checked" state={snapshot?.app_path ?? "Checking…"} />
      </SettingsGroup>

      {snapshot?.restart_required ? (
        <div className="flex justify-end">
          <Button onClick={() => void restartSani()}>Restart Sani</Button>
        </div>
      ) : null}
      {!granted && snapshot ? (
        <p className="mt-2 text-xs text-muted-foreground">
          macOS grants permissions by code identity, so a rebuilt Sani asks once again. The driver shares
          Sani’s grants.
        </p>
      ) : null}
    </div>
  );
}
