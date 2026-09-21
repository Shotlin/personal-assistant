interface MessageProps {
  role: "user" | "assistant";
  text: string;
  createdAt?: number;
  muted?: boolean;
}

function time(ts?: number): string {
  const d = ts ? new Date(ts) : new Date();
  return d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
}

export default function Message({ role, text, createdAt, muted }: MessageProps) {
  const isUser = role === "user";
  return (
    <div className={`msg ${isUser ? "msg-user" : "msg-agent"}`}>
      <div className="msg-header">
        <span className="msg-avatar">
          {isUser ? (
            <svg viewBox="0 0 24 24" width="16" height="16" fill="none">
              <rect x="8" y="4" width="8" height="12" rx="4" fill="currentColor" />
              <path d="M5 11.5a7 7 0 0 0 14 0" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
            </svg>
          ) : (
            <svg viewBox="0 0 24 24" width="16" height="16" fill="none">
              <circle cx="12" cy="12" r="8.5" stroke="currentColor" strokeWidth="1.6" />
              <circle cx="12" cy="12" r="3" fill="currentColor" />
            </svg>
          )}
        </span>
        <span className="msg-author">{isUser ? "You" : "Sani"}</span>
        <span className="msg-time">{time(createdAt)}</span>
      </div>
      <div className={`msg-text ${muted ? "muted" : ""}`}>{text}</div>
    </div>
  );
}
