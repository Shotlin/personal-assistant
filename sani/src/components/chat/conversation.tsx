import { useCallback, useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { ArrowDown } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { ChatItem } from "@/chat/types";
import { MessageItem } from "./messages";

/** Same threshold OpenWork uses: within 16px of the end counts as "at the end". */
const PIN_GAP_PX = 16;

/**
 * Scrolling transcript. Follows new content only while the reader is at the
 * bottom; scrolling up releases it and offers a quiet "Jump to latest".
 */
export function Conversation({ items, footer }: { items: ChatItem[]; footer?: ReactNode }) {
  const scroller = useRef<HTMLDivElement>(null);
  const pinned = useRef(true);
  const [showJump, setShowJump] = useState(false);

  const atBottom = useCallback(() => {
    const el = scroller.current;
    if (!el) return true;
    return el.scrollHeight - (el.scrollTop + el.clientHeight) <= PIN_GAP_PX;
  }, []);

  const scrollToEnd = useCallback((smooth = false) => {
    const el = scroller.current;
    if (!el) return;
    el.scrollTo({ top: el.scrollHeight, behavior: smooth ? "smooth" : "auto" });
  }, []);

  const onScroll = useCallback(() => {
    const at = atBottom();
    pinned.current = at;
    setShowJump(!at);
  }, [atBottom, setShowJump]);

  // Follow growth: new items, streamed text and step updates all change height.
  const signature = items.map((item) => `${item.id}:${item.parts.length}:${textLength(item)}`).join("|");
  useLayoutEffect(() => {
    if (pinned.current) scrollToEnd();
  }, [signature, scrollToEnd]);

  // Footer growth (voice draft, approval card) should keep the end in view too.
  useEffect(() => {
    const el = scroller.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(() => {
      if (pinned.current) scrollToEnd();
    });
    const inner = el.firstElementChild;
    if (inner) observer.observe(inner);
    return () => observer.disconnect();
  }, [scrollToEnd]);

  return (
    <div className="relative min-h-0 flex-1">
      <div ref={scroller} onScroll={onScroll} className="h-full overflow-y-auto">
        <div className="mx-auto flex w-full max-w-[760px] flex-col gap-6 px-6 pt-6 pb-4">
          {items.map((item) => (
            <MessageItem key={item.id} item={item} />
          ))}
          {footer}
        </div>
      </div>
      {showJump ? (
        <div className="pointer-events-none absolute inset-x-0 bottom-3 flex justify-center">
          <Button
            variant="outline"
            size="sm"
            className="pointer-events-auto bg-background shadow-(--sani-card-shadow)"
            onClick={() => {
              pinned.current = true;
              setShowJump(false);
              scrollToEnd(true);
            }}
          >
            <ArrowDown className="size-3.5" aria-hidden="true" />
            Jump to latest
          </Button>
        </div>
      ) : null}
    </div>
  );
}

function textLength(item: ChatItem): number {
  let total = 0;
  for (const part of item.parts) if (part.type === "text") total += part.text.length;
  return total;
}
