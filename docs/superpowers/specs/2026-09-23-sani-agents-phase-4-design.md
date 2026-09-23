# Sani Phase 4 — Agents Experience Design

## Outcome

Sani exposes the two real sani-core registry entries, Velo and Deep Agent, and lets the user choose which one receives the next turn. New/missing settings select Velo; existing `velo`, `deep`, and legacy explicit `auto` values remain unchanged.

## Architecture

`runtime::agents` / `core_agents` remains the sole roster authority. Native `Settings.agent_mode` is the sole selection authority and is updated through the Phase 3 Settings facade without restarting sani-core. React fetches the roster, renders unavailable honestly, and never embeds a second roster.

## Experience

The Agents page maps known descriptor ids to friendly copy only after receiving them from the registry: Velo is “Fast computer-control agent” with Computer control, Desktop actions, Quick execution; Deep Agent is “Reasoning and memory agent” with Reasoning, Memory, Skills. The Home composer and Quick Settings use the live roster and the same native mutation. Assistant-message headers render stored `agent_name`, never current selection.

## Safety and tests

Unknown/missing roster and saved modes render an unavailable/compatibility state; no selection invents orchestration. Tests cover migration, direct next-run dispatch, restart persistence, all three surface convergence, no static roster, and historic attribution.
