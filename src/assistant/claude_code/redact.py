"""Secret screening for anything Claude Code shows that Sani will display or keep.

Claude Code can read ``.env`` files and print tokens. Step details are shown in
the UI and persisted as technical details, so they pass the same screening as
logs, plus the credential shapes a coding tool is likely to meet.
"""

from __future__ import annotations

import re

from assistant.observability.logging import redact as _base_redact

_REDACTED = "[REDACTED]"

_PATTERNS = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b"),
    re.compile(r"\bey[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
    re.compile(r"(?i)\b(secret|private)[_-]?key\b(\s*[=:]\s*)\S+"),
)


def screen(text: str, *, limit: int = 2000) -> str:
    """Redact credential-shaped text and cap its length."""
    cleaned = _base_redact(text)
    for pattern in _PATTERNS:
        if pattern.groups >= 2:
            cleaned = pattern.sub(lambda m: f"{m.group(1)}{m.group(2)}{_REDACTED}", cleaned)
        else:
            cleaned = pattern.sub(_REDACTED, cleaned)
    if len(cleaned) > limit:
        cleaned = cleaned[:limit].rstrip() + "…"
    return cleaned
