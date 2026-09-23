"""Frozen entry point for Sani's private core IPC sidecar.

The executable is packaged with the application. It intentionally delegates to
the existing ``assistant.core`` module so the wire protocol and agent registry
remain identical to development mode.
"""

from __future__ import annotations

import asyncio

from assistant.core.__main__ import main


if __name__ == "__main__":
    asyncio.run(main())
