"""Exercise actual production lifespan; replace only paid model, disable CUA."""

import json
import uuid

import httpx
import pytest
from langchain_core.messages import AIMessage

from assistant.main import create_application
from assistant.settings import Settings
from tests.helpers.scripted_model import ScriptedChatModel


@pytest.mark.parametrize("stream", [False, True])
async def test_production_lifespan_claims_and_closes_store(monkeypatch, require_postgres, stream):
    model = ScriptedChatModel(responses=[AIMessage("production wiring checked")])
    monkeypatch.setattr("assistant.main.build_chat_model", lambda settings: model)
    settings = Settings(
        agent_gateway_api_key="test-gateway-key",
        model_provider="openrouter",
        openrouter_api_key="dummy",
        cua_enabled=False,
    )
    app = create_application(settings)
    turn = uuid.uuid4().hex
    headers = {
        "Authorization": "Bearer test-gateway-key",
        "X-OpenWebUI-User-Id": "production-wiring-test",
        "X-OpenWebUI-Chat-Id": turn,
        "X-OpenWebUI-User-Message-Id": turn,
    }
    payload = {
        "model": settings.assistant_model_id,
        "messages": [{"role": "user", "content": "check wiring"}],
        "stream": stream,
    }
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://gateway"
        ) as client:
            first = await client.post("/v1/chat/completions", headers=headers, json=payload)
            assert first.status_code == 200
            if stream:
                assert "data: [DONE]" in first.text
                chunk = json.loads(first.text.splitlines()[0].removeprefix("data: "))
                run_id = chunk["id"].removeprefix("chatcmpl-")
            else:
                run_id = first.json()["id"].removeprefix("chatcmpl-")
            store = app.state.run_store
            record = await store.get_run(run_id)
            assert record is not None
            assert record.status == "completed"
            assert model.call_index == 1
            duplicate = await client.post("/v1/chat/completions", headers=headers, json=payload)
            assert duplicate.status_code == 200
            if stream:
                assert duplicate.headers["content-type"].startswith("text/event-stream")
                assert "data: [DONE]" in duplicate.text
                replay = json.loads(duplicate.text.splitlines()[0].removeprefix("data: "))
            else:
                replay = duplicate.json()
            assert replay["id"] == f"chatcmpl-{run_id}"
            assert model.call_index == 1
    assert store._conn.closed


async def test_production_startup_failure_closes_run_store(monkeypatch, require_postgres):
    from assistant.runtime.runs import RunStore

    opened = []
    connect = RunStore.connect

    async def capture(url):
        store = await connect(url)
        opened.append(store)
        return store

    def fail_model(settings):
        raise RuntimeError("model initialization failed")

    monkeypatch.setattr(RunStore, "connect", capture)
    monkeypatch.setattr("assistant.main.build_chat_model", fail_model)
    app = create_application(Settings(
        agent_gateway_api_key="test-gateway-key", model_provider="openrouter",
        openrouter_api_key="dummy", cua_enabled=False,
    ))
    with pytest.raises(RuntimeError, match="model initialization failed"):
        async with app.router.lifespan_context(app):
            pytest.fail("startup should not reach serving state")
    assert len(opened) == 1
    assert opened[0]._conn.closed
