import { Gauge } from "lucide-react";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";
import type { ClaudeCodeStatus } from "@/lib/tauri";

const RATE_LABEL: Record<string, string> = {
  five_hour: "5h",
  seven_day: "Week",
  seven_day_opus: "Week",
};
const RATE_NAME: Record<string, string> = {
  five_hour: "5-hour window",
  seven_day: "weekly limit",
  seven_day_opus: "weekly limit",
};

function resetText(epochSeconds?: number | null): string {
  if (!epochSeconds) return "";
  const date = new Date(epochSeconds * 1000);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

interface Chip {
  key: string;
  text: string;
  percent: number;
  detail: string;
}

export function chipsFor(status: ClaudeCodeStatus | null): Chip[] {
  if (!status || !status.enabled || !status.claude.signed_in) return [];
  const chips: Chip[] = [];
  const context = status.usage.context_percent;
  if (context !== null && context !== undefined) {
    chips.push({
      key: "context",
      text: `Context ${context}%`,
      percent: context,
      detail: `This Claude Code conversation is ${context}% full. Near 90% it is best to start a fresh session.`,
    });
  }
  for (const [kind, rate] of Object.entries(status.usage.rates)) {
    if (rate.used_percent === null || rate.used_percent === undefined) continue;
    const percent = Math.round(rate.used_percent);
    const reset = resetText(rate.resets_at);
    chips.push({
      key: kind,
      text: `${RATE_LABEL[kind] ?? kind} ${percent}%`,
      percent,
      detail: `${percent}% of your ${RATE_NAME[kind] ?? kind} used${reset ? `, resets ${reset}` : ""}.`,
    });
  }
  return chips;
}

/**
 * Quiet usage facts from the user's own Claude Code, shown only when Claude
 * Code reported them. Neutral until a limit is close, then plain emphasis
 * (never alarm colours for a normal state).
 */
export function UsageMeter({ status }: { status: ClaudeCodeStatus | null }) {
  const chips = chipsFor(status);
  if (chips.length === 0) return null;
  return (
    <div className="flex items-center gap-1" aria-label="Claude Code usage">
      {chips.map((chip) => (
        <Tooltip key={chip.key}>
          <TooltipTrigger
            render={
              <span
                className={cn(
                  "inline-flex h-6 items-center gap-1 rounded-full px-2 text-xs whitespace-nowrap tabular-nums",
                  chip.percent >= 90
                    ? "bg-secondary font-medium text-foreground"
                    : "text-muted-foreground",
                )}
              />
            }
          >
            <Gauge className="size-3" aria-hidden="true" />
            {chip.text}
          </TooltipTrigger>
          <TooltipContent>{chip.detail}</TooltipContent>
        </Tooltip>
      ))}
    </div>
  );
}
