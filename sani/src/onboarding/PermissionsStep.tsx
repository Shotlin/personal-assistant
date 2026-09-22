import { useCallback, useEffect, useState } from "react";
import {
  openPermissionSettings,
  permissionSnapshot,
  requestAccessibility,
  requestMicrophone,
  requestScreenRecording,
  restartApp,
} from "./api";
import type { PermissionSnapshot } from "./types";
import { Button } from "./components/ui";
import { Check, Computer, Eye, Mic } from "./components/icons";

type Perm = "microphone" | "accessibility" | "screen_recording";

const LABEL: Record<Perm, { title: string; desc: string; Icon: typeof Mic }> = {
  microphone: { title: "Microphone", desc: "Needed so Sani can hear you.", Icon: Mic },
  accessibility: {
    title: "Accessibility",
    desc: "Needed when you ask Sani to click, type, or control apps.",
    Icon: Computer,
  },
  screen_recording: {
    title: "Screen Recording",
    desc: "Needed so Sani can understand what's visible on your screen.",
    Icon: Eye,
  },
};

const granted = (snap: PermissionSnapshot | null, p: Perm) =>
  !!snap && snap[p] === "granted";

export default function PermissionsStep({ onContinue }: { onContinue: () => void }) {
  const [snap, setSnap] = useState<PermissionSnapshot | null>(null);

  const refresh = useCallback(async () => setSnap(await permissionSnapshot()), []);

  useEffect(() => {
    void refresh();
    const id = window.setInterval(refresh, 800);
    const onVis = () => void refresh();
    document.addEventListener("visibilitychange", onVis);
    return () => {
      window.clearInterval(id);
      document.removeEventListener("visibilitychange", onVis);
    };
  }, [refresh]);

  const count = (["microphone", "accessibility", "screen_recording"] as Perm[]).filter((p) =>
    granted(snap, p),
  ).length;

  const restartNeeded = !!snap?.screen_recording_restart_required;

  const act = async (p: Perm) => {
    if (p === "microphone") await requestMicrophone();
    else if (p === "accessibility") {
      await requestAccessibility();
      openPermissionSettings("accessibility");
    } else {
      await requestScreenRecording();
      openPermissionSettings("screen_recording");
    }
    // Live-detect: give the OS a beat, then poll.
    window.setTimeout(refresh, 600);
  };

  return (
    <div>
      <h1 className="onb-h1">Allow Sani to work with your Mac</h1>
      <p className="onb-sub">
        Sani needs a few system permissions for voice and computer control.
      </p>
      <p className="onb-help" style={{ marginTop: 6 }}>
        Permissions {count} of 3
      </p>

      {restartNeeded ? (
        <div className="card" style={{ borderColor: "var(--warning)" }}>
          <div className="card-title" style={{ color: "var(--warning)" }}>
            Sani needs to restart to finish enabling screen access.
          </div>
          <div style={{ marginTop: 12 }}>
            <Button className="primary" onClick={() => void restartApp()}>
              Restart Sani
            </Button>
          </div>
        </div>
      ) : null}

      <ul className="step-list" style={{ marginTop: 14 }}>
        {(Object.keys(LABEL) as Perm[]).map((p) => {
          const { title, desc, Icon } = LABEL[p];
          const ok = granted(snap, p);
          return (
            <li className="step-row" key={p} data-state={ok ? "complete" : "pending"} style={{ alignItems: "flex-start" }}>
              <span className={`step-icon ${ok ? "complete" : "pending"}`}>
                {ok ? <Check width={13} height={13} /> : <Icon width={14} height={14} />}
              </span>
              <span className="step-label">
                <span style={{ display: "block", color: "var(--text)" }}>{title}</span>
                <span className="step-note">{desc}</span>
              </span>
              <span style={{ flex: "none", display: "flex", alignItems: "center", gap: 10 }}>
                <span className={`perm-status${ok ? " granted" : ""}`}>
                  {ok ? "Enabled" : "Not enabled"}
                </span>
                {!ok ? (
                  <Button small variant="secondary" onClick={() => void act(p)}>
                    {p === "microphone" ? "Allow" : "Open Settings"}
                  </Button>
                ) : null}
              </span>
            </li>
          );
        })}
      </ul>

      <div className="onb-section">
        <Button className="primary" onClick={onContinue} disabled={restartNeeded}>
          Continue
        </Button>
      </div>
    </div>
  );
}
