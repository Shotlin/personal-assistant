import { useState, type ButtonHTMLAttributes, type ReactNode } from "react";
import type { ComponentStatus } from "../types";
import { Check, Chevron, Cross, Dash } from "./icons";

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "secondary" | "link";
  small?: boolean;
};

export function Button({ variant = "primary", small, className = "", ...rest }: ButtonProps) {
  return (
    <button
      className={`onb-btn ${variant}${small ? " small" : ""} ${className}`}
      {...rest}
    />
  );
}

export function ProgressBar({ percent, label }: { percent: number; label: string }) {
  return (
    <div>
      <div className="progress-head">
        <span className="onb-h2">{label}</span>
        <span className="progress-pct">{percent}%</span>
      </div>
      <div
        className="progress-track"
        role="progressbar"
        aria-valuenow={percent}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label="Setup progress"
      >
        <div className="progress-fill" style={{ width: `${percent}%` }} />
      </div>
    </div>
  );
}

function StatusIcon({ status }: { status: ComponentStatus }) {
  switch (status) {
    case "running":
      return <span className="spinner" aria-hidden />;
    case "complete":
      return <Check className="step-icon complete" width={13} height={13} aria-hidden />;
    case "failed":
      return <Cross className="step-icon failed" width={12} height={12} aria-hidden />;
    case "skipped":
      return <Dash className="step-icon skipped" width={12} height={12} aria-hidden />;
    default:
      return <span className="step-icon pending" aria-hidden />;
  }
}

export function StepRow({
  friendly,
  status,
  error,
  note,
  onRetry,
}: {
  friendly: string;
  status: ComponentStatus;
  error?: string | null;
  note?: string;
  onRetry?: () => void;
}) {
  return (
    <li className="step-row" data-state={status}>
      <StatusIcon status={status} />
      <span className="step-label">
        {friendly}
        {status === "failed" && error ? <span className="step-note"> — {error}</span> : null}
        {status === "skipped" && note ? <span className="step-note"> — {note}</span> : null}
      </span>
      {status === "failed" && onRetry ? (
        <button className="step-retry" onClick={onRetry}>
          Retry
        </button>
      ) : null}
    </li>
  );
}

export function ShowDetails({ children, label = "Show details" }: { children: ReactNode; label?: string }) {
  const [open, setOpen] = useState(false);
  return (
    <div>
      <button className="details-toggle" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
        <Chevron width={13} height={13} style={{ transform: open ? "rotate(180deg)" : "none" }} />
        {open ? "Hide details" : label}
      </button>
      {open ? <div className="details-log">{children}</div> : null}
    </div>
  );
}

export function Switch({
  checked,
  onChange,
  label,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  label: string;
}) {
  return (
    <button
      className="switch"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      onClick={() => onChange(!checked)}
    />
  );
}

export function Segmented<T extends string>({
  value,
  options,
  onChange,
  label,
}: {
  value: T;
  options: { value: T; label: string }[];
  onChange: (v: T) => void;
  label: string;
}) {
  return (
    <div className="segmented" role="group" aria-label={label}>
      {options.map((o) => (
        <button
          key={o.value}
          aria-pressed={o.value === value}
          onClick={() => onChange(o.value)}
          type="button"
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}
