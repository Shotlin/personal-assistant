import { useCallback, useEffect, useState } from "react";
import { finalHealth } from "./api";
import type { FinalHealth } from "./types";
import { Button } from "./components/ui";
import { Check, Cross } from "./components/icons";

export default function FinalCheckStep({ onContinue }: { onContinue: () => void }) {
  const [health, setHealth] = useState<FinalHealth | null>(null);

  const run = useCallback(async () => setHealth(await finalHealth()), []);

  useEffect(() => {
    void run();
    const id = window.setTimeout(run, 900);
    return () => window.clearTimeout(id);
  }, [run]);

  const canContinue = !health || health.local_healthy;

  return (
    <div>
      <h1 className="onb-h1">Final check</h1>
      <p className="onb-sub">Sani is making sure everything works before it opens.</p>

      <ul className="check-list">
        {(health?.checks ?? []).map((c) => (
          <li className="step-row" key={c.label} data-state={c.ok ? "complete" : "failed"}>
            <span className={`step-icon ${c.ok ? "complete" : "failed"}`}>
              {c.ok ? <Check width={13} height={13} /> : <Cross width={12} height={12} />}
            </span>
            <span className="step-label">
              {c.label}
              {c.note ? <span className="step-note"> — {c.note}</span> : null}
            </span>
          </li>
        ))}
      </ul>

      {health?.network_only_issue ? (
        <p className="onb-help" style={{ marginTop: 16 }}>
          Sani is installed and ready locally. AI requests will need an internet
          connection when you first use a model.
        </p>
      ) : null}

      {health && !health.local_healthy ? (
        <p className="onb-help" style={{ marginTop: 16, color: "var(--error)" }}>
          Something local isn&apos;t ready yet. Go back and finish setup, then
          run this check again.
        </p>
      ) : null}

      <div className="onb-section" style={{ display: "flex", gap: 12, alignItems: "center" }}>
        <Button className="primary" onClick={onContinue} disabled={!canContinue}>
          Continue
        </Button>
        <Button variant="link" onClick={() => void run()}>
          Re-run check
        </Button>
      </div>
    </div>
  );
}
