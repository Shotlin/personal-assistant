"""Unit tests for MCP result normalization (master plan WP2)."""

from types import SimpleNamespace

import pytest

from assistant.tools.result_normalizer import (
    ImageRef,
    normalize_mcp_result,
)


def _result(content=None, structured=None, is_error=False):
    return SimpleNamespace(
        isError=is_error,
        content=content if content is not None else [],
        structuredContent=structured,
    )


def test_text_only_result_becomes_bounded_model_content():
    outcome = normalize_mcp_result(
        _result([SimpleNamespace(type="text", text="Display is 42")], {"display_value": "42"})
    )
    assert outcome.status == "ok"
    assert outcome.structured["display_value"] == "42"
    assert outcome.model_content(allow_images=False) == [
        {"type": "text", "text": "Display is 42"}
    ]


def test_structured_content_is_required_field():
    outcome = normalize_mcp_result(_result([], {"effect": "confirmed"}))
    assert outcome.effect == "confirmed"


def test_effect_defaults_to_not_applicable():
    outcome = normalize_mcp_result(_result([SimpleNamespace(type="text", text="ok")], None))
    assert outcome.effect == "not_applicable"


def test_image_content_becomes_local_reference_not_model_upload():
    image = SimpleNamespace(type="image", data="aGVsbG8=", mimeType="image/png")
    outcome = normalize_mcp_result(_result([image], None))
    refs = outcome.images
    assert len(refs) == 1
    assert isinstance(refs[0], ImageRef)
    assert refs[0].data_base64 == "aGVsbG8="
    # images are NOT part of model content unless explicitly allowed
    assert all(part.get("type") != "image" for part in outcome.model_content(allow_images=False))


def test_model_content_with_images_uses_multimodal_blocks():
    image = SimpleNamespace(type="image", data="aGVsbG8=", mimeType="image/png")
    outcome = normalize_mcp_result(_result([image], None))
    blocks = outcome.model_content(allow_images=True)
    assert any(block.get("type") == "image" for block in blocks)
    assert any(block.get("data") == "aGVsbG8=" for block in blocks)


def test_error_result_maps_to_failed_status_with_text():
    outcome = normalize_mcp_result(
        _result([SimpleNamespace(type="text", text="window_id=1 elements=0")], None, is_error=True)
    )
    assert outcome.status == "failed"
    assert "window_id=1" in outcome.model_content(allow_images=False)[0]["text"]


def test_oversized_text_is_bounded_for_the_model():
    big = "x" * 50000
    outcome = normalize_mcp_result(_result([SimpleNamespace(type="text", text=big)], None))
    text = outcome.model_content(allow_images=False)[0]["text"]
    assert len(text) < 5000
    assert outcome.truncated is True


def test_malformed_structured_content_is_tolerated():
    outcome = normalize_mcp_result(_result([], "not-a-dict"))
    assert outcome.structured == {}


@pytest.mark.parametrize(
    "payload",
    [
        SimpleNamespace(),  # no content/structuredContent at all
        "plain string result",
        None,
    ],
)
def test_unexpected_payload_shapes_do_not_crash(payload):
    outcome = normalize_mcp_result(payload)
    assert outcome.status in {"ok", "failed", "unknown"}


def _tuple_result(content, structured, is_error=False):
    """Real shape from this mcp lib's ClientSession.call_tool: a tuple.

    Verified live against cua-driver 0.28.2 (2026-09-17): first slot is a
    list of typed content dicts, second slot is a metadata dict holding
    the structured payload under 'structured_content'.
    """
    return (
        content if content is not None else [],
        {"structured_content": structured} if structured is not None else {},
    )


def test_tuple_result_structured_content_is_promoted():
    outcome = normalize_mcp_result(
        _tuple_result(
            [{"type": "text", "text": "found 8 windows"}],
            {"apps": [{"bundle_id": "com.google.Chrome", "name": "Google Chrome", "pid": 1081}]},
        )
    )
    assert outcome.status == "ok"
    assert outcome.structured.get("apps"), "tuple metadata slot must surface as structured evidence"


def test_tuple_result_text_items_are_extracted():
    outcome = normalize_mcp_result(
        _tuple_result([{"type": "text", "text": "window_id=1783 pid=47060"}], {})
    )
    assert "window_id=1783" in outcome.text
    assert outcome.status == "ok"


def test_tuple_result_image_items_are_captured():
    outcome = normalize_mcp_result(
        _tuple_result([{"type": "image", "id": "x", "base64": "aGVsbG8="}], {})
    )
    assert len(outcome.images) == 1
    assert outcome.images[0].data_base64 == "aGVsbG8="
