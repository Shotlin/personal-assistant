import { useEffect } from "react";
import { listen } from "@tauri-apps/api/event";
import {
  getMessages,
  getRunActivity,
  getState,
  mainReady,
  onActivity,
  onAgentChunk,
  onAgentDone,
  onAgentStart,
  onConversationChanged,
  onHistoryLoaded,
  onMessage,
  onPartial,
  onState,
} from "@/lib/tauri";
import { useChatStore } from "./store";

/**
 * Subscribes the main window's chat store to the host's event stream and
 * hydrates it from persisted history. Mount once, at the root of the window.
 *
 * Native history events can arrive before this WebView subscribes, so after
 * subscribing the active conversation is read once: the store always starts
 * from persisted truth, then live events fold on top.
 */
export function useChatEvents(onOpenSettings: () => void): void {
  useEffect(() => {
    const store = useChatStore.getState();
    const cleanups: Array<() => void> = [];
    let mounted = true;
    let announced = false;

    const subscribe = async (registration: Promise<() => void>) => {
      const cleanup = await registration;
      if (mounted) cleanups.push(cleanup);
      else cleanup();
    };

    const hydrate = async (conversationId: string) => {
      store.setActiveConversation(conversationId);
      const [messages, activity] = await Promise.all([
        getMessages(conversationId),
        getRunActivity(conversationId).catch(() => []),
      ]);
      if (mounted) store.loadHistory(messages, activity);
    };

    const start = async () => {
      const results = await Promise.allSettled([
        subscribe(onState(store.setUiState)),
        subscribe(onPartial(store.setPartial)),
        subscribe(onHistoryLoaded((messages) => store.loadHistory(messages))),
        subscribe(onMessage(store.addMessage)),
        subscribe(onAgentStart(store.startAgent)),
        subscribe(onAgentChunk(store.applyChunk)),
        subscribe(onActivity(store.applyActivity)),
        subscribe(onAgentDone(store.finishAgent)),
        subscribe(
          onConversationChanged(async (id) => {
            try {
              await hydrate(id);
            } catch (error) {
              console.error("[main] conversation refresh failed", error);
            }
          }),
        ),
        subscribe(listen("settings://open-full", onOpenSettings)),
      ]);
      for (const result of results) {
        if (result.status === "rejected") {
          console.error("[main] event subscription failed", result.reason);
        }
      }
      if (!mounted) return;
      const current = await getState();
      if (!mounted) return;
      store.setUiState(current.state);
      store.setPartial(current.partial);
      if (current.active_conversation_id) await hydrate(current.active_conversation_id);
      if (mounted && !announced) {
        announced = true;
        await mainReady();
      }
    };

    void start().catch((error) => console.error("[main] startup failed", error));
    return () => {
      mounted = false;
      cleanups.forEach((cleanup) => cleanup());
    };
    // The settings callback is stable for the window's lifetime.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
}
