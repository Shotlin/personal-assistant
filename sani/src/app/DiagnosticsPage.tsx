import { useEffect, useState } from "react";
import { RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { SettingsGroup, SettingsRow } from "@/components/settings-rows";
import { computerControlSnapshot, coreStatus, recentRunTiming, type ComputerControlSnapshot, type TimingRecord } from "@/lib/tauri";

/** Local operational evidence only. Sani never records content or credentials here. */
export default function DiagnosticsPage() {
  const [timings, setTimings] = useState<TimingRecord[]>([]);
  const [control, setControl] = useState<ComputerControlSnapshot | null>(null);
  const [runtime, setRuntime] = useState("Not measured");
  const refresh = () => {
    void recentRunTiming().then(setTimings).catch(() => setTimings([]));
    void computerControlSnapshot().then(setControl).catch(() => setControl(null));
    void coreStatus().then(() => setRuntime("Available")).catch(() => setRuntime("Unavailable"));
  };
  useEffect(refresh, []);
  return (
    <div>
      <div className="mb-4 flex justify-end">
        <Button variant="ghost" size="sm" onClick={refresh} className="text-muted-foreground">
          <RefreshCw className="size-3.5" aria-hidden="true" />
          Refresh
        </Button>
      </div>
      <SettingsGroup title="Status">
        <SettingsRow label="Runtime" state={runtime} />
        <SettingsRow label="Computer control" state={control?.message ?? "Not measured"} />
      </SettingsGroup>
      <SettingsGroup title="Recent measured stages">
        {timings.length === 0 ? (
          <SettingsRow label="Nothing measured yet" state="Complete a run to collect local timing evidence" />
        ) : (
          timings.map((timing) => (
            <SettingsRow
              key={`${timing.run_id}-${timing.stage}`}
              label={timing.stage.replaceAll("_", " ")}
              state={`${timing.elapsed_ms} ms · ${timing.status}`}
            />
          ))
        )}
      </SettingsGroup>
    </div>
  );
}
