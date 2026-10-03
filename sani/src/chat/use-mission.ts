import { useCallback, useEffect, useRef, useState } from "react";
import {
  missionApprove,
  missionControl,
  missionGet,
  missionPurge,
  onAgentDone,
  type MissionPendingApproval,
} from "@/lib/tauri";
import type { ChatItem } from "./types";

export interface MissionState {
  missionId: string;
  status: string;
  verified: boolean;
  planVersion: number;
  controlEpoch: number;
  pendingApprovals: MissionPendingApproval[];
  scope: { scope_hash: string };
}

type MissionRecord = Awaited<ReturnType<typeof missionGet>>;

function toState(record: MissionRecord): MissionState {
  return {
    missionId: record.mission_id,
    status: record.status,
    verified: record.verified,
    planVersion: record.plan_version,
    controlEpoch: record.control_epoch,
    pendingApprovals: record.pending_approvals ?? [],
    scope: { scope_hash: record.scope?.scope_hash ?? "" },
  };
}

const message = (reason: unknown) => (reason instanceof Error ? reason.message : String(reason));

/**
 * Mission status of the newest mission-backed turn, kept truthful (C08/N10):
 * correlation comes from the PERSISTED row (history and reopen included) and
 * from live agent-done events, never from a guess, and reads happen in effects.
 * Controls go through the real mission IPC with the record's plan version and
 * control epoch, so the renderer cannot forge authority; the host validates
 * the compare-and-swap.
 */
export function useMission(items: ChatItem[]) {
  const [mission, setMission] = useState<MissionState | null>(null);
  const [error, setError] = useState("");
  const alive = useRef(true);

  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);

  const refresh = useCallback((missionId: string) => {
    void missionGet(missionId)
      .then((record) => {
        if (alive.current) setMission(toState(record));
      })
      .catch(() => undefined);
  }, []);

  // Persisted correlation: the newest message's mission, if any.
  const followed = items.length ? (items[items.length - 1].missionId ?? null) : null;

  useEffect(() => {
    if (!followed) {
      setMission(null);
      return;
    }
    let cancelled = false;
    void missionGet(followed)
      .then((record) => {
        if (!cancelled && alive.current) setMission(toState(record));
      })
      .catch(() => {
        if (!cancelled && alive.current) setMission(null);
      });
    return () => {
      cancelled = true;
    };
  }, [followed]);

  // Live correlation: a mission-backed terminal event refreshes the truth now.
  useEffect(() => {
    const unlisten = onAgentDone((done) => {
      if (done.mission_id) refresh(done.mission_id);
    });
    return () => {
      void unlisten.then((off) => off()).catch(() => undefined);
    };
  }, [refresh]);

  const control = (kind: "PAUSE" | "RESUME" | "CANCEL") => {
    if (!mission) return;
    void missionControl(mission.missionId, kind, mission.planVersion, mission.controlEpoch)
      .then(() => refresh(mission.missionId))
      .catch(() => undefined);
  };

  const revise = (revision: string) => {
    if (!mission) return;
    void missionControl(mission.missionId, "REVISE", mission.planVersion, mission.controlEpoch, "owner revision", {
      revision_request: revision,
    })
      .then(() => refresh(mission.missionId))
      .catch(() => undefined);
  };

  const setPriority = (priority: number) => {
    if (!mission) return;
    void missionControl(mission.missionId, "SET_PRIORITY", mission.planVersion, mission.controlEpoch, "owner priority", {
      priority,
    })
      .then(() => refresh(mission.missionId))
      .catch(() => undefined);
  };

  const purge = () => {
    if (!mission) return;
    void missionPurge(mission.missionId, "sani-local")
      .then(() => refresh(mission.missionId))
      .catch((reason) => setError(message(reason)));
  };

  const approve = (pending: MissionPendingApproval) => {
    if (!mission) return;
    setError("");
    void missionApprove(mission.missionId, mission.scope?.scope_hash ?? "", pending)
      .then(() => missionControl(mission.missionId, "RESUME", pending.plan_version, pending.control_epoch))
      .then(() => refresh(mission.missionId))
      .catch((reason) => setError(message(reason)));
  };

  const reject = (pending: MissionPendingApproval) => {
    if (!mission) return;
    void missionControl(
      mission.missionId,
      "CANCEL",
      pending.plan_version,
      pending.control_epoch,
      "owner rejected the pending action",
    )
      .then(() => refresh(mission.missionId))
      .catch((reason) => setError(message(reason)));
  };

  return { mission, error, control, revise, setPriority, purge, approve, reject };
}
