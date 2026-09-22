import { useEffect, useState } from "react";
import { getPreferences, listMics, savePreferences } from "./api";
import { Button, Segmented, Switch } from "./components/ui";

type Theme = "system" | "dark" | "light";

const SHORTCUTS: { value: string; label: string }[] = [
  { value: "Alt+Space", label: "⌥ Space" },
  { value: "Super+Space", label: "⌘ Space" },
  { value: "Super+Shift+Space", label: "⇧⌘ Space" },
  { value: "Ctrl+Alt+Space", label: "⌃⌥ Space" },
];

export default function PreferencesStep({
  theme,
  onThemeChange,
  onContinue,
}: {
  theme: Theme;
  onThemeChange: (t: Theme) => void;
  onContinue: () => void;
}) {
  const [launch, setLaunch] = useState(false);
  const [hotkey, setHotkey] = useState("Alt+Space");
  const [mic, setMic] = useState("");
  const [mics, setMics] = useState<string[]>([]);

  useEffect(() => {
    void (async () => {
      const p = await getPreferences();
      setLaunch(p.launch_at_login);
      setHotkey(p.hotkey || "Alt+Space");
      setMic(p.mic_device || "");
      setMics(await listMics());
    })();
  }, []);

  const setTheme = (t: Theme) => {
    onThemeChange(t);
    void savePreferences({ theme: t });
  };

  return (
    <div>
      <h1 className="onb-h1">Make Sani yours</h1>
      <p className="onb-sub">A few quick preferences. Everything else stays in Settings.</p>

      <div className="onb-section">
        <div className="pref-row">
          <div className="pref-text">
            <div className="pref-title">Launch Sani at login</div>
            <div className="pref-sub">Sani is ready the moment you open your Mac.</div>
          </div>
          <Switch
            checked={launch}
            label="Launch Sani at login"
            onChange={(v) => {
              setLaunch(v);
              void savePreferences({ launch_at_login: v });
            }}
          />
        </div>

        <div className="pref-row">
          <div className="pref-text">
            <div className="pref-title">Voice shortcut</div>
            <div className="pref-sub">Press it anytime to start talking.</div>
          </div>
          <select
            className="select"
            style={{ width: 180 }}
            value={hotkey}
            onChange={(e) => {
              setHotkey(e.target.value);
              void savePreferences({ hotkey: e.target.value });
            }}
          >
            {SHORTCUTS.map((s) => (
              <option key={s.value} value={s.value}>
                {s.label}
              </option>
            ))}
          </select>
        </div>

        <div className="pref-row">
          <div className="pref-text">
            <div className="pref-title">Microphone</div>
            <div className="pref-sub">The input Sani listens with.</div>
          </div>
          <select
            className="select"
            style={{ width: 220 }}
            value={mic}
            onChange={(e) => {
              setMic(e.target.value);
              void savePreferences({ mic_device: e.target.value });
            }}
          >
            <option value="">System default</option>
            {mics.map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
          </select>
        </div>

        <div className="pref-row">
          <div className="pref-text">
            <div className="pref-title">Theme</div>
            <div className="pref-sub">Match the system or pick your own.</div>
          </div>
          <Segmented<Theme>
            label="Theme"
            value={theme}
            onChange={setTheme}
            options={[
              { value: "system", label: "System" },
              { value: "dark", label: "Dark" },
              { value: "light", label: "Light" },
            ]}
          />
        </div>
      </div>

      <div className="onb-section">
        <Button className="primary" onClick={onContinue}>
          Continue
        </Button>
      </div>
    </div>
  );
}
