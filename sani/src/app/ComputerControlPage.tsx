import { useEffect, useState } from "react";
import {
  computerControlSnapshot,
  onComputerControlChange,
  type ComputerControlSnapshot,
} from "../lib/tauri";
import ComputerControlStatus from "./ComputerControlStatus";

export default function ComputerControlPage() {
  const [snapshot, setSnapshot] = useState<ComputerControlSnapshot | null>(null);

  const refresh = () =>
    void computerControlSnapshot().then(setSnapshot).catch(() => setSnapshot(null));

  useEffect(() => {
    let live = true;
    let stop: (() => void) | undefined;
    refresh();
    // Sani pushes when its cheap signals change, and the page answers by pulling
    // a real reading -- so a grant given in System Settings, or a driver the
    // watchdog just replaced, shows up without touching Refresh.
    void onComputerControlChange(refresh).then((unlisten) => {
      if (live) stop = unlisten;
      else unlisten();
    });
    const onVisible = () => {
      if (document.visibilityState === "visible") refresh();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      live = false;
      stop?.();
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, []);

  return (
    <section className="cc-page">
      <header className="cc-header">
        <div>
          <h1>Computer Control</h1>
          <p>
            Sani’s own macOS permissions, its embedded driver and the assistant runtime. Read locally, and
            refreshed whenever Sani reports a change.
          </p>
        </div>
      </header>
      <ComputerControlStatus snapshot={snapshot} onRefresh={refresh} />
    </section>
  );
}
