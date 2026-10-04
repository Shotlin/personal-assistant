import { useState } from "react";
import { Check, Lock } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { SettingsGroup, SettingsRow } from "@/components/settings-rows";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { StatusDot } from "@/components/status-dot";
import { describeChange, zcodeHealth } from "@/chat/claude-health";
import { ChoiceMenu } from "@/components/ui/choice-menu";
import { useZCode } from "@/chat/use-zcode";
import { openZcode, type ZCodeBalance, type ZCodePlan, type ZCodeWindowRead } from "@/lib/tauri";
import { ProjectFolders, RunLimitRow } from "./ProjectFolders";
import { useSettings } from "./SettingsContext";

/** 1000000 -> "1M", 200000 -> "200K". */
function tokens(value: number | null): string {
  if (!value) return "";
  if (value >= 1_000_000) return `${+(value / 1_000_000).toFixed(1)}M`;
  if (value >= 1_000) return `${Math.round(value / 1_000)}K`;
  return String(value);
}

function readAt(epochSeconds: number): string {
  const date = new Date(epochSeconds * 1000);
  if (Number.isNaN(date.getTime())) return "";
  const time = date.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  return date.toDateString() === new Date().toDateString() ? `today ${time}` : `${date.toLocaleDateString()} ${time}`;
}

function full(value: number): string {
  return value.toLocaleString("en-US");
}

function PlanPicker({
  plans,
  selected,
  inUse,
  onPick,
}: {
  plans: ZCodePlan[];
  selected: { provider?: string; model?: string };
  inUse: { provider: string; model: string } | null;
  onPick: (provider: string, model: string) => void;
}) {
  if (plans.length === 0) {
    return <SettingsRow label="No plans found" state="Install the ZCode app so Sani can list its plans" />;
  }
  const families = [...new Set(plans.map((plan) => plan.family_name))];
  return (
    <>
      {families.map((family) => (
        <SettingsGroup key={family} title={family}>
          {plans
            .filter((plan) => plan.family_name === family)
            .map((plan) => {
              return (
              <div key={plan.id} className="px-4 py-3" role="radiogroup" aria-label={plan.name}>
                <div className="mb-2 flex items-baseline justify-between gap-3">
                  <span className="font-medium text-foreground">{plan.name}</span>
                </div>
                <div className="flex flex-col gap-1.5">
                  {plan.models.map((model) => {
                    const chosen = selected.provider === plan.id && selected.model === model.id;
                    const active = inUse?.provider === plan.id && inUse.model === model.id;
                    return (
                      <button
                        key={model.id}
                        type="button"
                        role="radio"
                        aria-checked={chosen}
                        onClick={() => onPick(plan.id, model.id)}
                        className={`flex items-center justify-between gap-3 rounded-lg border px-3 py-2 text-left text-sm transition-colors ${
                          chosen ? "border-blue-9 bg-blue-a3" : "border-border hover:bg-secondary"
                        }`}
                      >
                        <span className="flex min-w-0 items-center gap-2">
                          <span
                            className={`flex size-4 shrink-0 items-center justify-center rounded-full border ${
                              chosen ? "border-blue-9 bg-blue-9 text-white" : "border-border"
                            }`}
                          >
                            {chosen ? <Check className="size-3" aria-hidden="true" /> : null}
                          </span>
                          <span className="truncate font-medium text-foreground">{model.id}</span>
                          {active ? (
                            <span className="shrink-0 rounded-full bg-secondary px-2 py-0.5 text-[11px] font-medium text-foreground">
                              ZCode's default
                            </span>
                          ) : null}
                        </span>
                        <span className="shrink-0 text-right text-xs tabular-nums text-muted-foreground">
                          {tokens(model.context_window)} context
                          {model.max_output ? ` · ${tokens(model.max_output)} reply` : ""}
                        </span>
                      </button>
                    );
                  })}
                </div>
              </div>
              );
            })}
        </SettingsGroup>
      ))}
    </>
  );
}

function ZCodeAccount({ zcode }: { zcode: ReturnType<typeof useZCode> }) {
  const { status, loading, error, act } = zcode;
  const [confirmRead, setConfirmRead] = useState(false);
  const health = zcodeHealth(status, loading);
  const job = status?.cdp?.job;
  const reading = job?.state === "reading";
  const installed = Boolean(status?.zcode?.installed);
  const account = status?.account;
  const readTime = Math.max(account?.as_of ?? 0, status?.cdp?.contract?.as_of ?? 0);
  return (
    <div className="mb-6 rounded-xl border border-border bg-card px-4 py-3.5">
      <div className="flex items-center gap-3">
        <StatusDot tone={health.tone} label={`ZCode: ${health.title}`} />
        <div className="min-w-0 flex-1">
          <div className="font-medium text-foreground">{health.title}</div>
          <div className="text-xs text-muted-foreground">{health.detail}</div>
          <div className="mt-1.5 truncate text-sm text-foreground">
            {account?.name || account?.email ? (
              <>
                <span className="font-medium">{account.name || account.email}</span>
                {readTime ? <span className="text-muted-foreground"> · read {readAt(readTime)}</span> : null}
              </>
            ) : (
              <span className="text-muted-foreground">Account not read yet</span>
            )}
          </div>
        </div>
        <div className="flex shrink-0 gap-2">
          <Button size="sm" variant="outline" disabled={!installed} onClick={() => void openZcode().catch(() => undefined)}>
            Open ZCode
          </Button>
          <Button size="sm" disabled={!installed || reading} onClick={() => setConfirmRead(true)}>
            {reading ? "Reading…" : "Read now"}
          </Button>
        </div>
      </div>
      {job?.state === "refused" && job.message ? (
        <p className="mt-3 border-t border-border pt-3 text-sm text-destructive" role="alert">
          {job.message}
        </p>
      ) : null}
      {job?.state === "done" && job.message ? (
        <p className="mt-3 border-t border-border pt-3 text-xs text-muted-foreground">{job.message}</p>
      ) : null}
      {!installed && status ? (
        <p className="mt-3 border-t border-border pt-3 text-xs text-muted-foreground">
          Install the ZCode app (zcode.z.ai), open it and sign in there.
        </p>
      ) : (
        <p className="mt-3 border-t border-border pt-3 text-xs text-muted-foreground">
          Sign in or out inside the ZCode app itself. Sani only reads what ZCode shows and never types a password.
        </p>
      )}
      {error ? (
        <p className="mt-3 text-sm text-destructive" role="alert">
          {error}
        </p>
      ) : null}
      <AlertDialog open={confirmRead} onOpenChange={setConfirmRead}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Close and reopen ZCode?</AlertDialogTitle>
            <AlertDialogDescription>
              To read it, Sani closes the ZCode app and starts it again for about a minute with a private, local-only
              debug port, then reopens it normally and closes that port. Save anything open in ZCode first. Running
              tasks in ZCode will stop.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Not now</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => {
                setConfirmRead(false);
                void act("read");
              }}
            >
              Close and read
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

function ChangeBanner({ zcode }: { zcode: ReturnType<typeof useZCode> }) {
  const change = zcode.status?.account_change?.change;
  if (!change) return null;
  return (
    <div className="mb-4 flex items-start justify-between gap-3 rounded-xl border border-amber-a6 bg-amber-a2 px-4 py-3" role="status">
      <div className="min-w-0">
        <div className="font-medium text-foreground">{describeChange(change)}</div>
        <div className="text-xs text-muted-foreground">
          Sani noticed it {change.source === "window" ? "in the open ZCode window" : "on the last read"}, dropped what it
          knew about the old account, and re-read ZCode. Tasks Sani had linked to the old account were unlinked.
        </div>
      </div>
      <Button size="sm" variant="outline" onClick={() => void zcode.act("ack_change")}>
        Dismiss
      </Button>
    </div>
  );
}

const MODES: Array<{ value: string; label: string }> = [
  { value: "cli", label: "Command line" },
  { value: "window", label: "ZCode app window" },
];

const REASONING: Array<{ value: string; label: string }> = [
  { value: "", label: "Leave as ZCode has it" },
  { value: "low", label: "Low: quickest, fewest tokens" },
  { value: "high", label: "High" },
  { value: "max", label: "Max: most thinking" },
];

function HowSaniUsesZCode({ zcode }: { zcode: ReturnType<typeof useZCode> }) {
  const { snapshot, saveClaudeCode } = useSettings();
  const [confirmOn, setConfirmOn] = useState(false);
  if (!snapshot) return null;
  const control = zcode.status?.control ?? null;
  const windowMode = snapshot.zcode_mode === "window";
  return (
    <>
      <SettingsGroup title="How Sani uses ZCode">
        <SettingsRow
          label="Work through"
          state={
            windowMode
              ? "Sani drives the real ZCode app: your Start Plan, its models and permission cards"
              : "Runs `zcode -p`, which cannot see the Start Plan"
          }
        >
          <ChoiceMenu
            label="Work through"
            value={snapshot.zcode_mode}
            choices={MODES}
            onChange={(value) => void saveClaudeCode({ zcode_mode: value as "cli" | "window" })}
          />
        </SettingsRow>
        {windowMode ? (
          <>
            <SettingsRow
              label="ZCode control"
              state={
                control === null
                  ? "Restart Sani to apply the new mode"
                  : !control.enabled
                    ? "Off. Sani will not close or reopen ZCode"
                    : control.open
                      ? "On. ZCode's debug port is open right now, on this Mac only"
                      : "On. ZCode is reopened normally between runs"
              }
            >
              <Switch
                aria-label="ZCode control"
                checked={Boolean(control?.enabled)}
                disabled={control === null}
                onCheckedChange={(checked) => {
                  if (checked) setConfirmOn(true);
                  else void zcode.act("control", { enabled: false });
                }}
              />
            </SettingsRow>
            <SettingsRow label="How hard ZCode thinks" state="Lower uses fewer tokens on your plan">
              <ChoiceMenu
                label="How hard ZCode thinks"
                value={snapshot.zcode_effort}
                choices={REASONING}
                onChange={(value) => void saveClaudeCode({ zcode_effort: value as "" | "low" | "high" | "max" })}
              />
            </SettingsRow>
          </>
        ) : null}
      </SettingsGroup>
      <AlertDialog open={confirmOn} onOpenChange={setConfirmOn}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Let Sani control ZCode?</AlertDialogTitle>
            <AlertDialogDescription>
              To read and use ZCode, Sani has to close it and start it again with a private, local-only debug port. It
              keeps the port open only while it works (and about ten minutes after), then reopens ZCode normally. Sani
              answers ZCode's permission cards by your run limit, never “always allow”, and never types a password.
              Turn this off any time to close the port at once.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Not now</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => {
                setConfirmOn(false);
                void zcode.act("control", { enabled: true });
              }}
            >
              Turn on
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}

function LastRun({ run }: { run: NonNullable<ZCodeWindowRead["last_run"]> | null | undefined }) {
  if (!run) return null;
  return (
    <SettingsGroup title="Last run through ZCode">
      <SettingsRow
        label={`${run.project} · ${run.ok ? "finished" : run.cancelled ? "cancelled" : "did not finish"}`}
        state={`${readAt(run.at)} · ${run.seconds}s · ${run.model || "no model"}${run.plan ? ` (${run.plan})` : ""}`}
      >
        <span className="text-sm tabular-nums text-foreground">
          {run.tokens_used === null || run.tokens_used === undefined ? "tokens unknown" : `${full(run.tokens_used)} tokens`}
        </span>
      </SettingsRow>
      {run.error || run.stopped_reason ? (
        <SettingsRow label="Why it stopped" state={run.error || run.stopped_reason} />
      ) : null}
      {run.files_changed.length > 0 ? (
        <SettingsRow label="Files changed (checked on disk)" state={run.files_changed.slice(0, 8).join(", ")} />
      ) : null}
      {run.notes.map((note) => (
        <SettingsRow key={note} label="Check" state={note} />
      ))}
      {run.steps.length > 0 ? (
        <details className="px-4 py-2.5 text-sm">
          <summary className="cursor-pointer text-foreground">Steps ({run.steps.length})</summary>
          <ul className="mt-2 space-y-1 text-xs">
            {run.steps.map((step, index) => (
              <li key={`${index}-${step.label}`} className="flex justify-between gap-3">
                <span className="truncate text-foreground">{step.label}</span>
                <span className="shrink-0 text-muted-foreground">{step.status}</span>
              </li>
            ))}
          </ul>
        </details>
      ) : null}
    </SettingsGroup>
  );
}

function ControlStatus({ cdp }: { cdp: ZCodeWindowRead | null }) {
  const contract = cdp?.contract;
  const job = cdp?.job;
  return (
    <>
      <SettingsGroup title="Control">
        <SettingsRow
          label="ZCode's window"
          state={
            !contract
              ? "Not checked yet"
              : contract.ok
                ? `Looks as expected · ZCode ${contract.version}${contract.version_verified ? "" : " (not tested by Sani yet)"}`
                : `Does not look as expected (missing: ${contract.missing.join(", ")})`
          }
        />
        <SettingsRow
          label="Debug port"
          state={
            job?.state === "reading"
              ? "Open now, only on this Mac, until the read finishes"
              : job?.port_closed === false
                ? "Could not confirm it closed. Quit and reopen ZCode"
                : "Closed"
          }
        />
      </SettingsGroup>
      <p className="-mt-3 mb-6 text-xs text-muted-foreground">
        To read ZCode's screen, Sani starts ZCode with a debug port. That port has no password, so Sani uses a random
        one on this Mac only, checks that ZCode owns it, opens it only while reading, and closes it by reopening ZCode
        normally. It is off unless you press “Read now”.
      </p>
    </>
  );
}

function Balances({ balances }: { balances: ZCodeBalance[] }) {
  const plans = [...new Set(balances.map((item) => item.plan))];
  if (plans.length === 0) {
    return (
      <SettingsGroup title="Plans and balances">
        <SettingsRow label="Not read yet" state="Press “Read now”. Figures are never estimated" />
      </SettingsGroup>
    );
  }
  return (
    <>
      {plans.map((plan) => {
        const items = balances.filter((item) => item.plan === plan);
        const expires = items.find((item) => item.expires)?.expires;
        return (
          <SettingsGroup key={plan} title={plan}>
            {expires ? <SettingsRow label={expires} state="When this plan ends" /> : null}
            {items.map((item) => (
              <SettingsRow
                key={`${plan}-${item.model}`}
                label={item.model}
                state={`${item.percent ?? Math.round((100 * item.remaining) / item.total)}% left${item.reset ? ` · resets ${item.reset}` : ""}`}
              >
                <span className="text-sm tabular-nums text-foreground">
                  {full(item.remaining)} / {full(item.total)}
                </span>
              </SettingsRow>
            ))}
          </SettingsGroup>
        );
      })}
    </>
  );
}

function ModelsAndModes({ models }: { models: ZCodeWindowRead["models"] }) {
  if (!models) return null;
  const current = (list: Array<{ label: string; current: boolean }>) => list.find((item) => item.current)?.label;
  return (
    <SettingsGroup title="In the ZCode app now">
      <SettingsRow label="Model" state={models.current_model || "Not read"} />
      <SettingsRow label="Reasoning" state={current(models.reasoning) ?? "Not read"} />
      <SettingsRow label="Mode" state={current(models.modes) ?? "Not read"} />
      <SettingsRow
        label="Models offered"
        state={models.models.map((item) => `${item.model} (${item.plan || item.plan_id})`).join(" · ") || "None"}
      />
    </SettingsGroup>
  );
}

function Sessions({ sessions }: { sessions: ZCodeWindowRead["sessions"] }) {
  if (!sessions) return null;
  return (
    <SettingsGroup title={`Tasks in ZCode · ${sessions.count}${sessions.complete ? "" : " or more"}`}>
      {sessions.projects.map((project) => (
        <details key={project.path || project.name} className="px-4 py-2.5 text-sm">
          <summary className="flex cursor-pointer items-baseline justify-between gap-3">
            <span className="truncate font-medium text-foreground">{project.name || "No project"}</span>
            <span className="shrink-0 text-xs text-muted-foreground">
              {project.tasks.length} {project.tasks.length === 1 ? "task" : "tasks"}
            </span>
          </summary>
          <ul className="mt-2 space-y-1">
            {project.tasks.map((task) => (
              <li key={task.id} className="flex items-baseline justify-between gap-3 text-xs">
                <span className="truncate text-foreground">{task.title || "Untitled"}</span>
                <span className="shrink-0 text-muted-foreground">{task.age}</span>
              </li>
            ))}
          </ul>
        </details>
      ))}
    </SettingsGroup>
  );
}

export default function ZCodeSettings() {
  const { snapshot, saveClaudeCode, error } = useSettings();
  const zcode = useZCode();
  const { status } = zcode;
  if (!snapshot) return null;
  const cdp = status?.cdp ?? null;
  const ready = Boolean(status?.zcode?.installed && status.extra?.app_signed_in !== false);
  const chosen = status?.selection ?? {};
  const inUse = status?.extra?.zcode_default ?? null;
  const differs = Boolean(chosen.model && inUse && (inUse.provider !== chosen.provider || inUse.model !== chosen.model));
  return (
    <>
      <ChangeBanner zcode={zcode} />
      <ZCodeAccount zcode={zcode} />
      <SettingsGroup>
        <SettingsRow label="Let Sani use ZCode" state={ready ? undefined : "Install and sign in to ZCode first"}>
          <Switch
            aria-label="Let Sani use ZCode"
            checked={snapshot.zcode_cli_enabled}
            onCheckedChange={(checked) => void saveClaudeCode({ zcode_enabled: checked })}
          />
        </SettingsRow>
        <RunLimitRow note="Same limit and folders as Claude Code" />
        <SettingsRow label="Plan and model" state={chosen.model ? `Selected: ${chosen.model}` : "Pick one below"} />
      </SettingsGroup>

      <HowSaniUsesZCode zcode={zcode} />

      <ProjectFolders />

      <ControlStatus cdp={cdp} />
      <Balances balances={status?.balances?.items ?? []} />
      {status?.balances?.as_of ? (
        <p className="-mt-3 mb-6 text-xs text-muted-foreground">
          Read {readAt(status.balances.as_of)} from ZCode's own Settings page. They can be out of date until you read again.
        </p>
      ) : null}
      <ModelsAndModes models={cdp?.models ?? null} />
      <Sessions sessions={cdp?.sessions ?? null} />
      <LastRun run={cdp?.last_run} />

      <PlanPicker
        plans={status?.extra?.catalog ?? []}
        selected={chosen}
        inUse={inUse}
        onPick={(provider, model) => void zcode.act("select", { provider, model })}
      />

      {differs ? (
        <p className="mb-4 text-xs text-amber-11" role="status">
          Sani's pick differs from ZCode's own default ({inUse?.model}). Until a run confirms ZCode follows Sani's
          choice, change the model in the ZCode app too.
        </p>
      ) : null}
      {zcode.error || error ? (
        <p className="mb-4 text-sm text-destructive" role="alert">
          {zcode.error || error}
        </p>
      ) : null}
      <p className="flex items-start gap-2 text-xs text-muted-foreground">
        <Lock className="mt-0.5 size-3 shrink-0" aria-hidden="true" />
        <span>
          Plans and model sizes come from the ZCode app. “Context” is how much a model can hold in one conversation, not
          what is left on your plan. The account, balances, models and tasks are read off the ZCode app's own window when
          you press “Read now”, and show when they were read. Sani never calls Z.ai for them and never sees your password
          or token.
        </span>
      </p>
    </>
  );
}
