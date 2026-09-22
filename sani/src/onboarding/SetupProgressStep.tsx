import { retrySetupComponent } from "./api";
import type { ComponentView } from "./types";
import { Button, ProgressBar, ShowDetails, StepRow } from "./components/ui";

export default function SetupProgressStep({
  components,
  percent,
  complete,
  caption,
  onContinue,
}: {
  components: ComponentView[];
  percent: number;
  complete: boolean;
  caption: string;
  onContinue: () => void;
}) {
  const failed = components.filter((c) => c.status === "failed");
  const anyRunning = components.some((c) => c.status === "running");
  const title = complete ? "Sani is ready locally" : "Preparing Sani";

  return (
    <div>
      <ProgressBar percent={complete ? 100 : percent} label={title} />
      <p className="onb-help" style={{ marginTop: 8 }}>
        {complete
          ? "Everything is configured locally on this Mac."
          : "This only takes a moment. Everything is being configured locally."}
      </p>

      <ul className="step-list">
        {components.map((c) => (
          <StepRow
            key={c.step}
            friendly={c.friendly}
            status={c.status}
            error={c.error}
            note={c.detail}
            onRetry={c.status === "failed" ? () => void retrySetupComponent(c.step) : undefined}
          />
        ))}
      </ul>

      {failed.length > 0 && !anyRunning ? (
        <p className="onb-help" style={{ color: "var(--error)", marginTop: 16 }}>
          We couldn&apos;t finish setting up Sani. Retry the step above, or open
          details to see what happened.
        </p>
      ) : (
        <div className="progress-caption">
          {!complete && anyRunning ? <span className="spinner" aria-hidden /> : null}
          <span>{complete ? "Local setup complete" : caption}</span>
          {!complete ? <span className="sub">This may take a few seconds.</span> : null}
        </div>
      )}

      <ShowDetails>
        {components.map((c) => (
          <div key={c.step}>
            {c.detail || c.error || c.friendly}{" "}
            <span style={{ opacity: 0.6 }}>({c.step} · {c.status})</span>
          </div>
        ))}
      </ShowDetails>

      <div style={{ marginTop: 22 }}>
        <Button className="primary" disabled={!complete} onClick={onContinue}>
          Continue
        </Button>
      </div>
    </div>
  );
}
