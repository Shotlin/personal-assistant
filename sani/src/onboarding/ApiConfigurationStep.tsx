import { useEffect, useState } from "react";
import {
  getAiConfig,
  listOpenrouterModels,
  saveAiConfig,
  storeProviderKey,
  validateProviderKey,
} from "./api";
import type { ModelOption } from "./types";
import { Button } from "./components/ui";
import { Check, Cross } from "./components/icons";
import SearchableSelect from "./components/SearchableSelect";

type ValState = "idle" | "checking" | "connected" | "invalid" | "offline";

function statusText(s: ValState): string {
  switch (s) {
    case "checking":
      return "Checking…";
    case "connected":
      return "Connected";
    case "invalid":
      return "Invalid key";
    case "offline":
      return "Network unavailable";
    default:
      return "";
  }
}

function StatusAffix({ state }: { state: ValState }) {
  if (state === "idle") return null;
  const cls = state === "connected" ? "ok" : state === "invalid" ? "err" : "wait";
  return (
    <span className={`affix ${cls}`}>
      {state === "connected" ? (
        <Check width={12} height={12} />
      ) : state === "invalid" ? (
        <Cross width={11} height={11} />
      ) : null}
      {statusText(state)}
    </span>
  );
}

export default function ApiConfigurationStep({ onContinue }: { onBack: () => void; onContinue: () => void }) {
  const [orKey, setOrKey] = useState("");
  const [orState, setOrState] = useState<ValState>("idle");
  const [hasOr, setHasOr] = useState(false);

  const [models, setModels] = useState<ModelOption[]>([]);
  const [reasoningModel, setReasoningModel] = useState("");

  useEffect(() => {
    void (async () => {
      const cfg = await getAiConfig();
      setHasOr(cfg.has_openrouter);
      setOrState(cfg.has_openrouter ? "connected" : "idle");
      setReasoningModel(cfg.reasoning_model || "");
      const list = await listOpenrouterModels(null);
      setModels(list);
      if (!cfg.reasoning_model && list.length) {
        const claude = list.find((m) => m.id.includes("claude")) ?? list.find((m) => m.id.includes("openai")) ?? list[0];
        setReasoningModel(claude.id);
      }
    })();
  }, []);

  const checkKey = async (key: string) => {
    if (!key.trim()) {
      setOrState("idle");
      return;
    }
    setOrState("checking");
    const res = await validateProviderKey("openrouter", key);
    setOrState(res.status);
    if (res.status === "connected") {
      const out = await storeProviderKey("openrouter", key);
      setHasOr(out.has_openrouter);
    }
  };

  const proceed = async () => {
    // Persist non-secret provider/model choices. The key was already stored to
    // the OS credential store when it validated connected — never here.
    await saveAiConfig({
      reasoning_provider: "openrouter",
      reasoning_model: reasoningModel.trim() || "openrouter/auto",
    });
    onContinue();
  };

  return (
    <div>
      <h1 className="onb-h1">Connect your AI</h1>
      <p className="onb-sub">
        Choose the model Sani should use. Your credential stays securely on
        this Mac.
      </p>

      {/* Reasoning model */}
      <section className="onb-section">
        <div className="onb-section-head">Reasoning model</div>
        <p className="onb-help" style={{ marginTop: -2 }}>
          Sani thinks, answers and operates your computer with this one model.
        </p>
        <div className="card">
          <div className="card-head">
            <div>
              <div className="card-title">OpenRouter</div>
              <div className="card-desc">Powers Sani&apos;s thinking, answers and computer control.</div>
            </div>
            <span style={{ fontSize: 12.5, color: "var(--text-faint)" }}>Provider</span>
          </div>

          <div className="field">
            <label className="field-label" htmlFor="or-key">
              API key
            </label>
            <div className="input-affix">
              <input
                id="or-key"
                className="input"
                type="password"
                autoComplete="off"
                spellCheck={false}
                placeholder={hasOr ? "Saved on this Mac ✓ — enter a new key to change" : "sk-or-…"}
                value={orKey}
                onChange={(e) => {
                  setOrKey(e.target.value);
                  if (orState === "connected") setOrState("idle");
                }}
                onBlur={() => orKey.trim() && void checkKey(orKey)}
              />
              <StatusAffix state={orState} />
            </div>
            {orKey.trim() && orState !== "checking" ? (
              <Button small variant="secondary" onClick={() => void checkKey(orKey)}>
                Check
              </Button>
            ) : null}
          </div>

          <div className="field">
            <label className="field-label" htmlFor="or-model">
              Model
            </label>
            <div id="or-model">
              <SearchableSelect
                value={reasoningModel}
                onChange={setReasoningModel}
                options={models}
                placeholder="Search models or paste a model ID"
              />
            </div>
          </div>
        </div>
      </section>

      <div className="onb-section">
        <Button className="primary" onClick={() => void proceed()} disabled={orState === "checking"}>
          Continue
        </Button>
      </div>
    </div>
  );
}
