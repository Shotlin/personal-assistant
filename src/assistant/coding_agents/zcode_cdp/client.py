"""A minimal Chrome DevTools Protocol client for the ZCode window.

Only what Sani needs: evaluate a read-only script, click an element with real mouse
events (Radix menus ignore ``element.click()``), press a key, take a screenshot.
It talks to ``127.0.0.1`` only and never to the network.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import urllib.request
from collections import deque
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

import websockets


class CdpError(RuntimeError):
    """A CDP call failed, timed out, or the page raised."""


class Transport(Protocol):
    async def send(self, data: str) -> None: ...

    async def recv(self) -> str: ...

    async def close(self) -> None: ...


Connector = Callable[[str], Awaitable[Transport]]


async def _connect_websocket(url: str) -> Transport:
    if not url.startswith("ws://127.0.0.1:"):
        raise CdpError("refusing a debug address that is not on this Mac (127.0.0.1)")
    return await websockets.connect(url, max_size=32 * 1024 * 1024, open_timeout=10)  # type: ignore[return-value]


def _http_get_json(url: str) -> Any:
    with urllib.request.urlopen(url, timeout=3) as response:  # noqa: S310 - 127.0.0.1 only
        return json.load(response)


async def http_get_json(url: str) -> Any:
    if not url.startswith("http://127.0.0.1:"):
        raise CdpError("refusing a debug address that is not on this Mac (127.0.0.1)")
    return await asyncio.to_thread(_http_get_json, url)


async def find_page_url(
    port: int,
    get: Callable[[str], Awaitable[Any]] = http_get_json,
    *,
    seconds: float = 30.0,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> str:
    """The websocket address of ZCode's one app page (not workers).

    The debug port answers before the window has loaded, so wait for the page to appear.
    """
    waited = 0.0
    while True:
        targets = await get(f"http://127.0.0.1:{port}/json/list")
        for target in targets:
            if target.get("type") == "page" and "renderer/index.html" in str(target.get("url", "")):
                return str(target["webSocketDebuggerUrl"])
        if waited >= seconds:
            raise CdpError("ZCode's window was not found on the debug port")
        await sleep(0.5)
        waited += 0.5


class CdpClient:
    def __init__(self, transport: Transport) -> None:
        self._transport = transport
        self._next_id = 0
        self._waiting: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self.events: deque[dict[str, Any]] = deque(maxlen=200)
        self._reader = asyncio.create_task(self._read_loop())

    @classmethod
    async def open(cls, url: str, connector: Connector = _connect_websocket) -> CdpClient:
        return cls(await connector(url))

    async def _read_loop(self) -> None:
        try:
            while True:
                message = json.loads(await self._transport.recv())
                future = self._waiting.pop(message["id"], None) if "id" in message else None
                if future is not None and not future.done():
                    future.set_result(message)
                elif "method" in message:
                    self.events.append(message)
        except Exception as exc:  # connection closed: wake everyone up
            for future in self._waiting.values():
                if not future.done():
                    future.set_exception(CdpError(f"connection to ZCode closed: {exc}"))
            self._waiting.clear()

    async def call(
        self, method: str, params: dict[str, Any] | None = None, *, timeout: float = 10.0
    ) -> dict[str, Any]:
        self._next_id += 1
        ident = self._next_id
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._waiting[ident] = future
        await self._transport.send(
            json.dumps({"id": ident, "method": method, "params": params or {}})
        )
        try:
            message = await asyncio.wait_for(future, timeout)
        except TimeoutError as exc:
            self._waiting.pop(ident, None)
            raise CdpError(f"{method} timed out after {timeout:g}s") from exc
        if "error" in message:
            raise CdpError(f"{method}: {message['error'].get('message', 'failed')}")
        result = message.get("result", {})
        return result if isinstance(result, dict) else {}

    async def evaluate(self, expression: str, *, timeout: float = 10.0) -> Any:
        result = await self.call(
            "Runtime.evaluate",
            {"expression": expression, "returnByValue": True, "awaitPromise": True},
            timeout=timeout,
        )
        if "exceptionDetails" in result:
            detail = result["exceptionDetails"]
            text = (detail.get("exception") or {}).get("description") or detail.get("text")
            raise CdpError(f"the page raised: {str(text)[:200]}")
        return (result.get("result") or {}).get("value")

    async def click(self, selector: str) -> None:
        """Scroll the element into view and click its centre with real mouse events.

        Mouse events at coordinates outside the window land on nothing, so a control in a
        long, scrolled sidebar has to be brought into view first.
        """
        box = await self.evaluate(
            "(()=>{const e=document.querySelector(" + json.dumps(selector) + ");if(!e)return null;"
            "e.scrollIntoView({block:'center',inline:'center'});"
            "const b=e.getBoundingClientRect();"
            "return b.width&&b.height?[b.x+b.width/2,b.y+b.height/2]:null})()"
        )
        if not box:
            raise CdpError(f"nothing to click for {selector}")
        x, y = box
        for kind in ("mouseMoved", "mousePressed", "mouseReleased"):
            await self.call(
                "Input.dispatchMouseEvent",
                {
                    "type": kind,
                    "x": x,
                    "y": y,
                    "button": "left",
                    "clickCount": 1,
                    "buttons": 1 if kind == "mousePressed" else 0,
                },
            )

    async def press(self, key: str) -> None:
        codes = {"Escape": 27, "Enter": 13}
        for kind in ("keyDown", "keyUp"):
            await self.call(
                "Input.dispatchKeyEvent",
                {
                    "type": kind,
                    "key": key,
                    "code": key,
                    "windowsVirtualKeyCode": codes.get(key, 0),
                },
            )

    async def type_text(self, text: str) -> None:
        """Insert text into the focused field as if pasted (newlines stay line breaks)."""
        await self.call("Input.insertText", {"text": text}, timeout=20.0)

    async def screenshot(self) -> bytes:
        import base64

        result = await self.call("Page.captureScreenshot", {"format": "png"}, timeout=15.0)
        return base64.b64decode(str(result.get("data", "")))

    async def close(self) -> None:
        self._reader.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await self._reader
        with contextlib.suppress(Exception):
            await self._transport.close()
