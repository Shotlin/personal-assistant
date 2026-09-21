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
            <svg viewBox="0 0 24 24" width="17" height="17" fill="currentColor">
              <rect x="3" y="10.5" width="2" height="3" rx="1" />
              <rect x="7" y="7.5" width="2" height="9" rx="1" />
              <rect x="11" y="4.5" width="2" height="15" rx="1" />
              <rect x="15" y="8" width="2" height="8" rx="1" />
              <rect x="19" y="10.5" width="2" height="3" rx="1" />
            </svg>
          ) : (
            <svg viewBox="0 0 24 24" width="17" height="17" fill="none">
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
