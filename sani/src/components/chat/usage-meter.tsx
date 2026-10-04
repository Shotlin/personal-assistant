import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import type { ClaudeCodeStatus, DeepContext, ZCodeStatus } from "@/lib/tauri";

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

/** Tokens left on the ZCode model in use, summed over the plans that include it, as last read. */
export function zcodeChip(status: ZCodeStatus | null): Chip | null {
  const items = status?.balances?.items ?? [];
  const model = status?.cdp?.models?.current_model;
  if (!status?.enabled || !model) return null;
  const mine = items.filter((item) => item.model === model);
  const total = mine.reduce((sum, item) => sum + item.total, 0);
  if (mine.length === 0 || total <= 0) return null;
  const left = mine.reduce((sum, item) => sum + item.remaining, 0);
  const used = Math.round(100 * (1 - left / total));
  const short = left >= 1_000_000 ? `${+(left / 1_000_000).toFixed(1)}M` : `${Math.round(left / 1000)}K`;
  const read = status.balances?.as_of ? new Date(status.balances.as_of * 1000).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }) : "";
  return {
    key: "zcode-left",
    text: `${short} left`,
    percent: used,
    detail: `${left.toLocaleString("en-US")} of ${total.toLocaleString("en-US")} tokens left for ${model} today across ${mine.length === 1 ? "your plan" : `${mine.length} plans`}${read ? `, as read at ${read}` : ""}. It does not update until Sani reads ZCode again.`,
  };
}

function deepChip(deep: DeepContext | null): Chip | null {
  if (!deep || !deep.known || deep.percent === null || deep.percent === undefined) return null;
  const mark = deep.estimated ? "~" : "";
  const tokens = deep.tokens ? ` (${mark}${Math.round(deep.tokens / 1000)}k of ${Math.round((deep.window ?? 0) / 1000)}k tokens)` : "";
  return {
    key: "deep-context",
    text: `Context ${mark}${deep.percent}%`,
    percent: deep.percent,
    detail: `This Sani conversation is ${mark}${deep.percent}% full${tokens}. Near 90% it is best to start a new chat.`,
  };
}

/** A small round progress ring: filled share = how much is used. */
function Ring({ percent, tone }: { percent: number; tone: "sani" | "claude" }) {
  const size = 18;
  const stroke = 2.5;
  const radius = (size - stroke) / 2;
  const circumference = 2 * Math.PI * radius;
  const clamped = Math.max(0, Math.min(100, percent));
  const hot = clamped >= 90;
  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} aria-hidden="true" className="-rotate-90">
      <circle cx={size / 2} cy={size / 2} r={radius} fill="none" strokeWidth={stroke} className="stroke-slate-5" />
      <circle
        cx={size / 2}
        cy={size / 2}
        r={radius}
        fill="none"
        strokeWidth={stroke}
        strokeLinecap="round"
        strokeDasharray={circumference}
        strokeDashoffset={circumference * (1 - clamped / 100)}
        className={hot ? "stroke-amber-9" : tone === "sani" ? "stroke-slate-11" : "stroke-blue-10"}
      />
    </svg>
  );
}

function RingChip({
  chip,
  tone,
  showText,
  hint,
}: {
  chip: Chip;
  tone: "sani" | "claude";
  showText: boolean;
  hint: string;
}) {
  return (
    <Tooltip>
      <TooltipTrigger
        render={
          <span
            className="inline-flex h-7 items-center gap-1.5 rounded-full px-1.5 text-xs whitespace-nowrap text-muted-foreground tabular-nums"
            aria-label={`${hint}: ${chip.text}`}
          />
        }
      >
        <Ring percent={chip.percent} tone={tone} />
        {showText ? <span>{chip.text}</span> : null}
      </TooltipTrigger>
      <TooltipContent>
        {hint} — {chip.detail}
      </TooltipContent>
    </Tooltip>
  );
}

/**
 * Round gauges that sit beside the mic. Claude Code's 5-hour and weekly limits
 * come first and appear only when Claude Code reported them; Sani's own context
 * ring is last, next to the mic, and carries no label.
 */
export function UsageMeter({
  status,
  deep = null,
  zcode = null,
}: {
  status: ClaudeCodeStatus | null;
  deep?: DeepContext | null;
  zcode?: ZCodeStatus | null;
}) {
  const own = deepChip(deep);
  const zcodeLeft = zcodeChip(zcode);
  const claude = chipsFor(status).filter((chip) => chip.key !== "context");
  const claudeContext = chipsFor(status).find((chip) => chip.key === "context");
  if (!own && claude.length === 0 && !claudeContext && !zcodeLeft) return null;
  return (
    <div className="flex items-center gap-0.5">
      {claudeContext ? (
        <RingChip chip={{ ...claudeContext, text: `Ctx ${claudeContext.percent}%` }} tone="claude" showText hint="Claude Code context" />
      ) : null}
      {claude.map((chip) => (
        <RingChip key={chip.key} chip={chip} tone="claude" showText hint="Claude Code" />
      ))}
      {zcodeLeft ? <RingChip chip={zcodeLeft} tone="claude" showText hint="ZCode" /> : null}
      {own ? <RingChip chip={own} tone="sani" showText={false} hint="Sani context" /> : null}
    </div>
  );
}
