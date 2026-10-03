import { useEffect, useState } from "react";
import { Check, ChevronRight, CircleAlert, Lock, MoreHorizontal, Pause, Play, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
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
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { DotMatrixLoader } from "@/components/ui/dot-matrix-loader";
import type { MissionPendingApproval } from "@/lib/tauri";
import { cn } from "@/lib/utils";

/**
 * Truthful mission status (Jarvis Phase 1, T08).
 *
 * Renders the DURABLE mission status from sani-core, never a fabricated
 * completion: a delivered reply is not a verified outcome. Unknown statuses
 * render as "Unknown" explicitly; COMPLETED without passed checks is marked
 * unverified. Blocked and waiting states are neutral, not red (DESIGN C5).
 */

export type MissionStatusValue =
  | "PLANNED"
  | "RUNNING"
  | "WAITING_EXTERNAL"
  | "BLOCKED"
  | "NEEDS_APPROVAL"
  | "PAUSED"
  | "VERIFYING"
  | "COMPLETED"
  | "FAILED"
  | "CANCELLED"
  | "UNKNOWN";

const STATUS_LABEL: Record<string, string> = {
  PLANNED: "Planned",
  RUNNING: "Working",
  WAITING_EXTERNAL: "Waiting for an external result",
  BLOCKED: "Blocked",
  NEEDS_APPROVAL: "Needs your approval",
  PAUSED: "Paused",
  VERIFYING: "Verifying",
  COMPLETED: "Done · verified",
  FAILED: "Failed",
  CANCELLED: "Cancelled",
  UNKNOWN: "Unknown",
};

type Control = "PAUSE" | "RESUME" | "CANCEL";

const CONTROL_WHEN: Record<string, Control[]> = {
  PLANNED: ["PAUSE", "CANCEL"],
  RUNNING: ["PAUSE", "CANCEL"],
  WAITING_EXTERNAL: ["PAUSE", "CANCEL"],
  PAUSED: ["RESUME", "CANCEL"],
  NEEDS_APPROVAL: ["RESUME", "CANCEL"],
  BLOCKED: ["CANCEL"],
};

const BUSY = new Set(["RUNNING", "WAITING_EXTERNAL", "VERIFYING"]);
const LOCKED = new Set(["BLOCKED", "NEEDS_APPROVAL", "PAUSED"]);
const FINAL = new Set(["COMPLETED", "FAILED", "CANCELLED"]);
const REVISABLE = new Set(["PLANNED", "RUNNING", "PAUSED", "NEEDS_APPROVAL", "WAITING_EXTERNAL"]);

const CONTROL_LABEL: Record<Control, string> = { PAUSE: "Pause", RESUME: "Resume", CANCEL: "Cancel" };

function StatusGlyph({ status, verified }: { status: string; verified: boolean }) {
  if (BUSY.has(status)) return <DotMatrixLoader label="Working" />;
  if (LOCKED.has(status)) return <Lock className="size-3.5" aria-hidden="true" />;
  if (status === "FAILED") return <CircleAlert className="size-3.5 text-destructive" aria-hidden="true" />;
  if (status === "COMPLETED" && verified) return <Check className="size-3.5" aria-hidden="true" />;
  return <span className="size-1.5 rounded-full bg-current opacity-60" aria-hidden="true" />;
}

export interface MissionBarProps {
  status: string | null;
  verified?: boolean;
  missionId?: string | null;
  onControl?: (kind: Control) => void;
  onRevise?: (revision: string) => void;
  onSetPriority?: (priority: number) => void;
  onPurge?: () => void;
}

export function MissionBar({
  status,
  verified = false,
  missionId = null,
  onControl,
  onRevise,
  onSetPriority,
  onPurge,
}: MissionBarProps) {
  const [reviseOpen, setReviseOpen] = useState(false);
  const [purgeOpen, setPurgeOpen] = useState(false);
  const [revision, setRevision] = useState("");
  if (!status) return null;

  const normalized = STATUS_LABEL[status] ? status : "UNKNOWN";
  const controls = onControl ? (CONTROL_WHEN[normalized] ?? []) : [];
  const revisable = onRevise !== undefined && REVISABLE.has(normalized);
  const priorityAdjustable = onSetPriority !== undefined && !FINAL.has(normalized);
  const purgable = onPurge !== undefined && FINAL.has(normalized);
  const hasMenu = revisable || priorityAdjustable || purgable;

  return (
    <div
      data-mission-id={missionId ?? undefined}
      className="flex min-h-9 items-center gap-2 rounded-xl border border-border bg-slate-2 px-3 text-sm"
    >
      <span
        className={cn(
          "flex min-w-0 items-center gap-2",
          normalized === "FAILED" ? "text-foreground" : "text-muted-foreground",
        )}
      >
        <StatusGlyph status={normalized} verified={verified} />
        <span className="truncate">
          {STATUS_LABEL[normalized]}
          {normalized === "COMPLETED" && !verified ? " (unverified)" : ""}
        </span>
      </span>
      <span className="flex-1" />
      {controls.map((kind) => (
        <Button
          key={kind}
          type="button"
          variant="ghost"
          size="xs"
          onClick={() => onControl?.(kind)}
          className="rounded-full text-muted-foreground hover:bg-secondary hover:text-foreground"
        >
          {kind === "PAUSE" ? <Pause className="size-3" /> : kind === "RESUME" ? <Play className="size-3" /> : <X className="size-3" />}
          {CONTROL_LABEL[kind]}
        </Button>
      ))}
      {hasMenu ? (
        <DropdownMenu>
          <DropdownMenuTrigger
            render={
              <Button
                type="button"
                variant="ghost"
                size="icon-xs"
                aria-label="More task options"
                className="rounded-full text-muted-foreground hover:bg-secondary hover:text-foreground"
              />
            }
          >
            <MoreHorizontal className="size-3.5" />
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="min-w-44">
            {revisable ? (
              <DropdownMenuItem onClick={() => setReviseOpen(true)}>Revise task</DropdownMenuItem>
            ) : null}
            {priorityAdjustable ? (
              <>
                <DropdownMenuSeparator />
                <DropdownMenuLabel>Priority</DropdownMenuLabel>
                {[0, 3, 6, 9].map((n) => (
                  <DropdownMenuItem key={n} onClick={() => onSetPriority?.(n)}>
                    {n === 0 ? "0 · Lowest" : n === 9 ? "9 · Highest" : String(n)}
                  </DropdownMenuItem>
                ))}
              </>
            ) : null}
            {purgable ? (
              <DropdownMenuItem onClick={() => setPurgeOpen(true)}>Delete stored records…</DropdownMenuItem>
            ) : null}
          </DropdownMenuContent>
        </DropdownMenu>
      ) : null}

      <Dialog open={reviseOpen} onOpenChange={setReviseOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Revise this task</DialogTitle>
          </DialogHeader>
          <textarea
            value={revision}
            onChange={(event) => setRevision(event.target.value)}
            aria-label="What changed"
            placeholder="What should change?"
            rows={4}
            className="w-full resize-none rounded-lg border border-border bg-background px-3 py-2 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/30"
          />
          <DialogFooter>
            <Button variant="ghost" onClick={() => setReviseOpen(false)}>
              Cancel
            </Button>
            <Button
              disabled={!revision.trim()}
              onClick={() => {
                onRevise?.(revision.trim());
                setRevision("");
                setReviseOpen(false);
              }}
            >
              Send revision
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <AlertDialog open={purgeOpen} onOpenChange={setPurgeOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete this task's stored records?</AlertDialogTitle>
            <AlertDialogDescription>
              Evidence files and derived recommendations are removed. The audit tombstone stays.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Keep records</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => {
                onPurge?.();
                setPurgeOpen(false);
              }}
            >
              Delete records
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

const EFFECT_LINE: Record<MissionPendingApproval["effect_class"], string> = {
  READ_ONLY: "Reads only. Nothing changes.",
  REPEATABLE_LOCAL: "Changes something on this Mac. Repeatable.",
  EXTERNAL_WRITE: "Sends or posts outside this Mac.",
  DESTRUCTIVE: "Deletes or overwrites. Can't be undone.",
};

/**
 * The consent card (DESIGN T4/P9): the focal element while a decision is
 * pending. It names the action, the target and the risk in one state line;
 * the exact digest and versions live under Technical details.
 */
export function ApprovalCard({
  pending,
  onApprove,
  onReject,
  shortcuts = false,
}: {
  pending: MissionPendingApproval;
  onApprove?: (pending: MissionPendingApproval) => void;
  onReject?: (pending: MissionPendingApproval) => void;
  /** Bind ⌘⏎ / esc. Only the first pending card in view should own them. */
  shortcuts?: boolean;
}) {
  useEffect(() => {
    if (!shortcuts) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Enter" && (event.metaKey || event.ctrlKey) && onApprove) {
        event.preventDefault();
        onApprove(pending);
      } else if (event.key === "Escape" && onReject) {
        event.preventDefault();
        onReject(pending);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [shortcuts, pending, onApprove, onReject]);

  return (
    <div
      role="group"
      aria-label="Approval needed"
      className="rounded-(--sani-radius) border border-slate-7 bg-card p-3.5 shadow-(--sani-card-shadow)"
    >
      <div className="flex items-start gap-2.5">
        <Lock className="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
        <div className="min-w-0 flex-1">
          <p className="font-medium text-foreground">
            Allow {pending.tool}
            {pending.target_ref ? ` on ${pending.target_ref}` : ""}?
          </p>
          <p className="text-sm text-muted-foreground">
            {EFFECT_LINE[pending.effect_class] ?? "Review before continuing."}
            {pending.account_ref ? ` Account: ${pending.account_ref}.` : ""}
            {pending.workspace_ref ? ` Workspace: ${pending.workspace_ref}.` : ""}
          </p>
          <Collapsible className="mt-1.5">
            <CollapsibleTrigger className="group/trigger flex items-center gap-1 rounded text-xs text-muted-foreground transition-colors hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/40 focus-visible:outline-none">
              Technical details
              <ChevronRight className="size-3 transition-transform duration-150 group-data-panel-open/trigger:rotate-90" />
            </CollapsibleTrigger>
            <CollapsibleContent>
              <dl className="mt-1.5 grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 font-mono text-xs text-muted-foreground select-text">
                <dt>digest</dt>
                <dd className="truncate">{pending.action_digest}</dd>
                <dt>plan</dt>
                <dd>
                  v{pending.plan_version} · epoch {pending.control_epoch}
                </dd>
                <dt>effect</dt>
                <dd>{pending.effect_class}</dd>
              </dl>
            </CollapsibleContent>
          </Collapsible>
        </div>
      </div>
      <div className="mt-3 flex justify-end gap-2">
        {onReject ? (
          <Button variant="ghost" size="sm" onClick={() => onReject(pending)}>
            Decline
            {shortcuts ? <kbd className="ml-1 font-sans text-[11px] text-muted-foreground">esc</kbd> : null}
          </Button>
        ) : null}
        {onApprove ? (
          <Button size="sm" onClick={() => onApprove(pending)}>
            Allow
            {shortcuts ? <kbd className="ml-1 font-sans text-[11px] opacity-70">⌘⏎</kbd> : null}
          </Button>
        ) : null}
      </div>
    </div>
  );
}
