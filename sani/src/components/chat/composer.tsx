import {
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type ClipboardEvent,
  type DragEvent,
  type FormEvent,
  type KeyboardEvent,
} from "react";
import { ArrowUp, ChevronDown, FileText, Mic, Paperclip, Square, X } from "lucide-react";
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
import { onDictationFinal, saveAttachment, type AgentDescriptor, type ClaudeCodeStatus, type UiState } from "@/lib/tauri";
import {
  ACCEPT,
  MAX_ATTACHMENT_BYTES,
  fileToBase64,
  isImage,
  withAttachments,
} from "@/chat/attachments";
import { UsageMeter } from "./usage-meter";
import { useDeepContext } from "@/chat/use-deep-context";
import { useZCode } from "@/chat/use-zcode";

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
  /** The mic was started from this box: words land here as a draft. */
  dictating?: boolean;
  /** Live speech-to-text while dictating. */
  partial?: string;
  /** Claude Code usage facts, when the companion is on and reporting. */
  claudeCode?: ClaudeCodeStatus | null;
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
  dictating = false,
  partial = "",
  claudeCode = null,
}: ComposerProps) {
  const deepContext = useDeepContext();
  const zcode = useZCode();
  const [draft, setDraft] = useState("");
  const [error, setError] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [sending, setSending] = useState(false);
  const [dragging, setDragging] = useState(false);
  const textarea = useRef<HTMLTextAreaElement>(null);
  const picker = useRef<HTMLInputElement>(null);
  // Attachments go to Claude Code, so the control only exists when it can use them.
  const canAttach = Boolean(claudeCode?.enabled && claudeCode.claude.signed_in);
  const previews = useMemo(
    () => files.map((file) => (isImage(file.name) ? URL.createObjectURL(file) : "")),
    [files],
  );
  useEffect(() => () => previews.forEach((url) => url && URL.revokeObjectURL(url)), [previews]);
  const capturing = state === "listening" || state === "preparing" || state === "finalizing";
  const working = state === "working";
  const locked = working || capturing;
  const dictationLive = dictating && capturing;
  const shown = dictationLive && partial ? `${draft}${draft && !/\s$/.test(draft) ? " " : ""}${partial}` : draft;

  // Finished dictation joins the draft; the user reads it, edits it, then sends.
  useEffect(() => {
    let off: (() => void) | undefined;
    let gone = false;
    void onDictationFinal((text) => {
      setDraft((current) => `${current}${current && !/\s$/.test(current) ? " " : ""}${text}`);
      requestAnimationFrame(() => textarea.current?.focus());
    }).then((unlisten) => {
      if (gone) unlisten();
      else off = unlisten;
    });
    return () => {
      gone = true;
      off?.();
    };
  }, []);
  const canSend = (draft.trim().length > 0 || files.length > 0) && !locked && !sending;

  useLayoutEffect(() => {
    const el = textarea.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, MAX_HEIGHT_PX)}px`;
  }, [shown]);

  useEffect(() => {
    if (autoFocus) textarea.current?.focus();
  }, [autoFocus]);

  const addFiles = (incoming: File[]) => {
    if (!canAttach || incoming.length === 0) return;
    const accepted: File[] = [];
    for (const file of incoming) {
      if (file.size > MAX_ATTACHMENT_BYTES) {
        setError(`${file.name} is larger than ${MAX_ATTACHMENT_BYTES / (1024 * 1024)} MB.`);
        continue;
      }
      accepted.push(file);
    }
    if (accepted.length) {
      setError("");
      setFiles((current) => [...current, ...accepted].slice(0, 6));
    }
  };

  const submit = async (event?: FormEvent) => {
    event?.preventDefault();
    if (!canSend) return;
    setError("");
    setSending(true);
    try {
      const paths: string[] = [];
      for (const file of files) paths.push(await saveAttachment(file.name, await fileToBase64(file)));
      await onSend(withAttachments(draft.trim() || "See the attached file.", paths));
      setDraft("");
      setFiles([]);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setSending(false);
    }
  };

  const onPaste = (event: ClipboardEvent<HTMLTextAreaElement>) => {
    const pasted = Array.from(event.clipboardData.files);
    if (pasted.length && canAttach) {
      event.preventDefault();
      addFiles(pasted);
    }
  };
  const onDrop = (event: DragEvent<HTMLFormElement>) => {
    event.preventDefault();
    setDragging(false);
    addFiles(Array.from(event.dataTransfer.files));
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
      onDragOver={(event) => {
        if (canAttach) {
          event.preventDefault();
          setDragging(true);
        }
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={onDrop}
      className={cn(
        "rounded-(--sani-radius) border border-border bg-card shadow-(--sani-card-shadow) transition-shadow",
        "focus-within:border-slate-7 focus-within:shadow-(--sani-shell-shadow)",
        dragging && "border-dashed border-slate-8",
      )}
    >
      {files.length > 0 ? (
        <ul className="flex flex-wrap gap-2 px-3 pt-3" aria-label="Attached files">
          {files.map((file, index) => (
            <li
              key={`${file.name}-${index}`}
              className="flex h-9 max-w-52 items-center gap-2 rounded-lg border border-border bg-slate-2 pr-1 pl-1.5 text-xs"
            >
              {previews[index] ? (
                <img src={previews[index]} alt="" className="size-6 rounded object-cover" />
              ) : (
                <FileText className="size-4 text-muted-foreground" aria-hidden="true" />
              )}
              <span className="min-w-0 flex-1 truncate">{file.name}</span>
              <button
                type="button"
                aria-label={`Remove ${file.name}`}
                onClick={() => setFiles((current) => current.filter((_, i) => i !== index))}
                className="flex size-5 items-center justify-center rounded text-muted-foreground hover:bg-slate-4 hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/40 focus-visible:outline-none"
              >
                <X className="size-3" />
              </button>
            </li>
          ))}
        </ul>
      ) : null}
      <textarea
        ref={textarea}
        rows={1}
        value={shown}
        disabled={locked}
        aria-label="Ask Sani something"
        placeholder={dictationLive ? "Listening…" : capturing ? "Finish or cancel voice capture first" : "Ask Sani to do something…"}
        onChange={(event) => setDraft(event.target.value)}
        onKeyDown={onKeyDown}
        onPaste={onPaste}
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
          {canAttach ? (
            <>
              <input
                ref={picker}
                type="file"
                multiple
                hidden
                accept={ACCEPT}
                onChange={(event) => {
                  addFiles(Array.from(event.target.files ?? []));
                  event.target.value = "";
                }}
              />
              <Tooltip>
                <TooltipTrigger
                  render={
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon-sm"
                      disabled={locked || sending}
                      onClick={() => picker.current?.click()}
                      aria-label="Attach a file"
                      className="rounded-full text-muted-foreground hover:bg-secondary hover:text-foreground"
                    />
                  }
                >
                  <Paperclip className="size-4" />
                </TooltipTrigger>
                <TooltipContent>Attach an image or file for Claude Code</TooltipContent>
              </Tooltip>
            </>
          ) : null}
        </div>

        <div className="flex items-center gap-1.5">
          <UsageMeter status={claudeCode} deep={deepContext} zcode={zcode.status} />
          <Tooltip>
            <TooltipTrigger
              render={
                <Button
                  type="button"
                  variant="ghost"
                  size="icon-sm"
                  onClick={onMic}
                  disabled={working}
                  aria-label={capturing ? "Stop dictation" : "Dictate"}
                  className={cn(
                    "rounded-full text-muted-foreground hover:bg-secondary hover:text-foreground",
                    capturing &&
                      "relative bg-blue-10 text-foreground shadow-[0_0_0_4px_var(--blue-a4)] hover:bg-blue-11 hover:text-foreground",
                  )}
                />
              }
            >
              {capturing ? (
                <span
                  aria-hidden="true"
                  className="absolute inset-0 rounded-full bg-blue-10 opacity-40 motion-safe:animate-ping"
                />
              ) : null}
              <Mic className={"relative size-4"} />
            </TooltipTrigger>
            <TooltipContent>{dictationLive ? "Done" : capturing ? "Finish and send" : "Dictate"}</TooltipContent>
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
