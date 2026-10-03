import { useState } from "react";
import { AlertCircle, Check, Lock, SquareTerminal } from "lucide-react";
import { DotMatrixLoader } from "@/components/ui/dot-matrix-loader";
import { cn } from "@/lib/utils";
import { formatDuration } from "@/lib/format";
import type { StepPart } from "@/chat/types";
import { Markdown } from "./markdown";

/**
 * One call from Sani to Claude Code, drawn as a short conversation:
 *
 *   why Sani called it  (the round title)
 *   ├ Sani asked Claude Code      what Sani wrote
 *   ├ steps                       what Claude Code did
 *   └ Claude Code replied         what came back
 *
 * Flat, with one rule down the left (DESIGN S1): no cards inside cards.
 */
export interface Round {
  group: string;
  header?: StepPart;
  prompt?: StepPart;
  reply?: StepPart;
  notes: StepPart[];
  steps: StepPart[];
}

export type RailEntry = { type: "step"; step: StepPart } | { type: "round"; round: Round };

const STRUCTURAL = new Set(["round", "prompt", "reply", "note"]);

/** Fold a flat step list into plain steps and Claude Code rounds, in order. */
export function buildEntries(steps: StepPart[]): RailEntry[] {
  const entries: RailEntry[] = [];
  const rounds = new Map<string, Round>();
  for (const step of steps) {
    if (!step.group) {
      entries.push({ type: "step", step });
      continue;
    }
    let round = rounds.get(step.group);
    if (!round) {
      round = { group: step.group, notes: [], steps: [] };
      rounds.set(step.group, round);
      entries.push({ type: "round", round });
    }
    if (step.kind === "round") round.header = step;
    else if (step.kind === "prompt") round.prompt = step;
    else if (step.kind === "reply") round.reply = step;
    else if (step.kind === "note") round.notes.push(step);
    else round.steps.push(step);
  }
  return entries;
}

/** Real work steps only: headers, requests and replies are not "steps". */
export function countWorkSteps(steps: StepPart[]): number {
  return steps.filter((step) => !step.kind || !STRUCTURAL.has(step.kind)).length;
}

function Quote({
  label,
  text,
  markdown = false,
}: {
  label: string;
  text: string;
  markdown?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const long = text.length > 260 || text.split("\n").length > 4;
  return (
    <div className="min-w-0">
      <div className="text-xs font-medium text-muted-foreground">{label}</div>
      <blockquote
        className={cn(
          "mt-1 border-l-2 border-slate-6 pl-2.5 text-sm text-slate-11 select-text",
          !open && long && "line-clamp-4",
        )}
      >
        {markdown ? <Markdown text={text} className="text-sm" /> : <p className="whitespace-pre-wrap">{text}</p>}
      </blockquote>
      {long ? (
        <button
          type="button"
          onClick={() => setOpen((value) => !value)}
          className="mt-0.5 ml-3 rounded text-xs text-muted-foreground transition-colors hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/40 focus-visible:outline-none"
        >
          {open ? "Show less" : "Show all"}
        </button>
      ) : null}
    </div>
  );
}

function HeaderGlyph({ status }: { status: StepPart["status"] | undefined }) {
  if (status === "running" || status === undefined) return <DotMatrixLoader label="Working" />;
  if (status === "failed") return <AlertCircle className="size-4 text-destructive" aria-label="Did not finish" />;
  return <Check className="size-4" aria-label="Done" />;
}

export function RoundBlock({
  round,
  renderStep,
}: {
  round: Round;
  renderStep: (step: StepPart) => React.ReactNode;
}) {
  const status = round.header?.status;
  const running = status === "running" || (status === undefined && !round.reply);
  return (
    <section aria-label={round.header?.label ?? "Claude Code"} className="flex flex-col gap-1.5 py-1">
      <div className="flex min-h-7 items-center gap-2 text-sm">
        <span className="flex size-4 shrink-0 items-center justify-center text-muted-foreground">
          <HeaderGlyph status={status} />
        </span>
        <span className={cn("min-w-0 truncate font-medium", running ? "sani-shimmer" : "text-foreground")}>
          {round.header?.label ?? "Claude Code"}
        </span>
        <span className="flex shrink-0 items-center gap-1 text-xs text-muted-foreground">
          <SquareTerminal className="size-3" aria-hidden="true" />
          Claude Code
        </span>
        <span className="flex-1" />
        {round.header?.durationMs ? (
          <span className="shrink-0 text-xs text-muted-foreground/80 tabular-nums">
            {formatDuration(round.header.durationMs)}
          </span>
        ) : null}
      </div>
      <div className="ml-2 flex flex-col gap-2 border-l border-border pl-4">
        {round.prompt?.detail ? <Quote label="Sani asked Claude Code" text={round.prompt.detail} /> : null}
        {round.steps.length > 0 ? (
          <div className="flex flex-col">{round.steps.map((step) => renderStep(step))}</div>
        ) : null}
        {round.notes.map((note) => (
          <div key={note.id} className="flex min-h-7 items-center gap-2 text-sm text-foreground">
            <Lock className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
            <span className="min-w-0">{note.label}</span>
          </div>
        ))}
        {round.reply?.detail ? <Quote label="Claude Code replied" text={round.reply.detail} markdown /> : null}
      </div>
    </section>
  );
}
