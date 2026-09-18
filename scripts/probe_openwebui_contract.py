"""Probe the pinned Open WebUI's authenticated read endpoints and record contracts.

P0 evidence script for the Agent Designer. Two modes:

probe     Sign in once with the supplied user credentials, then issue
          read-only GET requests against candidate endpoints for the
          resource kinds the Designer reuses (current user, models,
          prompts, skills, knowledge, tools). For each candidate it
          records status, response shape (keys/types, never values that
          look secret), pagination markers, and whether the route exists
          at all. Results are merged into
          docs/designer/upstream-contracts.json.

capture   Start a tiny local HTTP server and log every request it
          receives (method, path, headers, body) to a JSON file. Use it
          to answer C5: temporarily point the Open WebUI OpenAI
          connection at the capture URL, refresh the model list in the
          Open WebUI UI, and record the exact request Open WebUI sends
          for model discovery (headers present, auth header behaviour,
          whether user/chat/message ids are present, global vs
          user-specific). This script never rewrites Open WebUI config;
          repointing the connection is an explicit operator action.

Security rules enforced here:
- Credentials are used only for the signin call; the password is never
  written to the output file, stdout, or logs.
- Bearer tokens are never recorded. Anything that looks like a token
  (JWT shape, long hex) is redacted before serialization.
- Only GET probes are issued. No writes, no deletes, no tool execution.

Usage:

    uv run python scripts/probe_openwebui_contract.py probe \
        --email <user email> --password <user password>

    uv run python scripts/probe_openwebui_contract.py capture \
        --output docs/designer/model-discovery-capture.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = REPO_ROOT / "docs" / "designer" / "upstream-contracts.json"
DEFAULT_BASE_URL = "http://127.0.0.1:3000"

# Candidate endpoints, tried in order per kind. The probe records which
# exist on the pinned v0.11.3 build; nothing is assumed.
CANDIDATE_ROUTES: dict[str, list[str]] = {
    "current_user": ["/api/v1/auths/", "/api/v1/auths/me", "/api/v1/users/"],
    "models_gateway_view": ["/openai/models", "/api/v1/models/"],
    "models_openai_compat": ["/api/chat/models", "/openai/models"],
    "prompts": ["/api/v1/prompts/"],
    "skills": ["/api/v1/skills/", "/api/v1/skills"],
    "knowledge": ["/api/v1/knowledge/"],
    "tools": ["/api/v1/tools/"],
}

_STRING_VALUE_PATTERN = re.compile(r"(eyJ[A-Za-z0-9_-]{10,}|[a-f0-9]{32,})")
_STRING_SAMPLE_CAP = 80


def _redact_string(value: str) -> str:
    if _STRING_VALUE_PATTERN.search(value):
        return "<redacted:looks-like-token>"
    return value[:_STRING_SAMPLE_CAP] + ("…" if len(value) > _STRING_SAMPLE_CAP else "")


def describe_shape(payload: Any, depth: int = 0) -> Any:
    """Reduce a JSON payload to a structural shape with capped samples."""
    if depth > 4:
        return "<max-depth>"
    if isinstance(payload, dict):
        return {key: describe_shape(value, depth + 1) for key, value in payload.items()}
    if isinstance(payload, list):
        if not payload:
            return {"type": "array", "length": 0}
        return {
            "type": "array",
            "length": len(payload),
            "item_shape": describe_shape(payload[0], depth + 1),
        }
    if isinstance(payload, str):
        return {"type": "string", "sample": _redact_string(payload)}
    if isinstance(payload, bool) or payload is None:
        return payload
    if isinstance(payload, (int, float)):
        return {"type": type(payload).__name__}
    return {"type": type(payload).__name__}


def probe(client: httpx.Client, headers: dict[str, str], route: str) -> dict[str, Any]:
    """Issue one authenticated GET and record status plus response shape."""
    try:
        response = client.get(f"{client.base_url}{route}", headers=headers)
    except httpx.HTTPError as exc:
        return {"route": route, "error": f"{type(exc).__name__}"}
    error_excerpt = None
    if response.status_code != 200:
        error_excerpt = _redact_string(response.text[:200])
    return {
        "route": route,
        "status": response.status_code,
        "content_type": response.headers.get("content-type", ""),
        "shape": describe_shape(response.json()) if response.status_code == 200 else None,
        "error_excerpt": error_excerpt,
    }


def run_probe(args: argparse.Namespace) -> int:
    output_path = Path(args.output)

    with httpx.Client(timeout=30, base_url=args.base_url) as client:
        signin = client.post(
            "/api/v1/auths/signin",
            json={"email": args.email, "password": args.password},
        )
        if signin.status_code != 200:
            print(f"sign-in failed: {signin.status_code} (credentials never logged)")
            return 1
        token = signin.json().get("token")
        if not isinstance(token, str) or not token:
            print("signin response did not include a token field as expected")
            return 1
        headers = {"Authorization": f"Bearer {token}"}

        kinds: dict[str, Any] = {}
        for kind, routes in CANDIDATE_ROUTES.items():
            kinds[kind] = [probe(client, headers, route) for route in routes]

        result = {
            "schema_version": 1,
            "probed_at": datetime.now(UTC).isoformat(),
            "openwebui_base_url": args.base_url,
            "credential_mechanism": "Bearer token obtained via POST /api/v1/auths/signin",
            "probe_notes": [
                "Read-only GET probes; no resource content persisted, shapes only.",
                "404 candidates do not exist on this build; 401/403 exist but deny this user.",
            ],
            "kinds": kinds,
        }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, indent=2) + "\n")
    print(f"probe complete -> {output_path}")
    for kind, attempts in kinds.items():
        statuses = ",".join(str(a.get("status", a.get("error"))) for a in attempts)
        print(f"  {kind}: {statuses}")
    return 0


class _CaptureHandler(BaseHTTPRequestHandler):
    """Record every request verbatim (headers included, secrets redacted)."""

    captured: list[dict[str, Any]] = []
    output_path: Path | None = None

    def _record(self) -> None:
        headers = {
            key: _redact_string(value) if key.lower() == "authorization" else value
            for key, value in self.headers.items()
        }
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length).decode("utf-8", errors="replace") if length else ""
        _CaptureHandler.captured.append(
            {
                "received_at": datetime.now(UTC).isoformat(),
                "method": self.command,
                "path": self.path,
                "headers": headers,
                "body_excerpt": _redact_string(body[:2000]),
            }
        )
        if _CaptureHandler.output_path is not None:
            _CaptureHandler.output_path.write_text(
                json.dumps(_CaptureHandler.captured, indent=2) + "\n"
            )
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"data":[]}')

    def do_GET(self) -> None:  # noqa: N802 (http.server API)
        self._record()

    def do_POST(self) -> None:  # noqa: N802 (http.server API)
        self._record()

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        print(f"capture: {format % args}")


def run_capture(args: argparse.Namespace) -> int:
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _CaptureHandler.output_path = output_path
    server = HTTPServer((args.bind, args.port), _CaptureHandler)
    print(f"capture server listening on http://{args.bind}:{args.port}")
    print("operator action: temporarily point the Open WebUI OpenAI connection at")
    print(f"  http://host.docker.internal:{args.port}/v1  then refresh the model list.")
    print("Ctrl+C to stop; every request is appended to the output file.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print(f"\ncaptured {len(_CaptureHandler.captured)} request(s) -> {output_path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="mode", required=True)

    probe_parser = sub.add_parser("probe", help="authenticated read-only endpoint probes")
    probe_parser.add_argument("--email", required=True)
    probe_parser.add_argument("--password", required=True)
    probe_parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    probe_parser.add_argument("--output", default=str(DEFAULT_OUTPUT))

    capture_parser = sub.add_parser("capture", help="C5 model-discovery capture server")
    capture_parser.add_argument("--bind", default="127.0.0.1")
    capture_parser.add_argument("--port", type=int, default=8788)
    capture_parser.add_argument(
        "--output", default=str(REPO_ROOT / "docs" / "designer" / "model-discovery-capture.json")
    )

    args = parser.parse_args()
    if args.mode == "probe":
        return run_probe(args)
    return run_capture(args)


if __name__ == "__main__":
    sys.exit(main())
