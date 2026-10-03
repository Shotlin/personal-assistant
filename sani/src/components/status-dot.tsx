import { DotMatrixLoader } from "@/components/ui/dot-matrix-loader";
import { cn } from "@/lib/utils";
import type { HealthTone } from "@/chat/claude-health";

/** Green means on and working, red means a problem, grey means off by choice. */
export function StatusDot({ tone, className, label }: { tone: HealthTone; className?: string; label?: string }) {
  if (tone === "busy") return <DotMatrixLoader label={label ?? "Working"} className={className} />;
  return (
    <span
      role="img"
      aria-label={label ?? { green: "On", red: "Problem", grey: "Off" }[tone]}
      className={cn(
        "inline-block size-2.5 shrink-0 rounded-full",
        tone === "green" && "bg-green-9 ring-4 ring-green-a4",
        tone === "red" && "bg-red-9 ring-4 ring-red-a4",
        tone === "grey" && "bg-slate-8 ring-4 ring-slate-a3",
        className,
      )}
    />
  );
}
