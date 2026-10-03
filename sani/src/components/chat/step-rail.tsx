import { useState } from "react";
import {
  AlertCircle,
  ChevronRight,
  Code2,
  FileText,
  FolderPlus,
  Globe,
  ListChecks,
  MessageCircleQuestion,
  Scale,
  MousePointerClick,
  Search,
  SquareTerminal,
  Wrench,
} from "lucide-react";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { DotMatrixLoader } from "@/components/ui/dot-matrix-loader";
import { cn } from "@/lib/utils";
import { formatDuration, pluralize } from "@/lib/format";
import { itemSteps, type ChatItem, type StepPart } from "@/chat/types";
import { RoundBlock, buildEntries, countWorkSteps } from "./round";

/** Leading slot: a glyph chosen from the tool/label, never an "AI" sparkle. */
function StepGlyph({ step }: { step: StepPart }) {
  if (step.status === "running") return <DotMatrixLoader label="Working" />;
  if (step.status === "failed") return <AlertCircle className="size-4 text-destructive" aria-label="Failed" />;
  const key = `${step.tool ?? ""} ${step.label}`.toLowerCase();
  const className = "size-4";
  // Steps Sani takes itself, as opposed to Claude Code's tool calls.
  if (step.tool === "folder") return <FolderPlus className={className} />;
  if (step.tool === "plan") return <ListChecks className={className} />;
  if (step.tool === "decision") return <Scale className={className} />;
  if (step.tool === "question") return <MessageCircleQuestion className={className} />;
  if (/claude code/.test(key)) return <SquareTerminal className={className} />;
  if (/(chrome|safari|browser|web|navigate|url|tab)/.test(key)) return <Globe className={className} />;
  if (/(terminal|shell|bash|command|ran )/.test(key)) return <SquareTerminal className={className} />;
  if (/(click|type|press|scroll|drag)/.test(key)) return <MousePointerClick className={className} />;
  if (/(read|file|open|write|edit)/.test(key)) return <FileText className={className} />;
  if (/(search|find|look)/.test(key)) return <Search className={className} />;
  if (step.tool) return <Wrench className={className} />;
  return <span className="size-1.5 rounded-full bg-current opacity-60" aria-hidden="true" />;
}

function StepRow({ step }: { step: StepPart }) {
  const [open, setOpen] = useState(false);
  const running = step.status === "running";
  const failed = step.status === "failed";
  return (
    <div>
      <div className="group/step flex min-h-7 items-center gap-2 text-sm">
        <span
          className={cn(
            "flex size-4 shrink-0 items-center justify-center",
            failed ? "text-destructive" : "text-muted-foreground",
          )}
        >
          <StepGlyph step={step} />
        </span>
        <span
          className={cn(
            "min-w-0 flex-1 truncate",
            running ? "sani-shimmer" : failed ? "text-foreground" : "text-muted-foreground",
          )}
        >
          {step.label}
        </span>
        {step.durationMs ? (
          <span className="shrink-0 text-xs text-muted-foreground/80 tabular-nums">
            {formatDuration(step.durationMs)}
          </span>
        ) : null}
        {/* Fixed trailing slot so hover never shifts the row (DESIGN P3). */}
        <span className="flex size-5 shrink-0 items-center justify-center">
          {step.detail ? (
            <button
              type="button"
              onClick={() => setOpen((value) => !value)}
              aria-label="Technical details"
              aria-expanded={open}
              className={cn(
                "inline-flex size-5 items-center justify-center rounded text-muted-foreground transition-opacity hover:text-foreground focus-visible:opacity-100 focus-visible:ring-2 focus-visible:ring-ring/40 focus-visible:outline-none",
                failed || open ? "opacity-100" : "opacity-0 group-hover/step:opacity-100",
              )}
            >
              <Code2 className="size-3.5" />
            </button>
          ) : null}
        </span>
      </div>
      {open && step.detail ? (
        <pre className="mt-0.5 mb-1 ml-6 max-h-48 overflow-auto rounded-md border border-border bg-slate-2 px-2.5 py-2 font-mono text-xs leading-relaxed whitespace-pre-wrap text-muted-foreground select-text">
          {step.detail}
        </pre>
      ) : null}
    </div>
  );
}

function workedFor(item: ChatItem, steps: StepPart[]): number | null {
  if (item.startedAt && item.endedAt && item.endedAt > item.startedAt) {
    return item.endedAt - item.startedAt;
  }
  const times = steps.map((step) => step.timestamp).filter(Boolean);
  if (times.length > 1) return Math.max(...times) - Math.min(...times);
  return null;
}

/**
 * A turn's steps read as one rail. While the turn is live every step is
 * visible; once finished the rail collapses to one line (DESIGN T1).
 */
export function StepRail({ item }: { item: ChatItem }) {
  const steps = itemSteps(item);
  if (steps.length === 0) return null;
  const live = item.status === "streaming";

  const entries = buildEntries(steps);
  const hasRounds = entries.some((entry) => entry.type === "round");
  const plainSteps = steps.filter((step) => !step.group);

  const renderEntries = (liveRail: boolean) => {
    // The host reports plain progress as "info" lines. While the turn is live
    // the newest plain one is the step in flight, so it carries the running mark.
    const lastPlain = liveRail ? plainSteps[plainSteps.length - 1] : undefined;
    return entries.map((entry) =>
      entry.type === "round" ? (
        <RoundBlock key={entry.round.group} round={entry.round} renderStep={(step) => <StepRow key={step.id} step={step} />} />
      ) : (
        <StepRow
          key={entry.step.id}
          step={
            liveRail && entry.step === lastPlain && entry.step.status === "info"
              ? { ...entry.step, status: "running" }
              : entry.step
          }
        />
      ),
    );
  };

  if (live) {
    return (
      <div className="flex flex-col" aria-live="polite">
        {renderEntries(true)}
      </div>
    );
  }

  // With Claude Code rounds, the honest total is the time the rounds took.
  const roundTime = entries.reduce(
    (sum, entry) => sum + (entry.type === "round" ? (entry.round.header?.durationMs ?? 0) : 0),
    0,
  );
  const duration = hasRounds && roundTime > 0 ? roundTime : workedFor(item, steps);
  const failedCount = steps.filter((step) => step.status === "failed" && !step.kind).length;
  const roundCount = entries.filter((entry) => entry.type === "round").length;
  const summary = [
    hasRounds ? "Worked with Claude Code" : duration ? `Worked for ${formatDuration(duration)}` : "Worked",
    hasRounds ? pluralize(roundCount, "round") : null,
    pluralize(countWorkSteps(steps), "step"),
    hasRounds && duration ? formatDuration(duration) : null,
    failedCount ? `${failedCount} failed` : null,
  ]
    .filter(Boolean)
    .join(" · ");

  return (
    <Collapsible defaultOpen={failedCount > 0 || hasRounds}>
      <CollapsibleTrigger className="group/trigger flex cursor-pointer items-center gap-1 rounded text-sm text-muted-foreground transition-colors hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/40 focus-visible:outline-none">
        <span>{summary}</span>
        <ChevronRight
          aria-hidden="true"
          className="size-3.5 text-muted-foreground/70 transition-transform duration-150 group-data-panel-open/trigger:rotate-90"
        />
      </CollapsibleTrigger>
      <CollapsibleContent className="h-(--collapsible-panel-height) overflow-hidden transition-[height] duration-150 ease-out data-ending-style:h-0 data-starting-style:h-0">
        <div className="flex flex-col pt-1 pl-0.5">{renderEntries(false)}</div>
      </CollapsibleContent>
    </Collapsible>
  );
}
