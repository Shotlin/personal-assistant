import { useEffect, useLayoutEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { ArrowUp, ChevronDown, Mic, Square } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";
import type { AgentDescriptor, UiState } from "@/lib/tauri";

const MAX_HEIGHT_PX = 220;

export interface ComposerProps {
  state: UiState;
  agents: AgentDescriptor[];
  agentsAvailable: boolean;
  agentMode: string;
  onSelectAgent: (id: string) => void;
  /** Resolves when the host accepted the text; throws with a readable message. */
  onSend: (text: string) => Promise<void>;
  onMic: () => void;
  onStop: () => void;
  autoFocus?: boolean;
}

/** One quiet key hint, shown as a chord only (DESIGN T5). */
function Chord({ children }: { children: string }) {
  return (
    <kbd className="rounded border border-border bg-slate-2 px-1 font-sans text-[11px] text-muted-foreground">
      {children}
    </kbd>
  );
}

export function Composer({
  state,
  agents,
  agentsAvailable,
  agentMode,
  onSelectAgent,
  onSend,
  onMic,
  onStop,
  autoFocus,
}: ComposerProps) {
  const [draft, setDraft] = useState("");
  const [error, setError] = useState("");
  const textarea = useRef<HTMLTextAreaElement>(null);
  const capturing = state === "listening" || state === "preparing" || state === "finalizing";
  const working = state === "working";
  const locked = working || capturing;
  const canSend = draft.trim().length > 0 && !locked;

  useLayoutEffect(() => {
    const el = textarea.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, MAX_HEIGHT_PX)}px`;
  }, [draft]);

  useEffect(() => {
    if (autoFocus) textarea.current?.focus();
  }, [autoFocus]);

  const submit = async (event?: FormEvent) => {
    event?.preventDefault();
    if (!canSend) return;
    setError("");
    try {
      await onSend(draft);
      setDraft("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  };

  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      void submit();
    } else if (event.key === "Escape" && working) {
      onStop();
    }
  };

  const selectedAgent = agents.find((agent) => agent.id === agentMode);
  const agentLabel = selectedAgent?.name ?? (agentMode === "auto" ? "Auto" : agentMode || "Agent");

  return (
    <form
      onSubmit={submit}
      aria-label="Message composer"
      className={cn(
        "rounded-(--sani-radius) border border-border bg-card shadow-(--sani-card-shadow) transition-shadow",
        "focus-within:border-slate-7 focus-within:shadow-(--sani-shell-shadow)",
      )}
    >
      <textarea
        ref={textarea}
        rows={1}
        value={draft}
        disabled={locked}
        aria-label="Ask Sani something"
        placeholder={capturing ? "Finish or cancel voice capture first" : "Ask Sani to do something…"}
        onChange={(event) => setDraft(event.target.value)}
        onKeyDown={onKeyDown}
        className="block max-h-[220px] min-h-[52px] w-full resize-none bg-transparent px-4 pt-3.5 pb-1 text-[14px] leading-relaxed text-foreground outline-none placeholder:text-muted-foreground disabled:opacity-60"
      />
      <div className="flex items-center justify-between gap-2 px-2.5 pb-2.5">
        <div className="flex min-w-0 items-center gap-1">
          <DropdownMenu>
            <DropdownMenuTrigger
              render={
                <Button
                  type="button"
                  variant="ghost"
                  size="xs"
                  disabled={!agentsAvailable || locked}
                  className="rounded-full text-muted-foreground hover:bg-secondary hover:text-foreground"
                  aria-label="Choose agent for the next turn"
                />
              }
            >
              <span className="truncate">{agentLabel}</span>
              <ChevronDown className="size-3" aria-hidden="true" />
            </DropdownMenuTrigger>
            <DropdownMenuContent align="start" className="min-w-40">
              <DropdownMenuRadioGroup value={agentMode} onValueChange={(value) => onSelectAgent(String(value))}>
                {agents.map((agent) => (
                  <DropdownMenuRadioItem key={agent.id} value={agent.id}>
                    {agent.name}
                  </DropdownMenuRadioItem>
                ))}
              </DropdownMenuRadioGroup>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>

        <div className="flex items-center gap-1.5">
          {!locked && draft.trim() ? (
            <span className="hidden items-center gap-1 text-xs text-muted-foreground sm:flex">
              <Chord>⏎</Chord> send <Chord>⇧⏎</Chord> new line
            </span>
          ) : null}
          <Tooltip>
            <TooltipTrigger
              render={
                <Button
                  type="button"
                  variant="ghost"
                  size="icon-sm"
                  onClick={onMic}
                  disabled={working}
                  aria-label={capturing ? "Finish voice capture" : "Start voice"}
                  className={cn(
                    "rounded-full text-muted-foreground hover:bg-secondary hover:text-foreground",
                    capturing && "bg-secondary text-foreground",
                  )}
                />
              }
            >
              <Mic className="size-4" />
            </TooltipTrigger>
            <TooltipContent>{capturing ? "Finish and send" : "Speak"}</TooltipContent>
          </Tooltip>
          {working ? (
            <Button
              type="button"
              size="icon-sm"
              onClick={onStop}
              aria-label="Stop"
              className="rounded-full"
            >
              <Square className="size-3 fill-current" />
            </Button>
          ) : (
            <Button
              type="submit"
              size="icon-sm"
              disabled={!canSend}
              aria-label="Send"
              className="rounded-full bg-primary text-primary-foreground hover:bg-(--sani-accent-hover) disabled:bg-slate-4 disabled:text-slate-9 disabled:opacity-100"
            >
              <ArrowUp className="size-4" />
            </Button>
          )}
        </div>
      </div>
      {error ? (
        <p className="px-4 pb-3 text-sm text-destructive" role="alert">
          {error}
        </p>
      ) : null}
    </form>
  );
}
