"""Live CUA acceptance tests (spec Task 5/8, gated by RUN_LIVE_CUA=1).

Runs the real agent stack end-to-end against the real Cua Driver daemon
in bounded mode and the configured model provider. Skipped unless the
environment opts in:

    RUN_LIVE_CUA=1 uv run pytest tests/e2e/test_cua_calculator.py

Covers:
- allowed path: launch Calculator, compute 6 x 7, observe, report 42;
- denial path: an out-of-manifest tool call is refused natively.
"""

import asyncio
import os
import subprocess
import uuid
from pathlib import Path

import pytest
from langchain_core.messages import HumanMessage

from assistant.agent.build import build_agent
from assistant.agent.context import AgentContext, RunBudget
from assistant.memory.namespaces import thread_id_for
from assistant.memory.postgres import open_memory_resources
from assistant.models import build_chat_model
from assistant.settings import Settings
from assistant.tools.cua import load_cua_tools
from assistant.tools.policy import cua_run_budget

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_LIVE_CUA") != "1",
    reason="live CUA test; set RUN_LIVE_CUA=1 with the bounded daemon running",
)

SKILLS_ROOT = Path(__file__).resolve().parents[2] / "src" / "assistant" / "skills"
POSTGRES_URL = "postgresql://assistant:assistant@127.0.0.1:5433/assistant"


def _tool_names(agent) -> list[str]:
    tools_node = agent.nodes.get("tools")
    assert tools_node is not None
    return sorted(getattr(tools_node.bound, "tools_by_name", {}).keys())


async def test_calculator_scenario_reports_observed_42(require_postgres: None) -> None:
    settings = Settings()  # real .env: provider key + CUA enabled + manifest path
    assert settings.cua_enabled, "CUA must be enabled for the live scenario"

    async with open_memory_resources(POSTGRES_URL) as mem:
        connection = await load_cua_tools(settings)
        model = build_chat_model(settings)
        bundle = build_agent(
            model=model,
            checkpointer=mem.saver,
            store=mem.store,
            skills_root=SKILLS_ROOT,
            extra_tools=connection.tools,
        )
        agent = bundle.agent
        names = _tool_names(agent)
        assert "launch_app" in names and "click" in names

        budget = RunBudget()
        cua_run_budget.set(budget)
        thread = thread_id_for(f"cua-live-{uuid.uuid4().hex[:6]}", "calculator")
        result = await agent.ainvoke(
            {
                "messages": [
                    HumanMessage(
                        "Open Calculator, calculate 6 x 7, verify the displayed "
                        "result, and tell me the answer."
                    )
                ]
            },
            {"configurable": {"thread_id": thread}},
            context=AgentContext(user_id="cua-live", chat_id="calculator"),
        )
        final_text = ""
        for message in reversed(result["messages"]):
            if getattr(message, "type", "") == "ai" and not getattr(message, "tool_calls", None):
                text = getattr(message, "text", None)
                final_text = str(text() if callable(text) else message.content or "")
                break
        assert "42" in final_text, f"agent did not report the observed 42: {final_text!r}"
        assert budget.used > 0, "no mutating CUA actions were recorded"


async def test_out_of_manifest_tool_is_refused_natively() -> None:
    # clipboard_read is NOT in the capability manifest: the bounded daemon
    # must refuse it. Uses the CLI form against the running daemon.
    proc = await asyncio.create_subprocess_exec(
        os.path.expanduser("~/.local/bin/cua-driver"),
        "clipboard_read",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
    except TimeoutError:
        proc.kill()
        pytest.fail("bounded daemon did not answer an out-of-manifest call in time")
    text = out.decode("utf-8", errors="replace").lower()
    markers = ("not allowed", "denied", "refused", "not permitted", "permission")
    assert proc.returncode != 0 or any(marker in text for marker in markers), (
        f"expected refusal, got rc={proc.returncode}: {text[:300]}"
    )


async def test_cua_unavailable_is_reported_not_faked(require_postgres: None) -> None:
    """Stopping the driver makes the gateway report cua_unavailable, never success.

    Live variant of the Task 8 isolation rule: with the daemon stopped,
    loading CUA tools must fail startup (spec section 13.6).
    """
    running = subprocess.run(
        ["pgrep", "-f", "CuaDriver.app/Contents/MacOS/cua-driver serve"],
        capture_output=True,
        text=True,
    )
    if running.returncode == 0:
        pytest.skip("bounded daemon is running; this check targets the stopped state")
    from assistant.tools.policy import CuaUnavailableError

    settings = Settings()
    with pytest.raises((CuaUnavailableError, RuntimeError)):
        await load_cua_tools(settings)
