"""sani-core entry point (Sani master doc 13/14).

The process is spawned by the Tauri host and speaks framed JSON over
stdin/stdout. stdout is the IPC channel, so every log line goes to
stderr only; there is deliberately no localhost web server.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import sys
from typing import cast

from assistant.core.app import SaniCoreApp

logger = logging.getLogger("assistant.core")

_STDIN_CHUNK_BYTES = 65536


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def _pump_stdin_from_thread(reader: asyncio.StreamReader) -> None:
    """Feed the reader from a worker thread (platforms without connect_read_pipe).

    Constraint: os.read on the raw fd, never BufferedReader.read -- read()
    would block until the full chunk arrives and stall small frames.
    """
    loop = asyncio.get_running_loop()

    async def _pump() -> None:
        while True:
            try:
                chunk = await asyncio.to_thread(os.read, 0, _STDIN_CHUNK_BYTES)
            except OSError:
                logger.exception("sani-core: stdin pump failed")
                break
            if not chunk:
                break
            loop.call_soon_threadsafe(reader.feed_data, chunk)
        loop.call_soon_threadsafe(reader.feed_eof)

    asyncio.create_task(_pump(), name="sani-core-stdin-pump")


class _BlockingStdoutWriter:
    """StreamWriter stand-in over sys.stdout for platforms without connect_write_pipe.

    Constraint: drain() flushes synchronously; the Tauri host must keep
    reading stdout (it does -- these frames are the host's input).
    """

    def write(self, data: bytes) -> None:
        sys.stdout.buffer.write(data)

    def is_closing(self) -> bool:
        return False

    async def drain(self) -> None:
        sys.stdout.buffer.flush()

    def close(self) -> None:
        with contextlib.suppress(Exception):
            sys.stdout.buffer.flush()

    async def wait_closed(self) -> None:
        return None


def _pipe_protocol() -> asyncio.StreamReaderProtocol:
    return asyncio.StreamReaderProtocol(asyncio.StreamReader())


async def _connect_stdio() -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader()
    try:
        await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(reader), sys.stdin.buffer)
    except (NotImplementedError, OSError):
        logger.warning("sani-core: connect_read_pipe unavailable; using stdin thread pump")
        _pump_stdin_from_thread(reader)
    try:
        transport, protocol = await loop.connect_write_pipe(_pipe_protocol, sys.stdout.buffer)
        writer: asyncio.StreamWriter = asyncio.StreamWriter(transport, protocol, None, loop)
    except (NotImplementedError, OSError):
        logger.warning("sani-core: connect_write_pipe unavailable; using blocking stdout writer")
        writer = cast(asyncio.StreamWriter, _BlockingStdoutWriter())
    return reader, writer


async def main() -> None:
    _configure_logging()
    from assistant.core.agents import build_default_registry, build_status_provider
    from assistant.settings import Settings

    settings = Settings()
    reader, writer = await _connect_stdio()
    registry = build_default_registry(settings)
    status_provider = build_status_provider(settings)
    await SaniCoreApp(registry, status_provider=status_provider).serve(reader, writer)
    logger.info("sani-core: stdin EOF; exiting cleanly")


if __name__ == "__main__":
    asyncio.run(main())
