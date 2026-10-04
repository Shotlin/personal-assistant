import { useEffect, useMemo, useState } from "react";
import { MessageSquarePlus, Monitor, MoreHorizontal, Settings, SquareTerminal, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { cn } from "@/lib/utils";
import { computerControlSnapshot, onComputerControlChange, type Conversation } from "@/lib/tauri";
import { useConversations } from "@/chat/use-conversations";
import { useClaudeCode } from "@/chat/use-claude-code";
import { claudeHealth, zcodeHealth } from "@/chat/claude-health";
import { useZCode } from "@/chat/use-zcode";
import { StatusDot } from "@/components/status-dot";

export type Section = "chat" | "control" | "settings";

function startOfDay(ms: number): number {
  const date = new Date(ms);
  date.setHours(0, 0, 0, 0);
  return date.getTime();
}

function groupConversations(list: Conversation[]): Array<[string, Conversation[]]> {
  const today = startOfDay(Date.now());
  const yesterday = today - 86_400_000;
  const week = today - 6 * 86_400_000;
  const buckets: Record<string, Conversation[]> = { Today: [], Yesterday: [], "Last 7 days": [], Earlier: [] };
  for (const conversation of list) {
    const day = startOfDay(conversation.updated_at);
    const key = day >= today ? "Today" : day >= yesterday ? "Yesterday" : day >= week ? "Last 7 days" : "Earlier";
    buckets[key].push(conversation);
  }
  return Object.entries(buckets).filter(([, items]) => items.length > 0);
}

/** Computer control readiness as one quiet fact, not a page of explanation. */
function useControlReady(): boolean | null {
  const [ready, setReady] = useState<boolean | null>(null);
  useEffect(() => {
    let live = true;
    const read = () =>
      void computerControlSnapshot()
        .then((snapshot) => live && setReady(snapshot.status === "ready"))
        .catch(() => live && setReady(null));
    read();
    const off = onComputerControlChange(read);
    return () => {
      live = false;
      void off.then((fn) => fn()).catch(() => undefined);
    };
  }, []);
  return ready;
}

function NavRow({
  icon,
  label,
  selected,
  onClick,
  trailing,
}: {
  icon: React.ReactNode;
  label: string;
  selected: boolean;
  onClick: () => void;
  trailing?: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-current={selected ? "page" : undefined}
      className={cn(
        "flex h-8 w-full items-center gap-2.5 rounded-lg px-2.5 text-left text-sm transition-colors outline-none focus-visible:ring-2 focus-visible:ring-ring/40",
        selected
          ? "bg-sidebar-accent text-sidebar-accent-foreground"
          : "text-muted-foreground hover:bg-sidebar-accent/70 hover:text-foreground",
      )}
    >
      <span className="flex size-4 shrink-0 items-center justify-center">{icon}</span>
      <span className="min-w-0 flex-1 truncate">{label}</span>
      {trailing}
    </button>
  );
}

export function Sidebar({
  section,
  onSection,
  onOpenClaudeCode,
  onOpenZCode,
}: {
  section: Section;
  onSection: (section: Section) => void;
  onOpenClaudeCode: () => void;
  onOpenZCode: () => void;
}) {
  const claude = useClaudeCode();
  const health = claudeHealth(claude.status, claude.loading);
  const zcode = useZCode();
  const zcodeState = zcodeHealth(zcode.status, zcode.loading);
  const { conversations, activeId, open, create, remove } = useConversations();
  const groups = useMemo(() => groupConversations(conversations), [conversations]);
  const controlReady = useControlReady();
  const [pendingDelete, setPendingDelete] = useState<Conversation | null>(null);

  return (
    <nav
      aria-label="Sani navigation"
      className="flex h-full w-[248px] shrink-0 flex-col border-r border-sidebar-border bg-sidebar"
    >
      <div className="flex items-center justify-between px-4 pt-4 pb-2">
        <span className="text-[15px] font-semibold tracking-tight text-foreground">Sani</span>
      </div>

      <div className="px-2.5 pb-2">
        <Button
          variant="outline"
          className="h-8 w-full justify-start gap-2.5 rounded-lg bg-background px-2.5 text-sm font-normal"
          onClick={() => {
            onSection("chat");
            void create().catch((error) => console.error("[main] new conversation failed", error));
          }}
        >
          <MessageSquarePlus className="size-4 text-muted-foreground" aria-hidden="true" />
          New chat
        </Button>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-2.5 pb-2">
        {groups.length === 0 ? (
          <p className="px-2.5 py-3 text-sm text-muted-foreground">Your chats appear here.</p>
        ) : (
          groups.map(([label, items]) => (
            <div key={label} className="mb-2">
              <div className="px-2.5 pt-2 pb-1 text-xs font-medium text-muted-foreground">{label}</div>
              {items.map((conversation) => {
                const selected = section === "chat" && conversation.id === activeId;
                return (
                  <div key={conversation.id} className="group/row relative">
                    <NavRow
                      icon={<span className="size-1.5 rounded-full bg-current opacity-50" aria-hidden="true" />}
                      label={conversation.title || "New chat"}
                      selected={selected}
                      onClick={() => {
                        onSection("chat");
                        void open(conversation.id).catch((error) =>
                          console.error("[main] open conversation failed", error),
                        );
                      }}
                    />
                    <DropdownMenu>
                      <DropdownMenuTrigger
                        render={
                          <button
                            type="button"
                            aria-label={`Options for ${conversation.title || "chat"}`}
                            className="absolute top-1 right-1 flex size-6 items-center justify-center rounded-md text-muted-foreground opacity-0 transition-opacity outline-none group-focus-within/row:opacity-100 group-hover/row:opacity-100 hover:bg-slate-4 hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/40 data-popup-open:opacity-100"
                          />
                        }
                      >
                        <MoreHorizontal className="size-3.5" />
                      </DropdownMenuTrigger>
                      <DropdownMenuContent align="start" className="min-w-40">
                        <DropdownMenuItem onClick={() => setPendingDelete(conversation)}>
                          <Trash2 className="size-4" aria-hidden="true" />
                          Delete chat
                        </DropdownMenuItem>
                      </DropdownMenuContent>
                    </DropdownMenu>
                  </div>
                );
              })}
            </div>
          ))
        )}
      </div>

      <div className="flex flex-col gap-0.5 border-t border-sidebar-border px-2.5 py-2.5">
        <NavRow
          icon={<Monitor className="size-4" />}
          label="Computer control"
          selected={section === "control"}
          onClick={() => onSection("control")}
          trailing={
            controlReady === null ? null : (
              <span
                className={cn("text-xs", controlReady ? "text-muted-foreground" : "text-foreground")}
              >
                {controlReady ? "Ready" : "Needs setup"}
              </span>
            )
          }
        />
        <NavRow
          icon={<SquareTerminal className="size-4" />}
          label="Claude Code"
          selected={false}
          onClick={onOpenClaudeCode}
          trailing={<StatusDot tone={health.tone} label={`Claude Code: ${health.title}`} />}
        />
        <NavRow
          icon={<SquareTerminal className="size-4" />}
          label="ZCode"
          selected={false}
          onClick={onOpenZCode}
          trailing={<StatusDot tone={zcodeState.tone} label={`ZCode: ${zcodeState.title}`} />}
        />
        <NavRow
          icon={<Settings className="size-4" />}
          label="Settings"
          selected={section === "settings"}
          onClick={() => onSection("settings")}
        />
      </div>

      <AlertDialog open={pendingDelete !== null} onOpenChange={(openState) => !openState && setPendingDelete(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete this chat?</AlertDialogTitle>
            <AlertDialogDescription>
              The conversation and its local run details are removed from this Mac.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Keep chat</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => {
                if (pendingDelete) void remove(pendingDelete.id);
                setPendingDelete(null);
              }}
            >
              Delete chat
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </nav>
  );
}
