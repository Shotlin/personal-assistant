import { useCallback, useEffect, useState } from "react";
import { deepContext, onAgentDone, type DeepContext } from "@/lib/tauri";
import { useChatStore } from "./store";

/**
 * How full Sani's own (Deep Agent) conversation context is. Read after every
 * finished turn and when the chat changes; an unreadable answer is "unknown".
 */
const OVERHEAD_TOKENS = 3000; // system prompt + tool definitions, a rough allowance

export function useDeepContext(): DeepContext | null {
  const reported = useReported();
  const items = useChatStore((state) => state.items);
  if (reported?.known) return reported;
  if (!reported?.window || items.length === 0) return null;
  let chars = 0;
  for (const item of items) for (const part of item.parts) if (part.type === "text") chars += part.text.length;
  const tokens = OVERHEAD_TOKENS + Math.round(chars / 4);
  return {
    known: true,
    tokens,
    window: reported.window,
    percent: Math.min(100, Math.round((100 * tokens) / reported.window)),
    estimated: true,
    model: reported.model,
  };
}

function useReported(): DeepContext | null {
  const conversationId = useChatStore((state) => state.activeConversationId);
  const [value, setValue] = useState<DeepContext | null>(null);

  const refresh = useCallback(async () => {
    if (!conversationId) {
      setValue(null);
      return;
    }
    try {
      setValue(await deepContext(conversationId));
    } catch {
      setValue(null);
    }
  }, [conversationId]);

  useEffect(() => {
    void refresh();
    let off: (() => void) | undefined;
    let gone = false;
    void onAgentDone(() => void refresh()).then((unlisten) => {
      if (gone) unlisten();
      else off = unlisten;
    });
    return () => {
      gone = true;
      off?.();
    };
  }, [refresh]);

  return value;
}
