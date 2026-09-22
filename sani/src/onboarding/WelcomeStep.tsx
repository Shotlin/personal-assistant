import { Brand } from "./components/icons";
import { Button } from "./components/ui";

export default function WelcomeStep({
  onContinue,
  onAdvanced,
}: {
  onContinue: () => void;
  onAdvanced: () => void;
}) {
  return (
    <div className="onb-shell is-hero">
      <div className="hero-block" style={{ maxWidth: 460 }}>
        <Brand className="welcome-mark" />
        <h1 className="onb-h1">Welcome to Sani</h1>
        <p className="onb-sub" style={{ fontSize: 15.5 }}>
          Your personal AI assistant, running directly on your computer.
        </p>
        <p className="onb-help" style={{ marginTop: 14, maxWidth: 420 }}>
          Sani will prepare its local engine, voice system, memory, and computer
          controls automatically.
        </p>
        <div style={{ display: "flex", alignItems: "center", gap: 16, marginTop: 30 }}>
          <Button className="primary" onClick={onContinue} autoFocus>
            Continue Setup
          </Button>
          <Button variant="link" onClick={onAdvanced}>
            Advanced setup
          </Button>
        </div>
      </div>
    </div>
  );
}
