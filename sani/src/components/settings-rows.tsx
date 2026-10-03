import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

/** Settings are compact rows: label left, current state right, one action. */
export function SettingsGroup({ title, children }: { title?: string; children: ReactNode }) {
  return (
    <section className="mb-7">
      {title ? <h2 className="mb-1.5 text-sm font-semibold text-foreground">{title}</h2> : null}
      <div className="divide-y divide-border rounded-xl border border-border bg-card">{children}</div>
    </section>
  );
}

export function SettingsRow({
  label,
  state,
  children,
  stack = false,
}: {
  label: string;
  /** Current state, reported quietly under the label (never an explanation). */
  state?: ReactNode;
  /** The single control on the right. */
  children?: ReactNode;
  /** Put the control under the label (inputs that need width). */
  stack?: boolean;
}) {
  return (
    <div
      className={cn(
        "flex min-h-12 gap-3 px-3.5 py-2.5",
        stack ? "flex-col" : "items-center justify-between",
      )}
    >
      <div className="min-w-0">
        <div className="text-foreground">{label}</div>
        {state ? <div className="truncate text-xs text-muted-foreground">{state}</div> : null}
      </div>
      {children ? <div className={cn("flex shrink-0 items-center gap-2", stack && "w-full")}>{children}</div> : null}
    </div>
  );
}
