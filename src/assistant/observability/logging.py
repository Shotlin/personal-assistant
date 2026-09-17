"""Structured JSON logging with secret redaction (spec section 18).

Rules enforced here:
- one JSON object per log line (orjson),
- API keys, tokens, passwords, and bearer headers are redacted,
- log level is configurable via ``LOG_LEVEL`` / ``config/logging.yaml``.
"""

from __future__ import annotations

import logging
import logging.config
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import orjson

DEFAULT_CONFIG_PATH = Path("config/logging.yaml")
_REDACTED = "[REDACTED]"

# sk-... style provider keys
_SK_KEY_RE = re.compile(r"sk-[A-Za-z0-9_-]{8,}")
# key=value / key: value for secret-ish key names
_KV_SECRET_RE = re.compile(
    r"(?i)\b(api[_-]?key|apikey|token|password|passwd|secret|otp)\b(\s*[=:]\s*)\S+"
)
# Authorization headers
_BEARER_RE = re.compile(r"(?i)(bearer\s+)\S+")


def redact(message: str) -> str:
    """Return ``message`` with secret-like content replaced by [REDACTED]."""
    text = _SK_KEY_RE.sub(_REDACTED, message)
    text = _KV_SECRET_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}{_REDACTED}", text)
    text = _BEARER_RE.sub(lambda m: f"{m.group(1)}{_REDACTED}", text)
    return text


class RedactionFilter(logging.Filter):
    """Apply secret redaction to every record passing through the handler."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(record.getMessage())
        record.args = None
        return True


_DEFAULT_RECORD_ATTRS = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "module",
        "msecs",
        "message",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "stack_print",
        "taskName",
        "thread",
        "threadName",
    }
)


class JsonFormatter(logging.Formatter):
    """Format records as single-line JSON with redaction applied."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": redact(record.getMessage()),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        for key, value in record.__dict__.items():
            if key in _DEFAULT_RECORD_ATTRS or key.startswith("_"):
                continue
            if isinstance(value, str | int | float | bool) or value is None:
                payload[key] = value if not isinstance(value, str) else redact(value)
        return orjson.dumps(payload).decode("utf-8")


def setup_logging(level: str = "INFO", config_path: Path | None = DEFAULT_CONFIG_PATH) -> None:
    """Configure root logging from YAML when available, else from code."""
    if config_path is not None and config_path.is_file():
        import yaml

        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            raw.setdefault("version", 1)
            raw.setdefault("disable_existing_loggers", False)
            logging.config.dictConfig(raw)
            logging.getLogger().setLevel(level.upper())
            return

    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RedactionFilter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())
