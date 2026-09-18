"""Normalize MCP tool results into typed outcomes (master plan WP2/F02).

The persistent-session wrapper previously returned ``result.content`` only,
dropping ``structuredContent`` (machine-checkable evidence) and converting
image payloads into Python representations. This module defines the single
internal contract:

- ``ToolOutcome.status``: ok / failed / unknown
- ``ToolOutcome.effect``: confirmed / suspected_noop / unverifiable / not_applicable
- ``ToolOutcome.structured``: locally retained machine evidence
- ``ToolOutcome.model_content(allow_images=...)``: bounded blocks for the
  model -- never raw MCP objects, never base64 unless explicitly allowed
- ``ToolOutcome.images``: local image references for artifact retention
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

_TEXT_MODEL_LIMIT_CHARS = 4000


@dataclass(frozen=True)
class ImageRef:
    """A locally retained image artifact (base64 kept out of the prompt)."""

    data_base64: str
    mime_type: str = "image/png"


@dataclass
class ToolOutcome:
    """Normalized result of one CUA tool call."""

    status: str  # ok | failed | unknown
    effect: str  # confirmed | suspected_noop | unverifiable | not_applicable
    text: str = ""
    structured: dict[str, Any] = field(default_factory=dict)
    images: list[ImageRef] = field(default_factory=list)
    truncated: bool = False

    def model_content(self, *, allow_images: bool = False) -> list[dict[str, Any]]:
        """Blocks for the model: bounded text plus (optionally) images."""
        blocks: list[dict[str, Any]] = []
        text = self.text
        if len(text) > _TEXT_MODEL_LIMIT_CHARS:
            text = text[:_TEXT_MODEL_LIMIT_CHARS] + "\n[truncated]"
        if text:
            blocks.append({"type": "text", "text": text})
        if allow_images:
            for image in self.images:
                blocks.append(
                    {
                        "type": "image",
                        "source_type": "base64",
                        "data": image.data_base64,
                        "mime_type": image.mime_type,
                    }
                )
        return blocks

    def summary(self) -> str:
        """One-line factual summary for logs (no payload contents)."""
        image_count = len(self.images)
        return (
            f"status={self.status} effect={self.effect} "
            f"structured_keys={sorted(self.structured)[:5]} images={image_count} "
            f"text_chars={len(self.text)}"
        )


def _content_items(result: Any) -> list[Any]:
    # This mcp lib's ClientSession.call_tool returns a 2-tuple
    # (content items list, metadata dict) rather than a CallToolResult
    # object -- verified live against cua-driver 0.28.2. The metadata
    # dict carries the structured payload under 'structured_content'.
    if isinstance(result, tuple) and len(result) == 2:
        items = result[0]
        if isinstance(items, list):
            return [item for item in items if item is not None]
    content = getattr(result, "content", None)
    if isinstance(content, list):
        return [item for item in content if item is not None]
    if isinstance(content, str):
        return [SimpleText(content)]
    if content is not None:
        return [SimpleText(str(content))]
    return []


class SimpleText:
    """Adapter for plain-string content."""

    def __init__(self, text: str) -> None:
        self.type = "text"
        self.text = text


def normalize_mcp_result(result: Any) -> ToolOutcome:
    """Map any MCP call result (or unexpected shape) to a ToolOutcome."""
    is_error = bool(getattr(result, "isError", False))

    text_parts: list[str] = []
    images: list[ImageRef] = []
    for item in _content_items(result):
        # The tuple transport returns plain dicts, not typed objects.
        if isinstance(item, dict):
            item_type = item.get("type")
        else:
            item_type = getattr(item, "type", None)
        if item_type == "text":
            raw_text = item.get("text", "") if isinstance(item, dict) else getattr(item, "text", "")
            text_parts.append(str(raw_text))
        elif item_type == "image":
            if isinstance(item, dict):
                data = item.get("base64") or item.get("data")
                mime = item.get("mime_type") or item.get("mimeType") or "image/png"
            else:
                data = getattr(item, "data", None)
                mime = str(getattr(item, "mimeType", "image/png"))
            if isinstance(data, str) and data:
                images.append(ImageRef(data_base64=data, mime_type=str(mime)))
        elif isinstance(item, str):
            text_parts.append(item)

    text = "\n".join(part for part in text_parts if part)
    truncated = False
    if len(text) > _TEXT_MODEL_LIMIT_CHARS:
        text = text[:_TEXT_MODEL_LIMIT_CHARS] + "\n[truncated]"
        truncated = True

    structured_raw = getattr(result, "structuredContent", None)
    if structured_raw is None and isinstance(result, tuple) and len(result) == 2:
        metadata = result[1]
        if isinstance(metadata, dict):
            structured_raw = metadata.get("structured_content")
    structured: dict[str, Any]
    if isinstance(structured_raw, dict):
        structured = structured_raw
    elif isinstance(structured_raw, str) and structured_raw:
        try:
            parsed = json.loads(structured_raw)
            structured = parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            structured = {}
    else:
        structured = {}

    if is_error:
        status = "failed"
        effect = "unverifiable"
    else:
        status = "ok"
        effect = str(structured.get("effect", "not_applicable"))
        if effect not in {"confirmed", "suspected_noop", "unverifiable", "not_applicable"}:
            effect = "not_applicable"

    return ToolOutcome(
        status=status,
        effect=effect,
        text=text,
        structured=structured,
        images=images,
        truncated=truncated,
    )
