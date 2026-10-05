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
 * Bring the chat back in line with what the host actually saved.
 *
 * The window can miss live events (it was minimized, hidden behind another app, or on another
 * page), which used to leave a finished run showing "running" forever. The host keeps the truth:
 * the UI state, the saved messages and the finished steps. When the run is over, a stale
 * "streaming" turn or a missing message is replaced from history; while it is still going, the
 * live turn is kept and only what was missed is added.
 */
export async function reconcileChat(): Promise<void> {
  try {
    const current = await getState();
    const store = useChatStore.getState();
    store.setUiState(current.state);
    store.setPartial(current.partial);
    const id = current.active_conversation_id;
    if (!id) return;
    const [messages, activity] = await Promise.all([
      getMessages(id),
      getRunActivity(id).catch(() => []),
    ]);
    const now = useChatStore.getState();
    if (now.activeConversationId !== id) now.setActiveConversation(id);
    if (current.state === "working") {
      now.resumeLive(messages, activity);
      return;
    }
    // A new run may have started while we were asking: never wipe a live turn.
    if (now.uiState === "working") return;
    const have = new Set(now.items.map((item) => item.id));
    const stale = now.items.some((item) => item.status === "streaming");
    const missing = messages.some((message) => !have.has(message.id));
    if (stale || missing) now.loadHistory(messages, activity);
  } catch (error) {
    console.error("[main] chat reconcile failed", error);
  }
}

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

    // Catch up whenever the window comes back, and while a run is going in case events were missed.
    const resync = () => {
      if (mounted) void reconcileChat();
    };
    const onVisible = () => {
      if (document.visibilityState === "visible") resync();
    };
    window.addEventListener("focus", resync);
    document.addEventListener("visibilitychange", onVisible);
    const timer = window.setInterval(() => {
      const chat = useChatStore.getState();
      if (chat.uiState === "working" || chat.items.some((item) => item.status === "streaming")) resync();
    }, 3000);
    return () => {
      mounted = false;
      cleanups.forEach((cleanup) => cleanup());
      window.removeEventListener("focus", resync);
      document.removeEventListener("visibilitychange", onVisible);
      window.clearInterval(timer);
    };
    // The settings callback is stable for the window's lifetime.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
}
