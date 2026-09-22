import { useCallback, useEffect, useMemo, useState } from "react";
import "../styles/tokens.css";
import "../styles/onboarding.css";
import {
  completeOnboarding,
  getPreferences,
  onSetupProgress,
  onSetupStage,
  runSetup,
  setOnboardingStage,
  setupState,
} from "./api";
import type { ComponentView, OnboardingStage, SetupProgressEvent } from "./types";
import { Brand } from "./components/icons";
import { Button } from "./components/ui";
import WelcomeStep from "./WelcomeStep";
import SetupProgressStep from "./SetupProgressStep";
import ApiConfigurationStep from "./ApiConfigurationStep";
import PermissionsStep from "./PermissionsStep";
import PreferencesStep from "./PreferencesStep";
import FinalCheckStep from "./FinalCheckStep";
import ReadyStep from "./ReadyStep";

const VALID_STAGES: OnboardingStage[] = [
  "welcome",
  "preparing",
  "config",
  "permissions",
  "preferences",
  "verify",
  "ready",
];

// Position of each step within the "Step n of 5" indicator (welcome/ready aside).
const STEP_INDEX: Partial<Record<OnboardingStage, number>> = {
  preparing: 1,
  config: 2,
  permissions: 3,
  preferences: 4,
  verify: 5,
};

type Theme = "system" | "dark" | "light";

export default function OnboardingApp() {
  const [stage, setStage] = useState<OnboardingStage>("welcome");
  const [theme, setTheme] = useState<Theme>("dark");
  const [components, setComponents] = useState<Record<string, ComponentView>>({});
  const [percent, setPercent] = useState(0);
  const [localComplete, setLocalComplete] = useState(false);
  const [caption, setCaption] = useState("Preparing Sani…");

  const go = useCallback((next: OnboardingStage) => {
    setStage(next);
    void setOnboardingStage(next);
  }, []);

  // Hydrate persisted state + theme once, then subscribe to real progress.
  useEffect(() => {
    let unlisten: Array<() => void> = [];
    void (async () => {
      const snap = await setupState();
      setComponents(Object.fromEntries(snap.components.map((c) => [c.step, c])));
      setPercent(snap.percent);
      setLocalComplete(snap.local_setup_complete);
      if (VALID_STAGES.includes(snap.current_stage as OnboardingStage)) {
        setStage(snap.current_stage as OnboardingStage);
      }
      const prefs = await getPreferences();
      if (prefs.theme === "light" || prefs.theme === "dark" || prefs.theme === "system") {
        setTheme(prefs.theme);
      }
    })();
    void (async () => {
      unlisten.push(
        await onSetupProgress((e: SetupProgressEvent) => {
          setComponents((prev) => ({
            ...prev,
            [e.step]: { step: e.step, friendly: e.friendly, status: e.status, detail: e.detail, error: null },
          }));
          setPercent(e.percent);
          if (e.status === "running") setCaption(e.message);
        }),
        await onSetupStage((s) => {
          if (s === "local_complete") setLocalComplete(true);
        }),
      );
    })();
    return () => unlisten.forEach((u) => u());
  }, []);

  // The auto-setup run is idempotent; entering (or resuming at) this stage kicks
  // it. React never fakes progress — it only reflects what Rust emits.
  useEffect(() => {
    if (stage === "preparing" && !localComplete) void runSetup();
  }, [stage, localComplete]);

  const orderedComponents = useMemo(() => {
    const order = ["app_data", "database", "core", "stt_runtime", "stt_model", "skills", "cua"];
    return order.map((k) => components[k]).filter(Boolean) as ComponentView[];
  }, [components]);

  const start = () => {
    void completeOnboarding().catch(() => {});
  };

  const stepNo = STEP_INDEX[stage];

  // Full-bleed hero screens.
  if (stage === "welcome") {
    return (
      <Root theme={theme}>
        <WelcomeStep onContinue={() => go("preparing")} onAdvanced={() => go("config")} />
      </Root>
    );
  }
  if (stage === "ready") {
    return (
      <Root theme={theme}>
        <ReadyStep onStart={start} onSettings={() => go("config")} />
      </Root>
    );
  }

  // Framed steps share the topbar + footer.
  return (
    <Root theme={theme}>
      <div className="onb-shell">
        <header className="onb-topbar">
          <span className="onb-brand">
            <Brand className="mark" />
            Sani
          </span>
          {stepNo ? <span className="onb-step-count">Step {stepNo} of 5</span> : null}
        </header>

        <div className="onb-body">
          {stage === "preparing" && (
            <SetupProgressStep
              components={orderedComponents}
              percent={percent}
              complete={localComplete}
              caption={caption}
              onContinue={() => go("config")}
            />
          )}
          {stage === "config" && (
            <ApiConfigurationStep onBack={() => go("preparing")} onContinue={() => go("permissions")} />
          )}
          {stage === "permissions" && <PermissionsStep onContinue={() => go("preferences")} />}
          {stage === "preferences" && (
            <PreferencesStep theme={theme} onThemeChange={setTheme} onContinue={() => go("verify")} />
          )}
          {stage === "verify" && <FinalCheckStep onContinue={() => go("ready")} />}
        </div>

        {stage === "config" && (
          <footer className="onb-foot">
            <Button variant="link" onClick={() => go("preparing")}>
              Back
            </Button>
          </footer>
        )}
      </div>
    </Root>
  );
}

function Root({ theme, children }: { theme: Theme; children: React.ReactNode }) {
  const resolved =
    theme === "light" || (theme === "system" && window.matchMedia("(prefers-color-scheme: light)").matches)
      ? "light"
      : "dark";
  return (
    <div className="onb-root fade-in" data-theme={resolved}>
      {children}
    </div>
  );
}
