import {
  deleteConversation,
  newConversation,
  selectConversation,
} from "../lib/tauri";

interface ConversationItem {
  id: string;
  title: string;
  updated_at: number;
}

interface HistoryDrawerProps {
  conversations: ConversationItem[];
  onClose: () => void;
  onRefresh: () => Promise<void>;
  onSelect: (conversationId: string) => void;
}

function dayLabel(ts: number): string {
  const d = new Date(ts);
  const today = new Date();
  const startOfToday = new Date(today.getFullYear(), today.getMonth(), today.getDate()).getTime();
  if (ts >= startOfToday) return "Today";
  if (ts >= startOfToday - 86400000) return "Yesterday";
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

export default function HistoryDrawer({ conversations, onClose, onRefresh, onSelect }: HistoryDrawerProps) {
  const groups = new Map<string, ConversationItem[]>();
  for (const c of conversations) {
    const label = dayLabel(c.updated_at);
    if (!groups.has(label)) groups.set(label, []);
    groups.get(label)!.push(c);
  }

  return (
    <div className="drawer">
      <div className="drawer-header">
        <span>History</span>
        <button
          className="mini-btn"
          onClick={async () => {
            await newConversation();
            await onRefresh();
          }}
        >
          New
        </button>
        <button className="icon-btn" onClick={onClose} title="Close">
          <svg viewBox="0 0 24 24" width="15" height="15" stroke="currentColor" strokeWidth="2" fill="none" strokeLinecap="round">
            <path d="M6 6l12 12M18 6L6 18" />
          </svg>
        </button>
      </div>
      <div className="drawer-body">
        {conversations.length === 0 && <div className="drawer-empty">No conversations yet.</div>}
        {[...groups.entries()].map(([label, items]) => (
          <div key={label}>
            <div className="drawer-group">{label}</div>
            {items.map((c) => (
              <button
                key={c.id}
                className="drawer-item"
                onClick={async () => {
                  await selectConversation(c.id);
                  onSelect(c.id);
                  onClose();
                }}
              >
                <span className="drawer-item-title">{c.title}</span>
                <span
                  className="drawer-item-delete"
                  onClick={async (e) => {
                    e.stopPropagation();
                    await deleteConversation(c.id);
                    await onRefresh();
                  }}
                >
                  ×
                </span>
              </button>
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}
