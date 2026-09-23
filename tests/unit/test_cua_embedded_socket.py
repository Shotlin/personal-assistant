"""The packaged Sani host must never let MCP revive a standard daemon."""

from assistant.settings import Settings
from assistant.tools.cua import _assert_bounded_daemon, driver_mcp_args


def test_embedded_driver_uses_its_private_socket() -> None:
    settings = Settings(
        cua_command="/Applications/Sani.app/Contents/MacOS/cua-driver",
        cua_socket="/Users/example/Library/Application Support/app.sani.local/cua-driver.sock",
    )

    assert driver_mcp_args(settings) == [
        "mcp",
        "--embedded",
        "--socket",
        "/Users/example/Library/Application Support/app.sani.local/cua-driver.sock",
    ]


def test_standalone_development_keeps_its_existing_mcp_command() -> None:
    assert driver_mcp_args(Settings()) == ["mcp"]


async def test_embedded_guard_rejects_a_missing_private_endpoint() -> None:
    settings = Settings(cua_socket="/private/tmp/sani-no-such-cua-driver.sock")
    try:
        await _assert_bounded_daemon(settings)
    except RuntimeError as exc:
        assert "embedded CuaDriver socket is unavailable" in str(exc)
    else:  # pragma: no cover - the test path must remain absent
        raise AssertionError("missing embedded socket was accepted")
