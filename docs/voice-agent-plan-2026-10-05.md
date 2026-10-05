# Shubh: the voice agent that actually knows things — plan (2026-10-05)

## Where we are
The voice side works: Shubh calls you on WhatsApp, listens in Hindi/Bengali/English, waits for you to finish,
answers in an expressive voice, and gives a recap of a long message. What it does NOT have yet is a *brain*
behind the voice. Today the voice model is a talker only: it repeats, it cannot advise, it forgets nothing
because it knows nothing, and the Deep Agent never hears what you said.

## The idea in one picture
```
 YOU  <-- WhatsApp call -->  SHUBH (voice: fast, friendly, speaks)
                               |  \__ notes board: every task, person, time, amount you mention
                               |  \__ context: what Sani already knows (projects, people, reminders)
                               |  \__ "ask the expert" (Deep Agent) when a real answer is needed
                               v
                          THE BRIEF  ->  DEEP AGENT (thinks, codes, tests via ZCode / Claude Code)
                               ^                          |
                               |__ short spoken progress _|   Shubh calls you back when done or stuck
```
Two brains on purpose: the voice brain must answer in under a second; the Deep Agent is slower but far smarter.
Shubh talks, the Deep Agent thinks, and a shared notes board keeps them in sync.

## What you asked for -> how it is solved
| You want | How |
|---|---|
| Understands everything, summarises properly | Live **notes board**: after each thing you say, items are filed (what / who / when / how much). Recaps read the board, not the chat, so nothing is merged or dropped. |
| Gives advice ("salah"), like a sales or developer person | **Skill packs** (sales coach, dev advisor, schedule keeper) loaded per topic; hard questions go to the Deep Agent while Shubh says a natural "ek second, dekhta hoon…". |
| Corrects you when you say something wrong | A background **checker** compares each new statement with the board and Sani's memory (e.g. "you said Soumen before, now Shubham"). It speaks only when sure, and only at a pause. |
| Hangs up the WhatsApp call for you | Shubh asks "call cut kar du?" and ends it on a yes, judged by meaning, in any language (no word lists). |
| Does the work after the call | The board becomes a **brief** sent to the Deep Agent. Progress and results come back as short spoken updates, plus a text copy on WhatsApp. |
| Calls you when done / when stuck | **Callback loop**: Shubh rings you with a 20-second summary and, if needed, one clear question. |

## Phases (each ends with something you can test)
**Phase 0, fix what the last calls exposed. DONE offline 2026-10-05, live call still to confirm**
- "Is Shubh speaking?" flag stuck on after any reply (found today: 12.5 ms of audio stayed counted). FIXED. This caused false interruptions and blocked the hang-up.
- Recap mode fired when you asked a *question* inside a long message (you asked for a sales pitch; it recapped old tasks). Recap only when the message is a list of tasks or a repeat request.
- Hang-up by meaning with a yes/no confirmation (the language detector heard "ओके रखो" as Gujarati and the word list missed it).
- Treat a wrongly detected language (Gujarati, Marathi…) as the language you were just using.
- The "6 बजे" item: ask about unclear items instead of keeping them vague.
Result: judge helper by meaning (`src/judge.mts`, ~0.2 s per decision, 18/18 on test phrases incl. garbled "राखी"); recap vs answer chosen by the judge
(sales-pitch question now answered, task lists still recapped); hang-up flow 4/4 scenarios (offer -> yes, offer -> "no, one more thing", "send it" never hangs up,
direct Bengali goodbye); listener language labels other than hi/bn/en are ignored. Still to confirm on a real call: that the WhatsApp call really ends.
Test: a call with a long update, then a question; Shubh answers the question, then hangs up when you say so.

**Phase 1, notes board + smarter turns. DONE offline 2026-10-05, live call still to confirm**
- Router per turn: chat / recap / advice / task. Notes board filed in the background after each turn (cheap, not on the speaking path).
- Recap and "repeat" read the board. Unclear items are asked about.
Result (`src/notes.mts`, `judge.mts` router, `conversation.mts`): board filed in the background after each message (patches applied in code, so items cannot drift;
overwrite/remove only after a correction word; long messages get 2 review passes; complete in 5/5 runs, 5-9 s, never on the speaking path). Router per turn: chat / recap / advice
(~0.2 s). Corrections get a one-line confirm; the board's conflict ("person changed from Shubham to Soumen") is raised by Shubh itself at the next quiet moment.
Advice mode answers with a recommendation, a reason and a next step. Recap reads the board, asks about UNCLEAR items.
Test: say 6 items in a messy way; the recap has all 6, correct.

**Phase 2, context and memory. DONE offline 2026-10-05, live call still to confirm**
- At call start Shubh loads a private background (<= 3,600 chars): earlier call summaries, still-open items, rules/facts he stated, Sani's saved notes and the titles of his recent Sani chats (secrets screened out).
- After the call: a short model summary + the board items go to `voice-service/var/voice.db`, and a digest is written to Sani's memory as `/memories/voice-calls.md` (the Deep Agent reads it).
- New router mode `recall`: questions about an earlier day/call are answered only from that background; "I don't remember" when it is not there.
Result (`src/memory.mts`, `context.mts`, `sani.mts`, `summary.mts`, `callMemory.mts`, `secrets.mts`; `npm run memory-test`, 23/23 on his real sentences): "what did I say yesterday about the laundry app" answered correctly in Hindi and Bengali, "what is pending" lists the open items, an invented topic (hotel booking) gets "I don't remember", small talk does not recite memory, a spoken password is dropped, a restated meeting replaces the old one.
Test: "what did I ask yesterday about the laundry app?" works.

**Phase 3, connect the Deep Agent. BUILT + tested offline 2026-10-05 against a stand-in core; real Deep Agent run blocked by the OpenRouter key limit ("Key limit exceeded"), live call still to do**
- The voice service talks to sani-core the way the desktop app does (private stdio, no web server). It sends the brief, receives progress.
- Mid-call "ask the expert": a question goes to the Deep Agent, Shubh covers the wait naturally.
Result (`src/core.mts`, `brief.mts`, `handoff.mts`, `expert.mts`, router modes `expert` and a hand-off approval flow in `conversation.mts`; `npm run phase3-test`): sani-core starts as a child in ~1.3 s and speaks the real framed protocol; a spoken task is read back, then Shubh asks "send it to my agent?" and ONLY his spoken yes sends it (a stand-in marker without a spoken offer does not count); the brief is built from the board with his constraints and the owner's four standing rules; "how is it going?" is answered from the live job; a technical question goes to the agent while Shubh says "एक सेकंड, देखता हूँ…", and a silent agent never produces an invented answer. The voice core never controls the screen (CUA off) and Claude Code is capped at "edit".
Test: you say "fix the refund flow, don't touch the UI"; Deep Agent starts work in ZCode; Shubh can say what it is doing.

**Phase 4, callback loop. BUILT + dry-run offline 2026-10-05; live call pending (needs the OpenRouter key limit raised for the real Deep Agent)**
- Shubh rings you when the work finishes or needs a decision; 20-second summary; WhatsApp text copy for the record.
Result: `npm run talk` is now ONE long-running window: call 1, then the Deep Agent works, then Shubh sends a WhatsApp text copy and CALLS with a 2-3 sentence spoken report (`src/report.mts`, `supervisor.mts`, `liveCall.mts`, `placeCall.mts`, `schedule.mts`). Unanswered: 2 rings 2 minutes apart, then only the text copy. "Call me back in two minutes / at 6" is parsed (code for relative time, a tiny model call for clock time) and kept in `voice.db` (`callbacks`). Only the owner's number is ever called or texted. `npm run phase4-test` (offline, ~Rs 3) and `npm run dryrun` (fake phone replaying his own recording through the real pipeline, ~Rs 4).
Test: finish a task, your phone rings with the result.

**Phase 5, skills and character**
- Sales coach (role-play a customer or a seller), dev advisor, schedule keeper. Try `rehan` for Bengali.

**Phase 6, acting on other people and hardening**
- Reminders first. Messages or calls to *other* people only after an explicit yes each time (see decisions below).
- Cost caps, logs, retry, and running as a proper sidecar of the Sani desktop app.

## Decisions (ANSWERED by the owner 2026-10-05)
Answers: (1) others only with his spoken yes for each message, spare number; (2) deploy/publish, spend money, message/call others, delete: ALWAYS ask; (3) keep `voice-service/` separate for now (default, not contested); (4) transcripts + recordings 30 days on the Mac, summaries/open items stay; (5) CLAUDE.md exception written.
Original questions:
1. **Calling or messaging other people for you** (e.g. "call Shubham and tell him to pay"). Safest: Shubh prepares the message and the number, you approve each one by voice. Unofficial WhatsApp calling can get an account banned, so calls to others would use the spare number only, never your main one. OK?
2. **What needs your yes before it happens**: deploying, spending money, sending messages to others, deleting. My suggestion: all four always ask.
3. **Where it lives**: keep `voice-service/` as its own program for now, move it inside the Sani desktop app later. OK?
4. **Call recordings**: Shubh keeps text transcripts and your voice recording only on your Mac, deleted after 30 days unless you keep one. OK?
5. **Product rule exception**: Sani's rule is "only OpenRouter leaves the machine". The voice service also uses Sarvam (listening, thinking, speaking) and WhatsApp. This must be written in CLAUDE.md as an approved exception. OK?

## Honest limits
- Sarvam's conversation model is great at speaking Indian languages, but weaker at precise reasoning (it merged two times in one test). So real advice and checking use the Deep Agent's model; Sarvam only speaks.
- The WhatsApp calling library is unofficial; it can break when WhatsApp changes, and a ban is possible. Use the spare number.
- Things I have not measured live yet: hang-up when you hang up first, how the callback feels, long-call stability.
