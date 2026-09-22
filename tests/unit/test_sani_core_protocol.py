"""Unit tests for sani-core framing and typed messages (Sani master doc 13/14)."""

from __future__ import annotations

import asyncio
import contextlib
import os

import orjson
import pytest

from assistant.core.protocol import (
    MAX_FRAME_BYTES,
    Event,
    ProtocolError,
    Request,
    Response,
    error_from_request,
    read_frame,
    response_from_request,
    write_frame,
)


def _frame_bytes(payload: dict[str, object]) -> bytes:
    body = orjson.dumps(payload)
    return len(body).to_bytes(4, "big") + body


def _feed(reader: asyncio.StreamReader, *chunks: bytes) -> None:
    for chunk in chunks:
        reader.feed_data(chunk)


async def test_read_frame_roundtrip_two_frames_back_to_back() -> None:
    reader = asyncio.StreamReader()
    _feed(reader, _frame_bytes({"a": 1}), _frame_bytes({"b": [1, 2], "c": "x"}))
    reader.feed_eof()
    assert await read_frame(reader) == {"a": 1}
    assert await read_frame(reader) == {"b": [1, 2], "c": "x"}


async def test_read_frame_returns_none_on_clean_eof() -> None:
    reader = asyncio.StreamReader()
    reader.feed_eof()
    assert await read_frame(reader) is None


async def test_read_frame_returns_none_after_final_frame() -> None:
    reader = asyncio.StreamReader()
    _feed(reader, _frame_bytes({"x": 1}))
    reader.feed_eof()
    assert await read_frame(reader) == {"x": 1}
    assert await read_frame(reader) is None


async def test_read_frame_truncated_body_raises() -> None:
    reader = asyncio.StreamReader()
    body = orjson.dumps({"a": 1})
    _feed(reader, len(body).to_bytes(4, "big"), body[:-3])
    reader.feed_eof()
    with pytest.raises(ProtocolError, match="truncated"):
        await read_frame(reader)


async def test_read_frame_truncated_header_raises() -> None:
    reader = asyncio.StreamReader()
    _feed(reader, b"\x00\x00")
    reader.feed_eof()
    with pytest.raises(ProtocolError, match="truncated"):
        await read_frame(reader)


async def test_read_frame_invalid_json_raises() -> None:
    reader = asyncio.StreamReader()
    _feed(reader, (4).to_bytes(4, "big"), b"nope")
    reader.feed_eof()
    with pytest.raises(ProtocolError, match="invalid JSON"):
        await read_frame(reader)


async def test_read_frame_non_object_json_raises() -> None:
    reader = asyncio.StreamReader()
    _feed(reader, (2).to_bytes(4, "big"), b"[]")
    reader.feed_eof()
    with pytest.raises(ProtocolError, match="object"):
        await read_frame(reader)


async def test_read_frame_oversize_length_raises() -> None:
    reader = asyncio.StreamReader()
    _feed(reader, (MAX_FRAME_BYTES + 1).to_bytes(4, "big"))
    reader.feed_eof()
    with pytest.raises(ProtocolError, match="too large"):
        await read_frame(reader)


async def test_read_frame_accepts_exactly_max_frame_bytes() -> None:
    body = b'{"p":"' + b"x" * (MAX_FRAME_BYTES - 8) + b'"}'
    assert len(body) == MAX_FRAME_BYTES
    reader = asyncio.StreamReader()
    _feed(reader, len(body).to_bytes(4, "big"), body)
    reader.feed_eof()
    frame = await read_frame(reader)
    assert frame is not None
    assert len(frame["p"]) == MAX_FRAME_BYTES - 8


async def test_write_frame_roundtrip_through_pipe() -> None:
    loop = asyncio.get_running_loop()
    read_fd, write_fd = os.pipe()
    out_reader = asyncio.StreamReader()
    await loop.connect_read_pipe(
        lambda: asyncio.StreamReaderProtocol(out_reader), os.fdopen(read_fd, "rb")
    )
    transport, protocol = await loop.connect_write_pipe(
        lambda: asyncio.StreamReaderProtocol(asyncio.StreamReader()), os.fdopen(write_fd, "wb")
    )
    writer = asyncio.StreamWriter(transport, protocol, None, loop)
    try:
        await write_frame(writer, {"hello": "world"})
        frame = await asyncio.wait_for(read_frame(out_reader), 2.0)
        assert frame == {"hello": "world"}
    finally:
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()


def test_response_helpers_map_from_request() -> None:
    request = Request(id="r1", method="run.start", params={"text": "hi"})
    ok = response_from_request(request, {"status": "done"})
    assert ok == Response(id="r1", ok=True, result={"status": "done"}, error="")
    bad = error_from_request(request, "unknown method")
    assert bad.ok is False
    assert bad.error == "unknown method"


def test_message_frames_have_stable_shapes() -> None:
    assert Request(id="1", method="agents.list", params={}).to_frame() == {
        "type": "request",
        "id": "1",
        "method": "agents.list",
        "params": {},
    }
    assert Response(id="1", ok=False, error="boom").to_frame() == {
        "type": "response",
        "id": "1",
        "ok": False,
        "result": None,
        "error": "boom",
    }
    assert Event(
        run_id="abc", agent_id="deep", kind="step", data={"n": 1}
    ).to_frame() == {
        "type": "event",
        "run_id": "abc",
        "agent_id": "deep",
        "kind": "step",
        "data": {"n": 1},
    }
