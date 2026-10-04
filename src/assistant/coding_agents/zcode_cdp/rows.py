"""Read a ZCode task's live conversation (``rows``) and turn it into Sani's step events.

ZCode's conversation view keeps the whole transcript as structured rows (assistant text,
reasoning, tool calls with name, input and status) plus a session phase. The page-side script
returns only the rows newer than a baseline, with long text capped, so a poll stays small
however long the task is. Statuses seen on a real ZCode 3.14.4: tool calls are ``success``,
``pendingApproval`` or ``cancelled`` (a denied card); phases are ``running``,
``completedSuccess`` and ``completedInterrupted`` (after Stop).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from assistant.claude_code.events import (
    AssistantText,
    ClaudeEvent,
    Final,
    Init,
    ToolEnd,
    ToolStart,
)
from assistant.claude_code.labels import MUTATING_TOOLS, describe_tool, fingerprint
from assistant.claude_code.redact import screen

JS_STATE = """(()=>{
 const q=s=>document.querySelector(s);
 const tl=q('[data-testid=v4-timeline]');
 const pane=q('[data-testid=v4-session-pane-workspace-main]');
 let p=null;
 if(tl){const fk=Object.keys(tl).find(k=>k.startsWith('__reactFiber'));
  let f=fk&&tl[fk];for(let i=0;i<2&&f;i++)f=f.return;p=f&&f.memoizedProps}
 const cut=(v,n)=>typeof v==='string'&&v.length>n?v.slice(0,n):v;
 const slim=i=>{if(!i||typeof i!=='object')return null;const o={};
  for(const k of ['file_path','path','command','description','pattern','url','query',
                  'old_string','new_string','content'])
   if(typeof i[k]==='string')o[k]=cut(i[k],1200);
  if(Array.isArray(i.edits))o.edits=i.edits.slice(0,8).map(e=>({old_string:cut(e&&e.old_string,300),
   new_string:cut(e&&e.new_string,300)}));
  return o};
 const rows=((p&&p.rows)||[]).filter(r=>r.rowId>__AFTER__).map(r=>({
  id:r.rowId,turn:r.turnId,kind:r.kind,tool:r.toolName,call:r.toolCallId,
  status:r.status||r.state,
  text:(r.kind==='assistantText'||r.kind==='userInput')?cut(r.text,8000):undefined,
  input:slim(r.input),
  out:typeof r.output==='string'?cut(r.output,1500)
      :(r.output&&typeof r.output.text==='string'?cut(r.output.text,1500):undefined)}));
 const pending=((p&&p.rows)||[]).filter(r=>r.kind==='toolCall'&&r.status==='pendingApproval')
  .map(r=>({call:r.toolCallId,tool:r.toolName,input:slim(r.input)}));
 return {has_timeline:!!p,phase:p?p.sessionPhase:null,
  session_id:pane?pane.getAttribute('data-session-id'):null,
  seq:pane?pane.getAttribute('data-projection-seq'):null,
  stop:!!q('[data-testid=v4-stop]'),
  card:!!q("[role=listbox][aria-label='Permission required']"),pending,
  workspace_path:(q('[data-testid=workspace-path]')||{}).innerText||'',
  rows}})()"""


def state_script(after_row: int) -> str:
    return JS_STATE.replace("__AFTER__", str(int(after_row)))


#: Phases that mean the turn is over, and what they mean.
_DONE = {"completedSuccess": True, "completedInterrupted": False}
_TOOL_DONE_OK = {"success"}
_TOOL_DONE_FAILED = {"error", "failed", "failure", "cancelled", "denied", "rejected", "timeout"}
_TOOL_WAITING = {"pendingApproval"}


def pending_approvals(state: dict[str, Any]) -> list[Approval]:
    """Every tool call waiting on a card right now (there can be several at once)."""
    return [
        Approval(str(p.get("call")), str(p.get("tool") or "tool"), dict(p.get("input") or {}))
        for p in state.get("pending") or []
    ]


def phase_is_done(phase: str | None) -> bool:
    """A finished turn: the two phases seen so far, or anything else that starts "completed"."""
    return bool(phase) and (phase in _DONE or str(phase).startswith("completed"))


@dataclass
class Approval:
    """A tool call waiting for a yes or no on ZCode's permission card."""

    call_id: str
    tool: str
    input: dict[str, Any]


@dataclass
class RowTracker:
    """Feeds on successive page states; yields typed events exactly once each."""

    cwd: str
    session_announced: bool = False
    last_row: int = 0
    opened: dict[str, ToolStart] = field(default_factory=dict)
    closed: set[str] = field(default_factory=set)
    said: set[int] = field(default_factory=set)
    last_text: str = ""
    texts: list[str] = field(default_factory=list)
    #: Claimed file edits by relative path, and Bash outputs seen (for "was it really run").
    claimed_files: list[str] = field(default_factory=list)
    outputs: dict[str, str] = field(default_factory=dict)
    steps: int = 0
    #: Calls that started without any permission card (ZCode judged them safe on its own).
    ran_unasked: list[Approval] = field(default_factory=list)
    seen_pending: set[str] = field(default_factory=set)

    def feed(self, state: dict[str, Any]) -> list[ClaudeEvent]:
        events: list[ClaudeEvent] = []
        session = str(state.get("session_id") or "")
        if session and not self.session_announced:
            self.session_announced = True
            events.append(Init(session_id=session, cwd=self.cwd))
        for row in state.get("rows") or []:
            self.last_row = max(self.last_row, int(row.get("id") or 0))
            kind = row.get("kind")
            if kind == "assistantText" and row.get("status") == "complete":
                rid = int(row.get("id") or 0)
                text = str(row.get("text") or "").strip()
                if rid not in self.said and text:
                    self.said.add(rid)
                    self.texts.append(text)
                    self.last_text = text
                    events.append(AssistantText(text=text))
            elif kind == "toolCall":
                call = str(row.get("call") or row.get("id"))
                name = str(row.get("tool") or "tool")
                tool_input = dict(row.get("input") or {})
                status = str(row.get("status") or "")
                finished = status in _TOOL_DONE_OK or status in _TOOL_DONE_FAILED
                if status in _TOOL_WAITING:
                    self.seen_pending.add(call)
                if call not in self.opened and not tool_input and not finished:
                    continue  # ZCode is still streaming the arguments; label it once they are in
                if call not in self.opened:
                    label, detail, path = describe_tool(name, tool_input, self.cwd)
                    start = ToolStart(
                        id=call,
                        name=name,
                        label=label,
                        detail=detail,
                        fingerprint=fingerprint(name, tool_input),
                        mutating=name in MUTATING_TOOLS,
                        path=path,
                    )
                    self.opened[call] = start
                    self.steps += 1
                    events.append(start)
                start = self.opened[call]
                if call not in self.closed and finished:
                    self.closed.add(call)
                    if status in _TOOL_DONE_OK and call not in self.seen_pending:
                        self.ran_unasked.append(Approval(call, name, tool_input))
                    ok = status in _TOOL_DONE_OK
                    out = screen(str(row.get("out") or ""), limit=600)
                    if out:
                        self.outputs[call] = out
                    if ok and start.name in {"Write", "Edit", "MultiEdit", "NotebookEdit"}:
                        if start.path and start.path not in self.claimed_files:
                            self.claimed_files.append(start.path)
                    summary = "" if ok else ("denied" if status == "cancelled" else status)
                    events.append(ToolEnd(id=call, ok=ok, summary=summary or out))
        return events

    def final(self, state: dict[str, Any], *, model: str) -> Final:
        phase = str(state.get("phase") or "")
        ok = _DONE.get(phase, phase.startswith("completed") and "Error" not in phase)
        return Final(
            ok=ok,
            subtype=phase,
            text=self.last_text,
            session_id=str(state.get("session_id") or ""),
            turns=len(self.texts),
            model=model,
        )
