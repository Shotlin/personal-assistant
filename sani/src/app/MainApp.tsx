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
  useEffect(() => { const cleanups: Array<() => void> = []; let mounted = true; const start = async () => { cleanups.push(await onState(setState), await onPartial(setPartial), await onHistoryLoaded(setMessages), await onMessage(message => setMessages(old => [...old, message])), await onAgentDone(done => { if (!done.assistant_message_id || !done.text.trim()) return; setMessages(old => old.some(message => message.id === done.assistant_message_id) ? old : [...old, { id: done.assistant_message_id, role: "assistant", text: done.text, created_at: done.created_at, run_id: done.run_id || null, agent_id: done.agent_id || null, agent_name: done.agent_name || null }]); }), await onConversationChanged(async id => { const restored = await getMessages(id); if (mounted) setMessages(restored); }), await listen("settings://open-full", () => setSection("settings"))); const current = await getState(); if (mounted) { setState(current.state); setPartial(current.partial); } if (!ready.current) { ready.current = true; await mainReady(); } }; void start(); return () => { mounted = false; cleanups.forEach(cleanup => cleanup()); }; }, []);
  return <main className="main-app-shell"><MainSidebar selected={section} onSelect={setSection}/>{section === "home" ? <MainConversation messages={messages} state={state} partial={partial}/> : section === "conversations" ? <ConversationsPage/> : section === "diagnostics" ? <DiagnosticsPage/> : section === "control" ? <ComputerControlPage/> : section === "voice" ? <FullSettings initialCategory="Voice" onOpenLayout={() => setSection("layout")}/> : section === "agents" ? <AgentsPage/> : section === "layout" ? <OverlayLayoutEditor/> : <FullSettings onOpenLayout={() => setSection("layout")}/>}</main>;
}

export default function MainApp() { return <SettingsProvider><MainContents /></SettingsProvider>; }
