import { useEffect, useState } from "react";
import { Conversation } from "@/components/chat/conversation";
import { Composer } from "@/components/chat/composer";
import { ApprovalCard, MissionBar } from "@/components/chat/mission-bar";
import { VoiceDraft } from "@/components/chat/voice-draft";
import { useChatStore } from "@/chat/store";
import { useMission } from "@/chat/use-mission";
import { useClaudeCode } from "@/chat/use-claude-code";
import { QuickReplyContext } from "@/chat/quick-reply";
import { useSettings } from "@/app/settings/SettingsContext";
import { pressEscape, startDictation, stopListening, submitText } from "@/lib/tauri";

/**
 * Chat is the home surface (DESIGN: everything else supports the
 * conversation). Empty, the composer sits in the middle of the page and
 * invites; once there is a transcript it docks to the bottom.
 */
export default function ChatPage() {
  const items = useChatStore((state) => state.items);
  const uiState = useChatStore((state) => state.uiState);
  const partial = useChatStore((state) => state.partial);
  const { snapshot, agents, agentsAvailable, selectAgent } = useSettings();
  const mission = useMission(items);
  const claudeCode = useClaudeCode();

  const capturing = uiState === "listening" || uiState === "preparing" || uiState === "finalizing";
  const working = uiState === "working";
  // Mic pressed in this window: words go into the input box instead of being sent.
  const [dictating, setDictating] = useState(false);
  useEffect(() => {
    if (uiState === "idle" || uiState === "error" || uiState === "working") setDictating(false);
  }, [uiState]);
  const voiceCapture = capturing && !dictating;

  // Escape cancels an open voice capture; a pending approval owns Escape itself.
  const hasPending = (mission.mission?.pendingApprovals.length ?? 0) > 0;
  useEffect(() => {
    if (!capturing || hasPending) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") void pressEscape();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [capturing, hasPending]);

  const onMic = () => {
    if (capturing) {
      void stopListening();
    } else {
      setDictating(true);
      void startDictation();
    }
  };

  const composer = (
    <Composer
      state={uiState}
      agents={agents}
      agentsAvailable={agentsAvailable}
      agentMode={snapshot?.agent_mode ?? ""}
      onSelectAgent={(id) => void selectAgent(id)}
      onSend={submitText}
      onMic={onMic}
      onStop={() => void pressEscape()}
      autoFocus
      dictating={dictating}
      partial={partial}
      claudeCode={claudeCode.status}
    />
  );

  const pending = mission.mission?.pendingApprovals ?? [];
  const missionUi = (
    <>
      {mission.mission ? (
        <MissionBar
          status={mission.mission.status}
          verified={mission.mission.verified}
          missionId={mission.mission.missionId}
          onControl={mission.control}
          onRevise={mission.revise}
          onSetPriority={mission.setPriority}
          onPurge={mission.purge}
        />
      ) : null}
      {pending.map((entry, index) => (
        <ApprovalCard
          key={entry.step_id}
          pending={entry}
          shortcuts={index === 0}
          onApprove={mission.approve}
          onReject={mission.reject}
        />
      ))}
      {mission.error ? (
        <p className="text-sm text-destructive" role="alert">
          {mission.error}
        </p>
      ) : null}
    </>
  );

  const quickReply = {
    enabled: !voiceCapture && !working && !dictating,
    send: (text: string) => void submitText(text).catch(() => undefined),
  };

  if (items.length === 0 && !voiceCapture && !working) {
    return (
      <div className="flex h-full flex-col items-center justify-center px-6 pb-16">
        <div className="flex w-full max-w-[680px] flex-col gap-5">
          <h1 className="text-center text-xl font-semibold tracking-tight text-foreground">
            What should we work on?
          </h1>
          {composer}
          {missionUi}
        </div>
      </div>
    );
  }

  return (
    <QuickReplyContext.Provider value={quickReply}>
    <div className="flex h-full min-h-0 flex-col">
      <Conversation
        items={items}
        footer={
          voiceCapture ? (
            <VoiceDraft
              state={uiState}
              partial={partial}
              onFinish={() => void stopListening()}
              onCancel={() => void pressEscape()}
            />
          ) : null
        }
      />
      <div className="mx-auto flex w-full max-w-[760px] flex-col gap-2.5 px-6 pb-5">
        {missionUi}
        {composer}
      </div>
    </div>
    </QuickReplyContext.Provider>
  );
}
