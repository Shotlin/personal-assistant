"""Build provenance: what code is actually running in this process.

The incident this answers (master plan section 2): the installed core's
checksum differed from both the workspace build and its release manifest, so
nobody could say which code had produced the recorded behaviour. Every run
result now carries this identity, and the release build writes it -- the
answer to "which build am I talking to" comes from the build, not from
guesswork around timestamps.

Sources, in priority order: ``SANI_SOURCE_REVISION``/``SANI_BUILT_AT``
environment (set when the sidecar is launched), then ``build-info.json``
beside the executable (written at freeze/release time). Unknown stays
unknown -- an empty field is reported as empty, never filled in with a
plausible-looking guess.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

PROTOCOL_VERSION = 1

_CACHE: dict[str, Any] | None = None


def _from_build_info_file() -> dict[str, Any]:
    try:
        candidate = os.path.join(os.path.dirname(sys.executable), "build-info.json")
        with open(candidate, encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def engine_identity() -> dict[str, Any]:
    """The running build's identity: source revision, build time, protocol."""
    global _CACHE
    if _CACHE is not None:
        return dict(_CACHE)
    identity = {
        "protocol": PROTOCOL_VERSION,
        "revision": os.environ.get("SANI_SOURCE_REVISION", ""),
        "built_at": os.environ.get("SANI_BUILT_AT", ""),
    }
    if not identity["revision"]:
        file_info = _from_build_info_file()
        identity["revision"] = str(file_info.get("revision", ""))
        if not identity["built_at"]:
            identity["built_at"] = str(file_info.get("built_at", ""))
    _CACHE = identity
    return dict(identity)


__all__ = ["PROTOCOL_VERSION", "engine_identity"]
