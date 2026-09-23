import { useEffect, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { useSettings } from "./SettingsContext";
import { clearRunActivity, computerControlSnapshot, setTechnicalRetention, type ComputerControlSnapshot } from "../../lib/tauri";

const CATEGORIES = ["General", "AI & Models", "Voice", "Microphone", "Computer Control", "Appearance", "Shortcuts", "Startup", "Storage"] as const;
type Category = typeof CATEGORIES[number];

export default function FullSettings({ onOpenLayout, initialCategory = "General" }: { onOpenLayout: () => void; initialCategory?: Category }) {
  const { snapshot, microphones, voiceModels, voiceProgress, error, saveGeneral, saveAi, saveProviderKey, installVoice, useVoice, removeVoice } = useSettings();
  const [category, setCategory] = useState<Category>(initialCategory);
  const [voiceBusy, setVoiceBusy] = useState("");
  const [openRouterCandidate, setOpenRouterCandidate] = useState("");
  const [typeSafeCandidate, setTypeSafeCandidate] = useState("");
  const [storageNotice, setStorageNotice] = useState("");
  const [control, setControl] = useState<ComputerControlSnapshot | null>(null);
  const refreshControl = () => void computerControlSnapshot().then(setControl).catch(() => setControl(null));
  useEffect(() => { refreshControl(); }, []);
  useEffect(() => { setCategory(initialCategory); }, [initialCategory]);
  if (!snapshot) return <section className="full-settings"><p>Loading settings…</p></section>;
  const keyEditor = (provider: "openrouter" | "typesafe", value: string, setValue: (v: string) => void, label: string) => (
    <div className="settings-card">
      <strong>{label}</strong><span className="settings-muted">Keychain: {provider === "openrouter" ? snapshot.openrouter_key : snapshot.typesafe_key}</span>
      <div className="settings-inline"><input type="password" value={value} placeholder="Paste key to replace; blank clears" onChange={(e) => setValue(e.target.value)} /><button onClick={() => void saveProviderKey(provider, value).then(() => setValue(""))}>Apply key</button></div>
    </div>
  );
  const runVoiceAction = async (model: string, action: () => Promise<void>) => {
    setVoiceBusy(model);
    try { await action(); } finally { setVoiceBusy(""); }
  };
  const voiceLabel = (id: string) => {
    const prefix = id.split("-")[0];
    const name = prefix.charAt(0).toUpperCase() + prefix.slice(1);
    const detail = prefix === "tiny" ? "Fastest, smallest local footprint" : prefix === "base" ? "Lightweight local speech" : prefix === "medium" ? "Higher accuracy, more resources" : "Balanced everyday voice";
    return { name, detail };
  };
  return <section className="full-settings" aria-label="Full Settings">
    <header><div><h1>Settings</h1><p>Desired settings are saved locally; runtime status is reported separately.</p></div><span className={`runtime-status ${snapshot.runtime_status}`}>{snapshot.runtime_status.replaceAll("_", " ")}</span></header>
    <div className="full-settings-grid"><nav aria-label="Settings categories">{CATEGORIES.map((item) => <button key={item} className={category === item ? "selected" : ""} onClick={() => setCategory(item)}>{item}</button>)}</nav><div className="settings-content">
      {error && <p className="settings-error" role="alert">{error}</p>}
      {category === "General" && <><h2>General</h2><label>Theme<select value={snapshot.theme} onChange={(e) => void saveGeneral({ theme: e.target.value })}><option value="dark">Dark</option><option value="light">Light</option></select></label><label>Agent mode<input defaultValue={snapshot.agent_mode} onBlur={(e) => void saveGeneral({ agent_mode: e.target.value })}/></label></>}
      {category === "AI & Models" && <><h2>AI &amp; Models</h2><div className="settings-card"><strong>Deep Agent</strong><span className="settings-muted">Provider: OpenRouter</span><label>Model ID<input defaultValue={snapshot.reasoning_model} onBlur={(e) => void saveAi({ reasoning_model: e.target.value })}/></label></div><div className="settings-card"><strong>Velo / JEV</strong><label>Provider<select value={snapshot.velo_provider} onChange={(e) => void saveAi({ velo_provider: e.target.value as "openrouter" | "typesafe" })}><option value="openrouter">OpenRouter</option><option value="typesafe">TypeSafe</option></select></label><label>Model ID<input defaultValue={snapshot.velo_model} onBlur={(e) => void saveAi({ velo_model: e.target.value })}/></label></div>{keyEditor("openrouter", openRouterCandidate, setOpenRouterCandidate, "OpenRouter credential")}{keyEditor("typesafe", typeSafeCandidate, setTypeSafeCandidate, "TypeSafe credential")}</>}
      {category === "Voice" && <><h2>Voice</h2><p className="settings-muted">Install status and download progress come from Sani’s local voice engine. Changing models is unavailable while a voice capture is active.</p><div className="voice-model-grid">{voiceModels.map((model) => { const copy = voiceLabel(model.id); const busy = voiceBusy === model.id; const progress = voiceProgress[model.id]; return <article className="voice-model-card" key={model.id}><div><strong>{copy.name}</strong><span>{model.active ? "Active" : model.installed ? "Installed" : "Not installed"}</span></div><p>{copy.detail}</p><code>{model.id}</code>{progress !== undefined && !model.installed && <progress max="1" value={progress}>{Math.round(progress * 100)}%</progress>}<div className="settings-inline">{!model.installed && <button disabled={busy} onClick={() => void runVoiceAction(model.id, () => installVoice(model.id))}>{busy ? "Installing…" : "Install"}</button>}{model.installed && !model.active && <button disabled={busy} onClick={() => void runVoiceAction(model.id, () => useVoice(model.id))}>{busy ? "Switching…" : "Use"}</button>}{model.installed && !model.active && <button className="danger" disabled={busy} onClick={() => { if (window.confirm(`Remove ${copy.name} from Sani’s local voice cache?`)) void runVoiceAction(model.id, () => removeVoice(model.id)); }}>Remove</button>}</div></article>; })}</div>{voiceModels.length === 0 && <div className="settings-card"><strong>Voice engine unavailable</strong><p>Install or repair Sani’s local voice component before managing models.</p></div>}</>}
      {category === "Microphone" && <><h2>Microphone</h2><label>Input device<select value={snapshot.mic_device} onChange={(e) => void saveGeneral({ mic_device: e.target.value })}><option value="">System default</option>{microphones.map((mic) => <option key={mic} value={mic}>{mic}</option>)}</select></label><p className="settings-muted">Permission: {snapshot.microphone_permission}</p><button onClick={() => void invoke("open_mic_settings")}>Open macOS Microphone settings</button></>}
      {category === "Computer Control" && <><h2>Computer Control</h2><div className="settings-card"><strong>{control?.status.replaceAll("_", " ") ?? "Checking…"}</strong><p>{control?.message ?? "Checking current permissions and runtime…"}</p><p className="settings-muted">Accessibility: {control?.accessibility ?? snapshot.accessibility_permission} · Screen Recording: {control?.screen_recording ?? snapshot.screen_recording_permission} · Runtime: {control?.runtime ?? "checking"}</p><div className="settings-inline"><button onClick={() => { void invoke("request_accessibility").then(refreshControl); }}>Request Accessibility</button><button onClick={() => { void invoke("request_screen_recording").then(refreshControl); }}>Request Screen Recording</button><button onClick={refreshControl}>Refresh</button>{control?.restart_required && <button onClick={() => void invoke("restart_app")}>Restart Sani</button>}</div></div></>}
      {category === "Appearance" && <><h2>Appearance</h2><p>Overlay layout remains a separate, safe editor.</p><button onClick={onOpenLayout}>Open Layout Editor</button></>}
      {category === "Shortcuts" && <><h2>Shortcuts</h2><label>Global shortcut<input defaultValue={snapshot.hotkey} onBlur={(e) => void saveGeneral({ hotkey: e.target.value })}/></label></>}
      {category === "Startup" && <><h2>Startup</h2><label className="settings-toggle"><input type="checkbox" checked={snapshot.launch_at_login} onChange={(e) => void saveGeneral({ launch_at_login: e.target.checked })}/> Launch Sani at login</label></>}
      {category === "Storage" && <><h2>Storage</h2><div className="settings-card"><strong>Local Sani data</strong><code>{snapshot.storage_path}</code><p>Chats remain until you explicitly delete a conversation. Technical execution details are separate and never include keys, audio, screenshots, or clipboard data.</p><label>Keep technical details<select value={snapshot.technical_retention_days} onChange={(e) => void setTechnicalRetention(Number(e.target.value) as 7 | 14 | 30).then((count) => setStorageNotice(`Saved retention and removed ${count} expired technical records.`))}><option value={7}>7 days</option><option value={14}>14 days</option><option value={30}>30 days</option></select></label><button className="danger" onClick={() => { if (window.confirm("Clear all local technical execution details? Conversations and messages will remain.")) void clearRunActivity().then((count) => setStorageNotice(`Cleared ${count} technical records. Conversations were not changed.`)); }}>Clear technical details</button>{storageNotice && <p className="settings-muted">{storageNotice}</p>}</div></>}
    </div></div>
  </section>;
}
