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

const VEO_DEFAULT = { openrouter: "~typesafe/jev-latest", typesafe: "jev-latest" };

export default function ApiConfigurationStep({ onContinue }: { onBack: () => void; onContinue: () => void }) {
  const [orKey, setOrKey] = useState("");
  const [orState, setOrState] = useState<ValState>("idle");
  const [hasOr, setHasOr] = useState(false);

  const [models, setModels] = useState<ModelOption[]>([]);
  const [reasoningModel, setReasoningModel] = useState("");

  const [veloProvider, setVeloProvider] = useState<"openrouter" | "typesafe">("openrouter");
  const [veloModel, setVeloModel] = useState(VEO_DEFAULT.openrouter);

  const [tsKey, setTsKey] = useState("");
  const [tsState, setTsState] = useState<ValState>("idle");
  const [hasTs, setHasTs] = useState(false);

  useEffect(() => {
    void (async () => {
      const cfg = await getAiConfig();
      setHasOr(cfg.has_openrouter);
      setHasTs(cfg.has_typesafe);
      setOrState(cfg.has_openrouter ? "connected" : "idle");
      setTsState(cfg.has_typesafe ? "connected" : "idle");
      setReasoningModel(cfg.reasoning_model || "");
      if (cfg.velo_provider === "typesafe" || cfg.velo_provider === "openrouter") {
        setVeloProvider(cfg.velo_provider);
      }
      if (cfg.velo_model) setVeloModel(cfg.velo_model);
      const list = await listOpenrouterModels(null);
      setModels(list);
      if (!cfg.reasoning_model && list.length) {
        const claude = list.find((m) => m.id.includes("claude")) ?? list.find((m) => m.id.includes("openai")) ?? list[0];
        setReasoningModel(claude.id);
      }
    })();
  }, []);

  const checkKey = async (provider: "openrouter" | "typesafe", key: string) => {
    const set = provider === "openrouter" ? setOrState : setTsState;
    if (!key.trim()) {
      set("idle");
      return;
    }
    set("checking");
    const res = await validateProviderKey(provider, key);
    set(res.status);
    if (res.status === "connected") {
      const out = await storeProviderKey(provider, key);
      if (provider === "openrouter") setHasOr(out.has_openrouter);
      else setHasTs(out.has_typesafe);
    }
  };

  const changeVeloProvider = (p: "openrouter" | "typesafe") => {
    setVeloProvider(p);
    setVeloModel(VEO_DEFAULT[p]);
  };

  const proceed = async () => {
    // Persist non-secret provider/model choices. Keys were already stored to the
    // OS credential store when they validated connected — never here.
    await saveAiConfig({
      reasoning_provider: "openrouter",
      reasoning_model: reasoningModel.trim() || "openrouter/auto",
      velo_provider: veloProvider,
      velo_model: veloModel.trim() || VEO_DEFAULT[veloProvider],
    });
    onContinue();
  };

  const veloUsingOr = veloProvider === "openrouter";

  return (
    <div>
      <h1 className="onb-h1">Connect your AI</h1>
      <p className="onb-sub">
        Choose the models Sani should use. Your credentials stay securely on
        this Mac.
      </p>

      {/* Reasoning model */}
      <section className="onb-section">
        <div className="onb-section-head">Reasoning model</div>
        <div className="card">
          <div className="card-head">
            <div>
              <div className="card-title">OpenRouter</div>
              <div className="card-desc">Powers Sani&apos;s thinking and answers.</div>
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
                onBlur={() => orKey.trim() && void checkKey("openrouter", orKey)}
              />
              <StatusAffix state={orState} />
            </div>
            {orKey.trim() && orState !== "checking" ? (
              <Button small variant="secondary" onClick={() => void checkKey("openrouter", orKey)}>
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

      {/* Quick computer control (Velo / JEV) */}
      <section className="onb-section">
        <div className="onb-section-head">Quick computer control</div>
        <p className="onb-help" style={{ marginTop: -2 }}>
          Used for fast decisions while Sani operates your computer.
        </p>
        <div className="card">
          <div className="card-head">
            <div className="card-title">Provider</div>
            <div className="segmented" role="group" aria-label="Quick control provider">
              <button
                type="button"
                aria-pressed={veloUsingOr}
                onClick={() => changeVeloProvider("openrouter")}
              >
                OpenRouter
              </button>
              <button
                type="button"
                aria-pressed={!veloUsingOr}
                onClick={() => changeVeloProvider("typesafe")}
              >
                TypeSafe
              </button>
            </div>
          </div>

          {veloUsingOr ? (
            <div style={{ marginTop: 14 }}>
              {hasOr || orState === "connected" ? (
                <span className="pill">
                  <Check width={12} height={12} /> Using your OpenRouter connection
                </span>
              ) : (
                <p className="onb-help">Add your OpenRouter key above to reuse it here.</p>
              )}
            </div>
          ) : (
            <div className="field" style={{ marginTop: 14 }}>
              <label className="field-label" htmlFor="ts-key">
                TypeSafe API key
              </label>
              <div className="input-affix">
                <input
                  id="ts-key"
                  className="input"
                  type="password"
                  autoComplete="off"
                  spellCheck={false}
                  placeholder={hasTs ? "Saved on this Mac ✓" : "TypeSafe key"}
                  value={tsKey}
                  onChange={(e) => {
                    setTsKey(e.target.value);
                    if (tsState === "connected") setTsState("idle");
                  }}
                  onBlur={() => tsKey.trim() && void checkKey("typesafe", tsKey)}
                />
                <StatusAffix state={tsState} />
              </div>
            </div>
          )}

          <div className="field">
            <label className="field-label" htmlFor="velo-model">
              Model
            </label>
            <input
              id="velo-model"
              className="input"
              value={veloModel}
              onChange={(e) => setVeloModel(e.target.value)}
              spellCheck={false}
              autoComplete="off"
            />
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
