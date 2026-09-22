"""Throwaway probe: what does the Deep Agent's astream actually yield?"""

import asyncio
import os
import sys

sys.path.insert(0, "src")


async def main():
    from langchain_core.messages import HumanMessage

    from assistant.agent.build import build_agent
    from assistant.agent.context import AgentContext
    from assistant.memory.local import open_local_memory_resources
    from assistant.models import build_chat_model
    from assistant.settings import Settings

    settings = Settings()
    async with open_local_memory_resources(settings.sani_db_path) as resources:
        bundle = build_agent(
            model=build_chat_model(settings),
            checkpointer=resources.saver,
            store=resources.store,
            skills_root=__import__("pathlib").Path("src/assistant/skills"),
        )
        seen = 0
        async for event in bundle.agent.astream(
            {"messages": [HumanMessage("Reply with exactly: cutover ok")]},
            {"configurable": {"thread_id": "probe-1"}},
            context=AgentContext(user_id="sani-local", chat_id="probe-1"),
            stream_mode="messages",
        ):
            seen += 1
            if seen <= 6:
                print(f"--- item {seen}: type={type(event).__name__}")
                if isinstance(event, tuple):
                    print("    len:", len(event))
                    for i, part in enumerate(event):
                        print(f"    [{i}] {type(part).__name__}: {repr(part)[:220]}")
                else:
                    print("   ", repr(event)[:400])
        print("TOTAL ITEMS:", seen)


asyncio.run(main())
