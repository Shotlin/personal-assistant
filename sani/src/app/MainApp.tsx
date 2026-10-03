import { useEffect, useRef, useState } from "react";
import { listen } from "@tauri-apps/api/event";
import MainSidebar, { type MainSection } from "../components/MainSidebar";
import MainConversation from "../components/MainConversation";
import OverlayLayoutEditor from "./OverlayLayoutEditor";
import FullSettings from "./settings/FullSettings";
import AgentsPage from "./AgentsPage";
import ConversationsPage from "./ConversationsPage";
import DiagnosticsPage from "./DiagnosticsPage";
import ComputerControlPage from "./ComputerControlPage";
import { SettingsProvider } from "./settings/SettingsContext";
import { getMessages, getState, mainReady, onAgentDone, onConversationChanged, onHistoryLoaded, onMessage, onPartial, onState, type ChatMessage, type UiState } from "../lib/tauri";
import "../styles/tokens.css";
import "../styles/main.css";
function MainContents() {
  const [section, setSection] = useState<MainSection>("home"); const [messages,setMessages] = useState<ChatMessage[]>([]); const [state,setState] = useState<UiState>("idle"); const [partial,setPartial] = useState("");
  const ready = useRef(false);
  useEffect(() => {
    const cleanups: Array<() => void> = [];
    let mounted = true;
    const subscribe = async (registration: Promise<() => void>) => {
      const cleanup = await registration;
      if (mounted) cleanups.push(cleanup);
      else cleanup();
    };
    const start = async () => {
      const subscriptions = await Promise.allSettled([
        subscribe(onState(setState)),
        subscribe(onPartial(setPartial)),
        subscribe(onHistoryLoaded(setMessages)),
        subscribe(onMessage(message => setMessages(old => [...old, message]))),
        subscribe(onAgentDone(done => {
          if (!done.assistant_message_id || !done.text.trim()) return;
          setMessages(old => old.some(message => message.id === done.assistant_message_id)
            ? old
            : [...old, { id: done.assistant_message_id, role: "assistant", text: done.text,
                created_at: done.created_at, run_id: done.run_id || null,
                agent_id: done.agent_id || null, agent_name: done.agent_name || null }]);
        })),
        subscribe(onConversationChanged(async id => {
          try {
            const restored = await getMessages(id);
            if (mounted) setMessages(restored);
          } catch (error) {
            console.error("[main] conversation refresh failed", error);
          }
        })),
        subscribe(listen("settings://open-full", () => setSection("settings"))),
      ]);
      for (const subscription of subscriptions) {
        if (subscription.status === "rejected") {
          console.error("[main] event subscription failed", subscription.reason);
        }
      }
      if (!mounted) return;
      const current = await getState();
      if (!mounted) return;
      setState(current.state);
      setPartial(current.partial);
      // Native history events can arrive before this WebView subscribes. Read
      // the active conversation once so Home always reflects persisted truth.
      if (current.active_conversation_id) {
        const restored = await getMessages(current.active_conversation_id);
        if (mounted) setMessages(restored);
      }
      if (mounted && !ready.current) {
        ready.current = true;
        await mainReady();
      }
    };
    void start().catch(error => console.error("[main] startup failed", error));
    return () => { mounted = false; cleanups.forEach(cleanup => cleanup()); };
  }, []);
  return <main className="main-app-shell"><MainSidebar selected={section} onSelect={setSection}/>{section === "home" ? <MainConversation messages={messages} state={state} partial={partial}/> : section === "conversations" ? <ConversationsPage/> : section === "diagnostics" ? <DiagnosticsPage/> : section === "control" ? <ComputerControlPage/> : section === "voice" ? <FullSettings initialCategory="Voice" onOpenLayout={() => setSection("layout")}/> : section === "agents" ? <AgentsPage/> : section === "layout" ? <OverlayLayoutEditor/> : <FullSettings onOpenLayout={() => setSection("layout")}/>}</main>;
}

export default function MainApp() { return <SettingsProvider><MainContents /></SettingsProvider>; }
