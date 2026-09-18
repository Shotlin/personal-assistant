"""Gateway-path latency benchmark (master plan WP1/section 14).

Measures, for one chat request against the running gateway:
- ack_ms: gateway acceptance -> first non-role SSE content chunk
- verified_completion_ms: -> [DONE]
- usage: provider calls/tokens as reported by the run ledger (from the
  response usage for non-streaming; unknown for streaming until WP6+ adds
  a usage frame).

Usage:
    uv run python scripts/benchmark_latency.py --message "Open Chrome" \
        [--stream] [--runs 3] [--base-url http://127.0.0.1:8787] [--key KEY]

Development env only: uses dev test headers (APP_ENV=development).
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time

import httpx


def _reply_text(body: dict) -> str:
    choices = body.get("choices") or [{}]
    message = choices[0].get("message") or {}
    return str(message.get("content", ""))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--message", default="Open Chrome")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--stream", action="store_true", default=True)
    parser.add_argument("--no-stream", dest="stream", action="store_false")
    parser.add_argument("--base-url", default=os.environ.get("BENCH_GATEWAY", "http://127.0.0.1:8787"))
    parser.add_argument("--key", default=os.environ.get("AGENT_GATEWAY_API_KEY", "change-me"))
    args = parser.parse_args()

    headers = {
        "Authorization": f"Bearer {args.key}",
        "X-Assistant-Dev-User-Id": "bench-user",
        "X-Assistant-Dev-Chat-Id": f"bench-{int(time.time())}",
    }
    rows: list[dict[str, object]] = []

    with httpx.Client(timeout=300) as client:
        for run_index in range(args.runs):
            payload = {
                "model": "personal-assistant-v1",
                "messages": [{"role": "user", "content": args.message}],
                "stream": args.stream,
            }
            started = time.perf_counter()
            ack_ms: float | None = None
            completion_ms: float | None = None
            reply: list[str] = []

            if args.stream:
                with client.stream(
                    "POST",
                    f"{args.base_url}/v1/chat/completions",
                    headers=headers,
                    json=payload,
                ) as response:
                    if response.status_code != 200:
                        body = response.read().decode("utf-8", errors="replace")
                        rows.append(
                            {"run": run_index, "error": response.status_code, "body": body[:200]}
                        )
                        continue
                    for line in response.iter_lines():
                        if not line.startswith("data: "):
                            continue
                        data = line[6:]
                        if data == "[DONE]":
                            completion_ms = (time.perf_counter() - started) * 1000
                            break
                        try:
                            chunk = json.loads(data)
                        except json.JSONDecodeError:
                            continue
                        delta = chunk.get("choices", [{}])[0].get("delta", {})
                        content = delta.get("content")
                        if content and ack_ms is None:
                            ack_ms = (time.perf_counter() - started) * 1000
                        if content:
                            reply.append(content)
            else:
                response = client.post(
                    f"{args.base_url}/v1/chat/completions",
                    headers=headers,
                    json=payload,
                )
                completion_ms = (time.perf_counter() - started) * 1000
                if response.status_code == 200:
                    body = response.json()
                    usage = body.get("usage", {})
                    rows.append(
                        {
                            "run": run_index,
                            "ack_ms": None,
                            "completion_ms": completion_ms,
                            "reply_chars": len(
                                _reply_text(body)
                            ),
                            "input_tokens": usage.get("prompt_tokens"),
                            "output_tokens": usage.get("completion_tokens"),
                        }
                    )
                    continue
                rows.append(
                    {"run": run_index, "error": response.status_code, "body": response.text[:200]}
                )
                continue

            rows.append(
                {
                    "run": run_index,
                    "ack_ms": round(ack_ms, 1) if ack_ms is not None else None,
                    "completion_ms": round(completion_ms, 1) if completion_ms is not None else None,
                    "reply_chars": sum(len(part) for part in reply),
                }
            )

    ack_values: list[float] = [
        float(r["ack_ms"]) for r in rows if isinstance(r.get("ack_ms"), (int, float))
    ]
    completion_values: list[float] = [
        float(r["completion_ms"]) for r in rows if isinstance(r.get("completion_ms"), (int, float))
    ]
    summary = {
        "task": args.message[:60],
        "runs": args.runs,
        "ack_p50_ms": round(statistics.median(ack_values), 1) if ack_values else None,
        "completion_p50_ms": (
            round(statistics.median(completion_values), 1) if completion_values else None
        ),
        "rows": rows,
        "note": "single-sample summary; p95 requires >=20 trials per master plan section 14",
    }
    print(json.dumps(summary, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
