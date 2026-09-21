import { useEffect, useState } from "react";
import Waveform from "../components/Waveform";
import {
  onLevel,
  onState,
  onSttError,
  onSttStatus,
  startListening,
  stopListening,
  type UiState,
} from "../lib/tauri";
import "../styles/tokens.css";
import "../styles/pill.css";

const STATE_TEXT: Record<UiState, string> = {
  idle: "Ready",
  listening: "Listening...",
  finalizing: "Finalizing...",
  working: "Working...",
  error: "Error",
};

const STATE_COLOR: Record<UiState, string> = {
  idle: "#34c77b",
  listening: "#34c77b",
  finalizing: "#e6a23c",
  working: "#4f8ef7",
  error: "#f0564f",
};

export default function OverlayApp() {
  const [state, setState] = useState<UiState>("idle");
  const [levels, setLevels] = useState<number[]>([]);
  const [notice, setNotice] = useState<string>("");

  useEffect(() => {
    const unlistens: Array<() => void> = [];
    const reg = async () => {
      unlistens.push(
        await onState((s) => setState(s)),
        await onLevel((l) => setLevels(l)),
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
    };
    reg();
    return () => unlistens.forEach((u) => u());
  }, []);

  const active = state === "listening" || state === "finalizing";
  const color = active ? "rgba(244, 244, 246, 0.85)" : "rgba(244, 244, 246, 0.35)";
  const statusColor = STATE_COLOR[state];
  const working = state === "working";

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
                  background:
                    notice && state !== "listening" ? "var(--warning)" : statusColor,
                }}
              />
              <span className={working ? "status-shimmer" : ""}>
                {notice && state !== "listening" ? notice : STATE_TEXT[state]}
              </span>
            </div>
          </div>
        </div>

        {/* Center: real waveform */}
        <div className="pill-wave">
          <Waveform levels={levels} active={active} color={color} />
        </div>

        {/* Mic control */}
        <button
          className={`pill-mic ${state}`}
          onClick={onMicClick}
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
          {(state === "listening" || state === "finalizing" || state === "working") && (
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
