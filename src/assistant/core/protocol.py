"""Framed JSON transport and typed messages for sani-core (Sani master doc 13/14).

Private IPC over stdin/stdout: every frame is a 4-byte big-endian length
prefix followed by exactly that many bytes of UTF-8 JSON. There is
deliberately no localhost web server anywhere in this stack.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import orjson

MAX_FRAME_BYTES = 1024 * 1024


class ProtocolError(Exception):
    """A frame violated the wire contract: oversize, truncated, or not JSON."""


async def read_frame(reader: asyncio.StreamReader) -> dict[str, Any] | None:
    """Read one framed JSON object; return None only on clean EOF between frames."""
    try:
        header = await reader.readexactly(4)
    except asyncio.IncompleteReadError as exc:
        if not exc.partial:
            return None
        raise ProtocolError(f"truncated frame header: got {len(exc.partial)} of 4 bytes") from exc
    length = int.from_bytes(header, "big")
    if length > MAX_FRAME_BYTES:
        raise ProtocolError(f"frame too large: {length} bytes > {MAX_FRAME_BYTES}")
    try:
        payload = await reader.readexactly(length)
    except asyncio.IncompleteReadError as exc:
        raise ProtocolError(
            f"truncated frame body: got {len(exc.partial)} of {length} bytes"
        ) from exc
    try:
        frame = orjson.loads(payload)
    except orjson.JSONDecodeError as exc:
        raise ProtocolError(f"invalid JSON frame: {exc}") from exc
    if not isinstance(frame, dict):
        raise ProtocolError("frame must be a JSON object")
    return frame


async def write_frame(writer: asyncio.StreamWriter, payload: Mapping[str, Any]) -> None:
    """Serialize one payload with a length prefix and drain.

    orjson only serializes real dicts, so the Mapping is copied first.
    Refusing to send an oversize frame keeps both peers on one contract.
    """
    body = orjson.dumps(dict(payload))
    if len(body) > MAX_FRAME_BYTES:
        raise ProtocolError(f"frame too large to send: {len(body)} bytes > {MAX_FRAME_BYTES}")
    writer.write(len(body).to_bytes(4, "big") + body)
    await writer.drain()


@dataclass(frozen=True, slots=True)
class Request:
    id: str
    method: str
    params: dict[str, Any]

    def to_frame(self) -> dict[str, Any]:
        return {"type": "request", "id": self.id, "method": self.method, "params": self.params}


@dataclass(frozen=True, slots=True)
class Response:
    id: str
    ok: bool
    result: Any = None
    error: str = ""

    def to_frame(self) -> dict[str, Any]:
        return {
            "type": "response",
            "id": self.id,
            "ok": self.ok,
            "result": self.result,
            "error": self.error,
        }


@dataclass(frozen=True, slots=True)
class Event:
    run_id: str
    kind: str
    data: dict[str, Any]

    def to_frame(self) -> dict[str, Any]:
        return {"type": "event", "run_id": self.run_id, "kind": self.kind, "data": self.data}


def response_from_request(request: Request, result: Any) -> Response:
    return Response(id=request.id, ok=True, result=result)


def error_from_request(request: Request, error: str) -> Response:
    return Response(id=request.id, ok=False, error=error)
