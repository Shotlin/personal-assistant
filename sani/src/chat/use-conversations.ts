import { useCallback, useEffect, useState } from "react";
import {
  deleteConversation,
  getMessages,
  getRunActivity,
  listConversations,
  newConversation,
  onAgentDone,
  onConversationChanged,
  onMessage,
  selectConversation,
  type Conversation,
} from "@/lib/tauri";
import { useChatStore } from "./store";

/** The conversation list for the sidebar, kept fresh by host events. */
export function useConversations() {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const activeId = useChatStore((state) => state.activeConversationId);

  const refresh = useCallback(() => {
    void listConversations()
      .then((list) => setConversations([...list].sort((a, b) => b.updated_at - a.updated_at)))
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    refresh();
    const offs: Array<Promise<() => void>> = [
      onConversationChanged(() => refresh()),
      onMessage(() => refresh()),
      onAgentDone(() => refresh()),
    ];
    return () => {
      offs.forEach((off) => void off.then((fn) => fn()).catch(() => undefined));
    };
  }, [refresh]);

  const open = useCallback(async (id: string) => {
    await selectConversation(id);
    const store = useChatStore.getState();
    store.setActiveConversation(id);
    const [messages, activity] = await Promise.all([getMessages(id), getRunActivity(id).catch(() => [])]);
    store.loadHistory(messages, activity);
  }, []);

  const create = useCallback(async () => {
    const id = await newConversation();
    const store = useChatStore.getState();
    store.setActiveConversation(id);
    store.loadHistory([]);
    refresh();
  }, [refresh]);

  const remove = useCallback(
    async (id: string) => {
      await deleteConversation(id);
      if (useChatStore.getState().activeConversationId === id) {
        useChatStore.getState().loadHistory([]);
        useChatStore.getState().setActiveConversation("");
      }
      refresh();
    },
    [refresh],
  );

  return { conversations, activeId, open, create, remove };
}
