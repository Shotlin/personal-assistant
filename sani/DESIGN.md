# Sani UI design rules

How Sani's windows are designed. Read this before building or reviewing any
user-facing surface in `sani/src`. Light mode only. The rule set is adapted
from OpenWork's DESIGN.md (MIT, see `THIRD_PARTY_NOTICES.md`) and changed
where Sani differs (voice, computer control, missions).

## Who it is for
One person doing real work by talking or typing to an assistant on their own
Mac. Chat is the home surface; everything else supports the conversation.
Common path fast and obvious; advanced options quiet.

## Principles
- **P1 Show state, don't explain the UI.** Report what is true ("Ready",
  "Needs setup"), not how the screen works.
- **P2 Title or description, never both.**
- **P3 Progressive disclosure.** Raw detail sits behind a quiet code icon in a
  fixed trailing slot (always visible on failures) or a "Technical details" row.
- **P4 Presence with a lock beats absence.** Blocked things stay visible with a
  lock and a reason.
- **P5 Reuse before you build.** Use `src/components/ui/*`. Never re-implement
  keyboard/focus/ARIA behaviour.
- **P6 Density from data, not prose.** Rows, values, state. 12-16px padding.
- **P7 One focal element per screen.** While a decision is pending, the consent
  card is it.
- **P8 Undo over confirm.** Confirm only destructive or external actions.
- **P9 Consent names action, data and risk** in one state line.
- **P10 Evidence or it didn't ship.** UI changes need a screenshot.
- **P11 Continuity over lifecycle.** No interstitials invented from internal
  steps. The user's message and layout stay put while work continues.

## Structure
- **S1** Flat, not boxed: no card inside a card. Whitespace and one hairline.
- **S2** Settings are compact rows: label left, current state right, at most one
  action. Rows 40-48px.
- **S3** Disclosure uses a chevron that rotates 90 degrees.
- **S4** Nothing auto-navigates or steals focus.
- **S5** One action, one home. Show chords for frequent actions (`⏎`, `⌘⏎`, `esc`)
  and only when they are bound.

## Copy
- Verb-first, outcome-specific labels ("Allow", "Delete chat"); never "OK".
- An action keeps its name through the flow.
- Name things by what people control, never tool ids, JSON or engine names.
- Tool activity is sentence-first: "Opened Google Chrome · 1s".
- Blocked is not an error: neutral ink plus a lock. Red is for failures only.
- Errors and empty states give direction; never "Oops".
- Sentence case. No ALL-CAPS labels.

## States
Every data surface designs loading (layout-matching), empty, error,
blocked, offline and success.

## Visual system
Tokens live in `src/styles/app.css`. Use them; never hardcode.
- **V1** System font stack, 13px/1.5 body. Hierarchy from weight and opacity
  before size. Headings at most 20px, slightly negative tracking.
- **V2** Radix slate neutrals. Primary action is the near-black accent
  `--sani-accent`. Colour marks only what needs attention. No `#000`/`#fff`
  literals in components.
- **V3** Depth: hairline borders, plus one soft layered shadow for lifted
  surfaces. Never a solid border and a heavy shadow together.
- **V4** Radius: `--radius` for controls, `--sani-radius` (16px) for the composer
  and shells. Nested radii are concentric.
- **V5** Icons: lucide, 16px. A company or service uses its logo mark. No emoji,
  no sparkle/wand/robot icons for "AI".
- **V6** Motion 120-200ms, ease-out. No entrance animation on high-frequency
  surfaces. Shimmer and the 3x3 dot-matrix loader mark only the step that is
  running right now. Respect `prefers-reduced-motion`.
- **V7** Anti-patterns: purple/blue gradients, glassmorphism, identical card
  grids, `transition: all`, `outline: none` without a replacement ring, badge
  soup.

## Chat and steps
- **T1** A turn's steps read as one rail: glyph, sentence label, duration right.
  Finished turns collapse to "Worked for 1m 19s · 12 steps".
- **T2** Failures stay inline with the fix. Raw input/output lives under
  "Technical details".
- **T3** The consent card sits above the composer and offers Allow / Decline.
- **T4** Composer: one round send/stop button, agent as a quiet pill.

## Before opening a UI PR
- [ ] Screenshot(s) at real size, including non-happy states.
- [ ] Reuses `src/components/ui` primitives.
- [ ] No surface has both a title and a description.
- [ ] Tokens only. Focus ring visible. Reduced motion respected.
