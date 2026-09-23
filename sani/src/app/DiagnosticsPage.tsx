import { useEffect, useState } from "react";
import { computerControlSnapshot, coreStatus, recentRunTiming, type ComputerControlSnapshot, type TimingRecord } from "../lib/tauri";

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
  return <section className="diagnostics-page"><header><div><h1>Diagnostics</h1><p>Local operational evidence only. Sani never records content or credentials here.</p></div><button onClick={refresh}>Refresh</button></header><div className="diagnostic-grid"><article><h2>Runtime</h2><p>{runtime}</p></article><article><h2>Computer control</h2><p>{control?.message ?? "Not measured"}</p></article></div><h2>Recent measured stages</h2>{timings.length === 0 ? <p className="settings-muted">Not measured — complete a Sani run to collect local timing evidence.</p> : <div className="timing-list">{timings.map((timing) => <article key={`${timing.run_id}-${timing.stage}`}><strong>{timing.stage.replaceAll("_", " ")}</strong><span>{timing.elapsed_ms} ms · {timing.status}</span></article>)}</div>}</section>;
}
