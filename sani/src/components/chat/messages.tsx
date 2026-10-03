import { memo } from "react";
import { CircleSlash, FileText, ImageIcon } from "lucide-react";
import { DotMatrixLoader } from "@/components/ui/dot-matrix-loader";
import { itemSteps, itemText, type ChatItem } from "@/chat/types";
import { formatClock } from "@/lib/format";
import { isImage, splitAttachments } from "@/chat/attachments";
import { hideOptionsWhileStreaming, splitOptions } from "@/chat/options";
import { useQuickReply } from "@/chat/quick-reply";
import { Markdown } from "./markdown";
import { StepRail } from "./step-rail";

export const UserMessage = memo(function UserMessage({ item }: { item: ChatItem }) {
  const { text, files } = splitAttachments(itemText(item));
  return (
    <div className="flex flex-col items-end gap-1.5">
      {files.length > 0 ? (
        <ul className="flex max-w-[80%] flex-wrap justify-end gap-1.5" aria-label="Attached files">
          {files.map((file) => (
            <li
              key={file.path}
              title={file.path}
              className="flex h-7 max-w-52 items-center gap-1.5 rounded-lg border border-border bg-slate-2 px-2 text-xs text-muted-foreground"
            >
              {isImage(file.name) ? (
                <ImageIcon className="size-3.5 shrink-0" aria-hidden="true" />
              ) : (
                <FileText className="size-3.5 shrink-0" aria-hidden="true" />
              )}
              <span className="truncate">{file.name}</span>
            </li>
          ))}
        </ul>
      ) : null}
      {text ? (
        <div
          className="max-w-[80%] rounded-2xl bg-secondary px-3.5 py-2 leading-relaxed break-words whitespace-pre-wrap text-foreground select-text"
          title={formatClock(item.createdAt)}
        >
          {text}
        </div>
      ) : null}
    </div>
  );
});

/** What the live turn is doing right now, in one quiet line. */
function ThinkingLine({ text }: { text: string }) {
  return (
    <div className="flex min-h-7 items-center gap-2 text-sm text-muted-foreground" role="status">
      <DotMatrixLoader label="Working" />
      <span className="sani-shimmer truncate">{text}</span>
    </div>
  );
}

export const AssistantMessage = memo(function AssistantMessage({
  item,
  isLast = false,
}: {
  item: ChatItem;
  isLast?: boolean;
}) {
  const quick = useQuickReply();
  const raw = itemText(item);
  const streaming = item.status === "streaming";
  const split = splitOptions(raw);
  const text = streaming ? hideOptionsWhileStreaming(raw) : split.text;
  const options = streaming ? [] : split.options;
  const hasSteps = itemSteps(item).length > 0;
  const live = item.status === "streaming";
  const stopped = item.status === "cancelled";
  const failed = item.status === "failed";
  return (
    <div className="flex flex-col gap-1.5" title={formatClock(item.createdAt)}>
      {item.agentName ? (
        <span className="text-xs font-medium text-muted-foreground">{item.agentName}</span>
      ) : null}
      <StepRail item={item} />
      {live && !text && !hasSteps ? <ThinkingLine text={item.statusLine || "Working…"} /> : null}
      {text ? <Markdown text={text} /> : null}
      {options.length > 0 ? (
        <div className="flex flex-wrap gap-2 pt-1" role="group" aria-label="Choose an answer">
          {options.map((option) => (
            <button
              key={option}
              type="button"
              disabled={!isLast || !quick.enabled}
              onClick={() => quick.send(option)}
              className="rounded-full border border-border bg-card px-3 py-1.5 text-sm text-foreground shadow-(--sani-card-shadow) transition-colors hover:border-slate-7 hover:bg-slate-2 focus-visible:ring-2 focus-visible:ring-ring/40 focus-visible:outline-none disabled:opacity-50 disabled:shadow-none disabled:hover:bg-card"
            >
              {option}
            </button>
          ))}
        </div>
      ) : null}
      {stopped ? (
        <div className="flex items-center gap-1.5 text-sm text-muted-foreground">
          <CircleSlash className="size-3.5" aria-hidden="true" />
          <span>Stopped</span>
        </div>
      ) : null}
      {failed ? (
        <div className="text-sm text-destructive" role="alert">
          {item.error || "That didn't finish. Try again, or rephrase the request."}
        </div>
      ) : null}
    </div>
  );
});

export function MessageItem({ item, isLast = false }: { item: ChatItem; isLast?: boolean }) {
  return item.role === "user" ? <UserMessage item={item} /> : <AssistantMessage item={item} isLast={isLast} />;
}
