import { useEffect, useRef, useState } from "react";
import { listen } from "@tauri-apps/api/event";
import MainSidebar, { type MainSection } from "../components/MainSidebar";
import MainConversation from "../components/MainConversation";
import OverlayLayoutEditor from "./OverlayLayoutEditor";
import FullSettings from "./settings/FullSettings";
import AgentsPage from "./AgentsPage";
import ConversationsPage from "./ConversationsPage";
import { SettingsProvider } from "./settings/SettingsContext";
import { getMessages, getState, mainReady, onConversationChanged, onHistoryLoaded, onMessage, onPartial, onState, type ChatMessage, type UiState } from "../lib/tauri";
import "../styles/tokens.css";
import "../styles/main.css";
function MainContents() {
  const [section, setSection] = useState<MainSection>("home"); const [messages,setMessages] = useState<ChatMessage[]>([]); const [state,setState] = useState<UiState>("idle"); const [partial,setPartial] = useState("");
  const ready = useRef(false);
  useEffect(() => { const cleanups: Array<() => void> = []; let mounted = true; const start = async () => { cleanups.push(await onState(setState), await onPartial(setPartial), await onHistoryLoaded(setMessages), await onMessage(message => setMessages(old => [...old, message])), await onConversationChanged(async id => { const restored = await getMessages(id); if (mounted) setMessages(restored); }), await listen("settings://open-full", () => setSection("settings"))); const current = await getState(); if (mounted) { setState(current.state); setPartial(current.partial); } if (!ready.current) { ready.current = true; await mainReady(); } }; void start(); return () => { mounted = false; cleanups.forEach(cleanup => cleanup()); }; }, []);
  return <main className="main-app-shell"><MainSidebar selected={section} onSelect={setSection}/>{section === "home" ? <MainConversation messages={messages} state={state} partial={partial}/> : section === "conversations" ? <ConversationsPage/> : section === "agents" ? <AgentsPage/> : section === "layout" ? <OverlayLayoutEditor/> : section === "settings" ? <FullSettings onOpenLayout={() => setSection("layout")}/> : <section className="section-boundary"><h1>{section === "control" ? "Computer Control" : section[0].toUpperCase()+section.slice(1)}</h1><p>This section is part of Sani’s desktop workspace and will gain its dedicated controls in a later approved phase.</p></section>}</main>;
}

export default function MainApp() { return <SettingsProvider><MainContents /></SettingsProvider>; }
