import { useEffect, useState } from "react";
import MainSidebar, { type MainSection } from "../components/MainSidebar";
import MainConversation from "../components/MainConversation";
import { getMessages, getState, mainReady, onConversationChanged, onHistoryLoaded, onMessage, onPartial, onState, type ChatMessage, type UiState } from "../lib/tauri";
import "../styles/tokens.css";
import "../styles/main.css";
export default function MainApp() {
  const [section, setSection] = useState<MainSection>("home"); const [messages,setMessages] = useState<ChatMessage[]>([]); const [state,setState] = useState<UiState>("idle"); const [partial,setPartial] = useState("");
  useEffect(() => { const cleanups: Array<() => void> = []; const start = async () => { cleanups.push(await onState(setState), await onPartial(setPartial), await onHistoryLoaded(setMessages), await onMessage(message => setMessages(old => [...old, message])), await onConversationChanged(async id => setMessages(await getMessages(id)))); const current = await getState(); setState(current.state); setPartial(current.partial); await mainReady(); }; void start(); return () => cleanups.forEach(cleanup => cleanup()); }, []);
  return <main className="main-app-shell"><MainSidebar selected={section} onSelect={setSection}/>{section === "home" ? <MainConversation messages={messages} state={state} partial={partial}/> : <section className="section-boundary"><h1>{section === "control" ? "Computer Control" : section[0].toUpperCase()+section.slice(1)}</h1><p>This section is part of Sani’s desktop workspace and will gain its dedicated controls in a later approved phase.</p></section>}</main>;
}
