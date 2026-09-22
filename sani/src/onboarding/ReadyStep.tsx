import { Button } from "./components/ui";
import { Check } from "./components/icons";

export default function ReadyStep({
  onStart,
  onSettings,
}: {
  onStart: () => void;
  onSettings: () => void;
}) {
  return (
    <div className="onb-shell is-hero">
      <div className="hero-block" style={{ maxWidth: 460 }}>
        <div className="ready-mark">
          <Check width={26} height={26} />
        </div>
        <h1 className="onb-h1">Sani is ready.</h1>
        <p className="onb-sub" style={{ fontSize: 15 }}>
          Use your voice or type whenever you need something.
        </p>
        <div style={{ display: "flex", alignItems: "center", gap: 14, marginTop: 30 }}>
          <Button className="primary" onClick={onStart} autoFocus>
            Start using Sani
          </Button>
          <Button variant="secondary" onClick={onSettings}>
            Open Settings
          </Button>
        </div>
        <p className="onb-help" style={{ marginTop: 26 }}>
          Press <kbd>⌥</kbd> <kbd>Space</kbd> anytime to talk to Sani.
        </p>
      </div>
    </div>
  );
}
