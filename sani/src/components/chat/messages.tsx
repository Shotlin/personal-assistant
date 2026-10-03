import { memo } from "react";
import { CircleSlash } from "lucide-react";
import { DotMatrixLoader } from "@/components/ui/dot-matrix-loader";
import { itemSteps, itemText, type ChatItem } from "@/chat/types";
import { formatClock } from "@/lib/format";
import { Markdown } from "./markdown";
import { StepRail } from "./step-rail";

export const UserMessage = memo(function UserMessage({ item }: { item: ChatItem }) {
  return (
    <div className="flex justify-end">
      <div
        className="max-w-[80%] rounded-2xl bg-secondary px-3.5 py-2 leading-relaxed break-words whitespace-pre-wrap text-foreground select-text"
        title={formatClock(item.createdAt)}
      >
        {itemText(item)}
      </div>
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

export const AssistantMessage = memo(function AssistantMessage({ item }: { item: ChatItem }) {
  const text = itemText(item);
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

export function MessageItem({ item }: { item: ChatItem }) {
  return item.role === "user" ? <UserMessage item={item} /> : <AssistantMessage item={item} />;
}
