import { useEffect, useState } from "react";
import {
  getSettings,
  listMics,
  saveSettings,
  type SettingsShape,
} from "../lib/tauri";

interface SettingsDrawerProps {
  onClose: () => void;
}

export default function SettingsDrawer({ onClose }: SettingsDrawerProps) {
  const [settings, setSettings] = useState<SettingsShape | null>(null);
  const [mics, setMics] = useState<string[]>([]);
  const [hotkeyDraft, setHotkeyDraft] = useState("");
  const [capturing, setCapturing] = useState(false);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    void (async () => {
      const s = await getSettings();
      setSettings(s);
      setHotkeyDraft(s.hotkey);
      setMics(await listMics());
    })();
  }, []);

  const flashSaved = () => {
    setSaved(true);
    setTimeout(() => setSaved(false), 1200);
  };

  const patch = async (p: Parameters<typeof saveSettings>[0]) => {
    await saveSettings(p);
    setSettings(await getSettings());
    flashSaved();
  };

  // Hotkey capture: press the new combination once.
  useEffect(() => {
    if (!capturing) return;
    const handler = (e: KeyboardEvent) => {
      e.preventDefault();
      e.stopPropagation();
      const parts: string[] = [];
      if (e.metaKey) parts.push("Super");
      if (e.ctrlKey) parts.push("Control");
      if (e.altKey) parts.push("Alt");
      if (e.shiftKey) parts.push("Shift");
      if (e.key && !["Control", "Alt", "Shift", "Meta"].includes(e.key)) {
        parts.push(e.key.length === 1 ? e.key.toUpperCase() : e.key);
      }
      if (parts.length >= 2) {
        const combo = parts.join("+");
        setHotkeyDraft(combo);
        void patch({ hotkey: combo });
        setCapturing(false);
      }
    };
    window.addEventListener("keydown", handler, true);
    return () => window.removeEventListener("keydown", handler, true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [capturing]);

  if (!settings) return null;

  return (
    <div className="drawer">
      <div className="drawer-header">
        <span>Settings</span>
        <button className="icon-btn" onClick={onClose} title="Close">
          <svg viewBox="0 0 24 24" width="15" height="15" stroke="currentColor" strokeWidth="2" fill="none" strokeLinecap="round">
            <path d="M6 6l12 12M18 6L6 18" />
          </svg>
        </button>
      </div>
      <div className="drawer-body settings-body">
        {saved && <div className="saved-flash">Saved</div>}

        <div className="settings-group">General</div>
        <label className="settings-row">
          <span>Launch Sani at login</span>
          <input
            type="checkbox"
            checked={settings.launch_at_login}
            onChange={(e) => void patch({ launch_at_login: e.target.checked })}
          />
        </label>
        <label className="settings-row">
          <span>Theme</span>
          <select
            value={settings.theme}
            onChange={(e) => void patch({ theme: e.target.value })}
          >
            <option value="dark">Dark</option>
            <option value="light">Light</option>
          </select>
        </label>

        <div className="settings-group">Voice</div>
        <label className="settings-row">
          <span>Microphone</span>
          <select
            value={settings.mic_device}
            onChange={(e) => void patch({ mic_device: e.target.value })}
          >
            <option value="">Default</option>
            {mics.map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
          </select>
        </label>
        <div className="settings-row">
          <span>Voice model</span>
          <span className={`voice-status ${settings.stt_ready ? "ok" : "wait"}`}>
            {settings.stt_ready
              ? `Ready · ${settings.stt_model.replace(/-en$/, " English")}`
              : "Preparing voice model…"}
          </span>
        </div>

        <div className="settings-group">Shortcut</div>
        <div className="settings-row">
          <span>Global shortcut</span>
          <button
            className={`mini-btn ${capturing ? "capturing" : ""}`}
            onClick={() => setCapturing(true)}
          >
            {capturing ? "Press keys…" : hotkeyDraft}
          </button>
        </div>
      </div>
    </div>
  );
}
