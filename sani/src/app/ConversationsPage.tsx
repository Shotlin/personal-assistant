import { useCallback, useEffect, useState } from "react";
import ActivityTimeline from "../components/ActivityTimeline";
import { deleteConversation, getMessages, getRunActivity, listConversations, selectConversation, type ActivityEvent, type ChatMessage, type Conversation } from "../lib/tauri";

export default function ConversationsPage() {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [selected, setSelected] = useState("");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [activity, setActivity] = useState<ActivityEvent[]>([]);
  const [details, setDetails] = useState(false);
  const refresh = useCallback(async () => setConversations(await listConversations()), []);
  const open = useCallback(async (id: string) => {
    await selectConversation(id);
    const [nextMessages, nextActivity] = await Promise.all([getMessages(id), getRunActivity(id)]);
    setSelected(id); setMessages(nextMessages); setActivity(nextActivity);
  }, []);
  useEffect(() => { void refresh(); }, [refresh]);
  return <section className="conversations-page"><header><div><h1>Conversations</h1><p>Stored locally on this Mac. Execution details exclude secrets and captured content.</p></div>{selected && <button onClick={() => setDetails(!details)}>{details ? "Hide details" : "Execution details"}</button>}</header><div className="conversation-browser"><aside>{conversations.map((conversation) => <button key={conversation.id} className={selected === conversation.id ? "selected" : ""} onClick={() => void open(conversation.id)}><strong>{conversation.title}</strong><span>{new Date(conversation.updated_at).toLocaleDateString()}</span></button>)}</aside><div className="conversation-detail">{!selected ? <p>Select a conversation to view it.</p> : <>{messages.map((message) => <article className={`main-message ${message.role}`} key={message.id}><div className="message-meta"><strong>{message.role === "user" ? "You" : message.agent_name || "Sani"}</strong><time>{new Date(message.created_at).toLocaleString()}</time></div><p>{message.text}</p></article>)}{details && <ActivityTimeline events={activity} status="" working={false}/>}<button className="conversation-delete" onClick={() => { if (window.confirm("Delete this conversation and its local execution details?")) void deleteConversation(selected).then(() => { setSelected(""); setMessages([]); setActivity([]); return refresh(); }); }}>Delete conversation</button></>}</div></div></section>;
}
