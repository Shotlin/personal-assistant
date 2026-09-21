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
  requestMicPermission,
  startListening,
  stopListening,
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
  idle: "#34c77b",
  preparing: "#e6a23c",
  listening: "#34c77b",
  finalizing: "#e6a23c",
  working: "#4f8ef7",
  error: "#f0564f",
};

/**
 * Ungranted macOS TCC access is surfaced as an explicit action, never as a
 * silent "Listening" state (RC-04).
 */
function permissionPrompt(perm: MicPermission): { label: string; cta: string } | null {
  if (perm === "denied" || perm === "restricted") {
    return { label: "Microphone access denied", cta: "Open System Settings" };
  }
  if (perm === "not_determined") {
    return { label: "Microphone permission required", cta: "Grant Access" };
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
  const color = active ? "rgba(244, 244, 246, 0.85)" : "rgba(244, 244, 246, 0.35)";
  const statusColor = prompt ? "#e6a23c" : STATE_COLOR[state];
  const working = state === "working";
  const statusLabel = prompt
    ? prompt.label
    : notice && state !== "listening"
      ? notice
      : STATE_TEXT[state];

  const onMicClick = () => {
    if (state === "listening" || state === "finalizing") {
      stopListening();
    } else {
      startListening();
    }
  };

  return (
    <div className="pill-root">
      <div className="pill">
        {/* Left: identity + state */}
        <div className="pill-left">
          <div className="pill-avatar">
            <svg viewBox="0 0 24 24" width="20" height="20" fill="none">
              <rect x="9" y="3.5" width="6" height="11" rx="3" fill="currentColor" />
              <path
                d="M5.5 11.5a6.5 6.5 0 0 0 13 0"
                stroke="currentColor"
                strokeWidth="1.8"
                strokeLinecap="round"
              />
              <path d="M12 18v2.5" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
            </svg>
          </div>
          <div className="pill-title">
            <div className="pill-name">Sani</div>
            <div className="pill-status">
              <span
                className="dot"
                style={{
                  background: notice && state !== "listening" && !prompt ? "var(--warning)" : statusColor,
                }}
              />
              <span className={working || state === "preparing" ? "status-shimmer" : ""}>
                {statusLabel}
              </span>
            </div>
          </div>
        </div>

        {/* Center: real waveform, or the permission action */}
        {prompt ? (
          <button className="pill-permission" onClick={onPermissionClick}>
            {prompt.cta}
          </button>
        ) : (
          <div className="pill-wave">
            <Waveform levels={levels} active={active} color={color} />
          </div>
        )}

        {/* Mic control */}
        <button
          className={`pill-mic ${state}`}
          onClick={onMicClick}
          disabled={prompt !== null}
          aria-label={active ? "Stop listening" : "Start listening"}
        >
          {working ? (
            <span className="mic-spinner" />
          ) : (
            <svg viewBox="0 0 24 24" width="22" height="22" fill="none">
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

        {/* Stop + more */}
        <div className="pill-right">
          {(state === "preparing" ||
            state === "listening" ||
            state === "finalizing" ||
            state === "working") && (
            <button className="pill-stop" onClick={stopListening} aria-label="Stop">
              <svg viewBox="0 0 24 24" width="14" height="14">
                <rect x="6" y="6" width="12" height="12" rx="2.5" fill="currentColor" />
              </svg>
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
