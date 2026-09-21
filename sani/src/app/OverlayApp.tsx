import { useCallback, useEffect, useState } from "react";
import Waveform from "../components/Waveform";
import {
  getState,
  micPermissionState,
  onLevel,
  onMicError,
  onMicPermission,
  onState,
  onSttError,
  onSttStatus,
  openMicSettings,
  pressEscape,
  requestMicPermission,
  sendUiCommand,
  startListening,
  stopListening,
  togglePanel,
  type MicPermission,
  type UiState,
} from "../lib/tauri";
import "../styles/tokens.css";
import "../styles/pill.css";

const STATE_TEXT: Record<UiState, string> = {
  idle: "Ready",
  preparing: "Preparing…",
  listening: "Listening...",
  finalizing: "Finalizing...",
  working: "Working...",
  error: "Error",
};

const STATE_COLOR: Record<UiState, string> = {
  idle: "#37d487",
  preparing: "#f0ad4e",
  listening: "#37d487",
  finalizing: "#f0ad4e",
  working: "#5b9bff",
  error: "#ff6a63",
};

/**
 * Ungranted macOS TCC access is surfaced as an explicit action, never as a
 * silent "Listening" state (RC-04).
 */
function permissionPrompt(perm: MicPermission): { label: string; cta: string } | null {
  if (perm === "denied" || perm === "restricted") {
    return { label: "Mic access blocked", cta: "Open System Settings" };
  }
  if (perm === "not_determined") {
    return { label: "Mic access needed", cta: "Grant Access" };
  }
  return null;
}

export default function OverlayApp() {
  const [state, setState] = useState<UiState>("idle");
  const [levels, setLevels] = useState<number[]>([]);
  const [notice, setNotice] = useState<string>("");
  const [perm, setPerm] = useState<MicPermission>("unknown");

  useEffect(() => {
    const unlistens: Array<() => void> = [];
    const reg = async () => {
      unlistens.push(
        await onState((s) => setState(s)),
        await onLevel((l) => setLevels(l)),
        await onMicPermission((p) => setPerm(p)),
        await onMicError((msg) => setNotice(msg)),
        await onSttStatus((s) => {
          const payload = s as { status?: string; progress?: number } | string;
          if (typeof payload === "string") {
            setNotice(payload === "ready" ? "" : payload === "stopped" ? "Voice engine stopped" : payload);
          } else if (payload.status === "downloading") {
            setNotice(`Preparing voice model... ${Math.round((payload.progress ?? 0) * 100)}%`);
          } else {
            setNotice("");
          }
        }),
        await onSttError((msg) => setNotice(msg)),
      );
      const current = await getState();
      setState(current.state);
      setPerm(current.mic_permission);
    };
    reg();
    return () => unlistens.forEach((u) => u());
  }, []);

  const pollPermission = useCallback(async () => {
    for (let i = 0; i < 150; i++) {
      const p = await micPermissionState();
      setPerm(p);
      if (p === "denied" || p === "restricted") return;
      if (p === "granted") {
        // Continue the turn the user actually asked for instead of making them
        // press the mic a second time after the system prompt.
        setNotice("");
        startListening();
        return;
      }
      await new Promise((r) => setTimeout(r, 200));
    }
  }, []);

  const prompt = permissionPrompt(perm);

  const onPermissionClick = async () => {
    if (perm === "denied" || perm === "restricted") {
      await openMicSettings();
      return;
    }
    await requestMicPermission();
    void pollPermission();
  };

  const active = state === "listening" || state === "finalizing";
  const color = active ? "rgba(247, 247, 250, 0.88)" : "rgba(247, 247, 250, 0.32)";
  const statusColor = prompt ? "#f0ad4e" : STATE_COLOR[state];
  const working = state === "working";
  const busy = working || state === "preparing";
  const statusLabel = prompt
    ? prompt.label
    : notice && state !== "listening"
      ? notice
      : STATE_TEXT[state];

  const onMicClick = () => {
    if (working) {
      // One turn at a time: while the agent works, the button cancels it.
      pressEscape();
    } else if (active || state === "preparing") {
      // "Stop listening" commits what was heard rather than discarding it;
      // Esc is the only path that throws the utterance away.
      stopListening();
    } else {
      startListening();
    }
  };

  const openInPanel = async (command: "settings" | "history") => {
    await togglePanel();
    await sendUiCommand(command);
  };

  const openSettings = () => void openInPanel("settings");
  const openHistory = () => void openInPanel("history");

  return (
    <div className="pill-root">
      <div className="pill">
        {/* Left: identity chip */}
        <button
          className="pill-chip"
          onClick={() => void openHistory()}
          title="Show conversations"
        >
          <span className="pill-avatar">
            <svg viewBox="0 0 24 24" width="17" height="17" fill="none">
              <circle cx="12" cy="8.5" r="3.6" fill="currentColor" />
              <path
                d="M5 19.5a7 7 0 0 1 14 0"
                stroke="currentColor"
                strokeWidth="1.7"
                strokeLinecap="round"
              />
            </svg>
          </span>
          <div className="pill-title">
            <div className="pill-name-row">
              <span className="pill-name">Sani</span>
              <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" strokeWidth="2.4">
                <path d="M6 9.5l6 6 6-6" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            </div>
            <div className="pill-status">
              <span
                className="dot"
                style={{
                  background: notice && state !== "listening" && !prompt ? "var(--warning)" : statusColor,
                }}
              />
              <span className={busy ? "status-shimmer" : ""}>{statusLabel}</span>
            </div>
          </div>
        </button>

        {/* Center: real waveform split around the microphone, or the grant action */}
        {prompt ? (
          <button className="pill-grant" onClick={onPermissionClick}>
            {prompt.cta}
          </button>
        ) : (
          <>
            <div className="pill-wave">
              <Waveform levels={levels} active={active} color={color} side="left" />
            </div>
            <button
              className={`pill-mic ${state}`}
              onClick={onMicClick}
              aria-label={working ? "Stop" : active || state === "preparing" ? "Finish" : "Start listening"}
            >
              {working ? (
                <span className="mic-spinner" />
              ) : active || state === "preparing" ? (
                <span className="mic-stop-glyph" />
              ) : (
                <svg viewBox="0 0 24 24" width="24" height="24" fill="none">
                  <rect x="9" y="3.5" width="6" height="11" rx="3" fill="currentColor" />
                  <path
                    d="M5.5 11.5a6.5 6.5 0 0 0 13 0"
                    stroke="currentColor"
                    strokeWidth="1.8"
                    strokeLinecap="round"
                  />
                  <path d="M12 18v2.5" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
                </svg>
              )}
              <span className="mic-ring" />
            </button>
            <div className="pill-wave">
              <Waveform levels={levels} active={active} color={color} side="right" />
            </div>
          </>
        )}

        {/* Right controls */}
        <div className="pill-right">
          <button className="pill-icon" onClick={() => void togglePanel()} title="Show or hide the transcript">
            <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor">
              <rect x="3.5" y="10" width="2" height="4" rx="1" />
              <rect x="7.5" y="7" width="2" height="10" rx="1" />
              <rect x="11.5" y="4" width="2" height="16" rx="1" />
              <rect x="15.5" y="7.5" width="2" height="9" rx="1" />
              <rect x="19" y="10.5" width="2" height="3" rx="1" />
            </svg>
          </button>
          <button className="pill-icon" onClick={openSettings} title="Settings">
            <svg viewBox="0 0 24 24" width="19" height="19" fill="currentColor">
              <circle cx="5.5" cy="12" r="1.8" />
              <circle cx="12" cy="12" r="1.8" />
              <circle cx="18.5" cy="12" r="1.8" />
            </svg>
          </button>
        </div>
      </div>
    </div>
  );
}
