#!/usr/bin/env python3
"""Black-box validator for OpenAI-style tool/function calling.

Runs a battery of /v1/chat/completions probes against a llama-swap endpoint
and reports per-model pass/fail. Designed to surface chat-template / tool-call
parsing problems in the underlying llama.cpp servers.

Usage:
    python test_tools.py [model ...]

If no model is specified, models are auto-discovered via /v1/models.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

import httpx


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DEFAULT_URL = os.environ.get("LLAMA_SWAP_URL", "http://localhost:11434").rstrip("/")
COLD_TIMEOUT = float(os.environ.get("COLD_TIMEOUT_SECS", "120"))
WARM_TIMEOUT = float(os.environ.get("WARM_TIMEOUT_SECS", "60"))
TOOL_MAX_TOKENS = int(os.environ.get("TOOL_TEST_MAX_TOKENS", "256"))
FINAL_MAX_TOKENS = int(os.environ.get("TOOL_TEST_FINAL_TOKENS", "128"))


# Reusable tool definitions ---------------------------------------------------

WEATHER_TOOL = {
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "Get the current weather for a city.",
        "parameters": {
            "type": "object",
            "properties": {
                "city": {
                    "type": "string",
                    "description": "Name of the city, e.g. 'Paris'",
                },
            },
            "required": ["city"],
        },
    },
}

TIME_TOOL = {
    "type": "function",
    "function": {
        "name": "get_time",
        "description": "Get the current local time in a timezone.",
        "parameters": {
            "type": "object",
            "properties": {
                "timezone": {
                    "type": "string",
                    "description": "IANA timezone, e.g. 'Europe/Paris'",
                },
            },
            "required": ["timezone"],
        },
    },
}


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class TestResult:
    name: str
    passed: bool
    advisory: bool = False  # if True, failure does not affect overall exit code
    detail: str = ""
    raw: Any = None  # raw response (dict or string) on failure for diagnostics


@dataclass
class ModelReport:
    model: str
    results: list[TestResult] = field(default_factory=list)

    @property
    def required_failed(self) -> int:
        return sum(1 for r in self.results if not r.passed and not r.advisory)

    @property
    def advisory_failed(self) -> int:
        return sum(1 for r in self.results if not r.passed and r.advisory)

    @property
    def passed_count(self) -> int:
        return sum(1 for r in self.results if r.passed)


# ---------------------------------------------------------------------------
# HTTP client
# ---------------------------------------------------------------------------

class LlamaSwapClient:
    """Thin wrapper around /v1/chat/completions with cold/warm timeouts."""

    def __init__(self, base_url: str):
        self.base_url = base_url
        self._warm: set[str] = set()
        self._client = httpx.Client(timeout=httpx.Timeout(COLD_TIMEOUT))

    def close(self) -> None:
        self._client.close()

    def _timeout_for(self, model: str) -> float:
        return WARM_TIMEOUT if model in self._warm else COLD_TIMEOUT

    def list_models(self) -> list[str]:
        r = self._client.get(f"{self.base_url}/v1/models", timeout=WARM_TIMEOUT)
        r.raise_for_status()
        data = r.json()
        return [m["id"] for m in data.get("data", [])]

    def chat(self, model: str, payload: dict) -> tuple[int, dict | str]:
        body = dict(payload)
        body["model"] = model
        timeout = self._timeout_for(model)
        try:
            r = self._client.post(
                f"{self.base_url}/v1/chat/completions",
                json=body,
                timeout=timeout,
            )
        except httpx.HTTPError as e:
            return 0, f"transport error: {e!r}"
        self._warm.add(model)
        ct = r.headers.get("content-type", "")
        if "application/json" in ct:
            try:
                return r.status_code, r.json()
            except json.JSONDecodeError:
                return r.status_code, r.text
        return r.status_code, r.text

    def chat_stream(self, model: str, payload: dict) -> tuple[int, list[dict], str]:
        """POST with stream:true. Returns (status, parsed_chunks, raw_text)."""
        body = dict(payload)
        body["model"] = model
        body["stream"] = True
        timeout = self._timeout_for(model)
        chunks: list[dict] = []
        raw_lines: list[str] = []
        try:
            with self._client.stream(
                "POST",
                f"{self.base_url}/v1/chat/completions",
                json=body,
                timeout=timeout,
            ) as r:
                self._warm.add(model)
                if r.status_code != 200:
                    text = r.read().decode("utf-8", errors="replace")
                    return r.status_code, [], text
                for line in r.iter_lines():
                    if not line:
                        continue
                    raw_lines.append(line)
                    if line.startswith("data: "):
                        data = line[len("data: "):]
                        if data.strip() == "[DONE]":
                            break
                        try:
                            chunks.append(json.loads(data))
                        except json.JSONDecodeError:
                            pass
                return 200, chunks, "\n".join(raw_lines)
        except httpx.HTTPError as e:
            return 0, [], f"transport error: {e!r}"


# ---------------------------------------------------------------------------
# Helpers for response inspection
# ---------------------------------------------------------------------------

def _first_choice_message(resp: dict) -> dict | None:
    if not isinstance(resp, dict):
        return None
    choices = resp.get("choices")
    if not isinstance(choices, list) or not choices:
        return None
    msg = choices[0].get("message")
    return msg if isinstance(msg, dict) else None


def _tool_calls(resp: dict) -> list[dict]:
    msg = _first_choice_message(resp)
    if not msg:
        return []
    tcs = msg.get("tool_calls")
    return tcs if isinstance(tcs, list) else []


def _parse_args(tc: dict) -> dict | None:
    fn = tc.get("function") or {}
    args = fn.get("arguments")
    if isinstance(args, dict):
        return args
    if isinstance(args, str):
        try:
            return json.loads(args)
        except json.JSONDecodeError:
            return None
    return None


def _short(raw: Any, limit: int = 1500) -> str:
    if isinstance(raw, (dict, list)):
        text = json.dumps(raw, ensure_ascii=False)
    else:
        text = str(raw)
    if len(text) > limit:
        return text[:limit] + f"... <truncated, total {len(text)} chars>"
    return text


# ---------------------------------------------------------------------------
# Individual tests
# ---------------------------------------------------------------------------

def test_basic_tool_call(client: LlamaSwapClient, model: str) -> tuple[TestResult, dict | None]:
    """The model should emit a get_weather(city='Paris') tool call."""
    payload = {
        "messages": [
            {"role": "user", "content": "What's the weather in Paris right now?"},
        ],
        "tools": [WEATHER_TOOL],
        "max_tokens": TOOL_MAX_TOKENS,
        "temperature": 0.0,
    }
    status, resp = client.chat(model, payload)
    if status != 200 or not isinstance(resp, dict):
        return TestResult(
            name="basic_tool_call",
            passed=False,
            detail=f"http {status}",
            raw=resp,
        ), None

    tcs = _tool_calls(resp)
    if not tcs:
        msg = _first_choice_message(resp) or {}
        return TestResult(
            name="basic_tool_call",
            passed=False,
            detail=(
                "no tool_calls in response; "
                f"finish_reason={resp.get('choices',[{}])[0].get('finish_reason')!r}; "
                f"content={msg.get('content')!r}"
            ),
            raw=resp,
        ), None

    first = tcs[0]
    fn_name = (first.get("function") or {}).get("name")
    if fn_name != "get_weather":
        return TestResult(
            name="basic_tool_call",
            passed=False,
            detail=f"expected function get_weather, got {fn_name!r}",
            raw=resp,
        ), None

    args = _parse_args(first)
    if args is None:
        return TestResult(
            name="basic_tool_call",
            passed=False,
            detail="function.arguments did not parse as JSON",
            raw=resp,
        ), None

    city = args.get("city")
    if not isinstance(city, str) or city.strip().lower() != "paris":
        return TestResult(
            name="basic_tool_call",
            passed=False,
            detail=f"expected city=Paris (case-insensitive), got {city!r}",
            raw=resp,
        ), None

    return TestResult(name="basic_tool_call", passed=True), resp


def test_tool_result_roundtrip(
    client: LlamaSwapClient,
    model: str,
    prior_response: dict | None,
) -> TestResult:
    """Send back a fake tool result; expect a final assistant message that uses it."""
    if prior_response is None:
        return TestResult(
            name="tool_result_roundtrip",
            passed=False,
            detail="skipped: basic_tool_call did not produce a usable response",
        )

    msg = _first_choice_message(prior_response) or {}
    tcs = msg.get("tool_calls") or []
    if not tcs:
        return TestResult(
            name="tool_result_roundtrip",
            passed=False,
            detail="prior response lacked tool_calls",
            raw=prior_response,
        )
    call_id = tcs[0].get("id") or "call_0"

    # Reconstruct the assistant message exactly as the server returned it,
    # then append the tool result.
    assistant_msg = {
        "role": "assistant",
        "content": msg.get("content") or "",
        "tool_calls": tcs,
    }
    payload = {
        "messages": [
            {"role": "user", "content": "What's the weather in Paris right now?"},
            assistant_msg,
            {
                "role": "tool",
                "tool_call_id": call_id,
                "name": "get_weather",
                "content": "sunny, 20C",
            },
        ],
        "tools": [WEATHER_TOOL],
        "max_tokens": FINAL_MAX_TOKENS,
        "temperature": 0.0,
    }
    status, resp = client.chat(model, payload)
    if status != 200 or not isinstance(resp, dict):
        return TestResult(
            name="tool_result_roundtrip",
            passed=False,
            detail=f"http {status}",
            raw=resp,
        )

    final_msg = _first_choice_message(resp) or {}
    content = final_msg.get("content") or ""
    # If the model just emitted another tool call instead of synthesizing an
    # answer, that's a fail too.
    if final_msg.get("tool_calls"):
        return TestResult(
            name="tool_result_roundtrip",
            passed=False,
            detail="model emitted another tool_call instead of using the tool result",
            raw=resp,
        )
    if not content.strip():
        return TestResult(
            name="tool_result_roundtrip",
            passed=False,
            detail="final assistant content is empty",
            raw=resp,
        )

    lower = content.lower()
    # Be lenient: the model can paraphrase. Look for any signal it used the tool result.
    signals = ["sunny", "20", "warm", "clear", "weather"]
    if not any(s in lower for s in signals):
        return TestResult(
            name="tool_result_roundtrip",
            passed=False,
            detail=(
                "final answer does not appear to incorporate the tool result; "
                f"content={content!r}"
            ),
            raw=resp,
        )

    return TestResult(name="tool_result_roundtrip", passed=True)


def test_multi_tool_selection(client: LlamaSwapClient, model: str) -> TestResult:
    payload = {
        "messages": [
            {
                "role": "user",
                "content": "What time is it in Tokyo right now? Use the appropriate tool.",
            },
        ],
        "tools": [WEATHER_TOOL, TIME_TOOL],
        "max_tokens": TOOL_MAX_TOKENS,
        "temperature": 0.0,
    }
    status, resp = client.chat(model, payload)
    if status != 200 or not isinstance(resp, dict):
        return TestResult(
            name="multi_tool_selection",
            passed=False,
            detail=f"http {status}",
            raw=resp,
        )

    tcs = _tool_calls(resp)
    if not tcs:
        return TestResult(
            name="multi_tool_selection",
            passed=False,
            detail="no tool_calls emitted",
            raw=resp,
        )
    name = (tcs[0].get("function") or {}).get("name")
    if name != "get_time":
        return TestResult(
            name="multi_tool_selection",
            passed=False,
            detail=f"expected get_time, got {name!r}",
            raw=resp,
        )
    return TestResult(name="multi_tool_selection", passed=True)


def test_no_tool_path(client: LlamaSwapClient, model: str) -> TestResult:
    payload = {
        "messages": [
            {"role": "user", "content": "What is 2+2? Just answer with the number."},
        ],
        "tools": [WEATHER_TOOL, TIME_TOOL],
        "max_tokens": FINAL_MAX_TOKENS,
        "temperature": 0.0,
    }
    status, resp = client.chat(model, payload)
    if status != 200 or not isinstance(resp, dict):
        return TestResult(
            name="no_tool_path",
            passed=False,
            detail=f"http {status}",
            raw=resp,
        )

    msg = _first_choice_message(resp) or {}
    if msg.get("tool_calls"):
        return TestResult(
            name="no_tool_path",
            passed=False,
            detail="model invoked a tool when none was needed",
            raw=resp,
        )
    content = (msg.get("content") or "").strip()
    if not content:
        return TestResult(
            name="no_tool_path",
            passed=False,
            detail="empty content and no tool_calls",
            raw=resp,
        )
    if "4" not in content:
        return TestResult(
            name="no_tool_path",
            passed=False,
            detail=f"answer did not contain '4'; content={content!r}",
            raw=resp,
        )
    return TestResult(name="no_tool_path", passed=True)


def test_required_tool_choice(client: LlamaSwapClient, model: str) -> TestResult:
    """tool_choice='required' should force a tool call, even on an ambiguous prompt."""
    payload = {
        "messages": [
            {"role": "user", "content": "Hello, please look up something useful for me."},
        ],
        "tools": [WEATHER_TOOL],
        "tool_choice": "required",
        "max_tokens": TOOL_MAX_TOKENS,
        "temperature": 0.0,
    }
    status, resp = client.chat(model, payload)
    if status != 200 or not isinstance(resp, dict):
        return TestResult(
            name="required_tool_choice",
            passed=False,
            detail=f"http {status}",
            raw=resp,
        )
    tcs = _tool_calls(resp)
    if not tcs:
        return TestResult(
            name="required_tool_choice",
            passed=False,
            detail="tool_choice=required did not produce a tool_call",
            raw=resp,
        )
    name = (tcs[0].get("function") or {}).get("name")
    if name != "get_weather":
        return TestResult(
            name="required_tool_choice",
            passed=False,
            detail=f"expected get_weather (only tool offered), got {name!r}",
            raw=resp,
        )
    return TestResult(name="required_tool_choice", passed=True)


def test_streaming_tool_call(client: LlamaSwapClient, model: str) -> TestResult:
    payload = {
        "messages": [
            {"role": "user", "content": "What's the weather in Paris?"},
        ],
        "tools": [WEATHER_TOOL],
        "max_tokens": TOOL_MAX_TOKENS,
        "temperature": 0.0,
    }
    status, chunks, raw = client.chat_stream(model, payload)
    if status != 200:
        return TestResult(
            name="streaming_tool_call",
            passed=False,
            detail=f"http {status}",
            raw=raw,
        )

    # Assemble tool_calls from deltas. Index -> {name, args_buf, id}
    assembled: dict[int, dict[str, str]] = {}
    finish_reason: str | None = None
    for chunk in chunks:
        choices = chunk.get("choices") or []
        if not choices:
            continue
        ch = choices[0]
        delta = ch.get("delta") or {}
        for tc in delta.get("tool_calls") or []:
            idx = tc.get("index", 0)
            slot = assembled.setdefault(idx, {"name": "", "args": "", "id": ""})
            if tc.get("id"):
                slot["id"] = tc["id"]
            fn = tc.get("function") or {}
            if fn.get("name"):
                slot["name"] += fn["name"]
            if fn.get("arguments"):
                slot["args"] += fn["arguments"]
        if ch.get("finish_reason"):
            finish_reason = ch["finish_reason"]

    if not assembled:
        return TestResult(
            name="streaming_tool_call",
            passed=False,
            detail=(
                "no tool_call deltas observed in stream; "
                f"finish_reason={finish_reason!r}; chunks={len(chunks)}"
            ),
            raw=raw,
        )

    slot = assembled[min(assembled.keys())]
    if slot["name"] != "get_weather":
        return TestResult(
            name="streaming_tool_call",
            passed=False,
            detail=f"streamed function name was {slot['name']!r}",
            raw=raw,
        )
    try:
        args = json.loads(slot["args"]) if slot["args"] else {}
    except json.JSONDecodeError:
        return TestResult(
            name="streaming_tool_call",
            passed=False,
            detail=f"streamed arguments did not parse: {slot['args']!r}",
            raw=raw,
        )
    city = args.get("city")
    if not isinstance(city, str) or city.strip().lower() != "paris":
        return TestResult(
            name="streaming_tool_call",
            passed=False,
            detail=f"streamed city was {city!r}",
            raw=raw,
        )
    return TestResult(name="streaming_tool_call", passed=True)


def test_parallel_tool_calls(client: LlamaSwapClient, model: str) -> TestResult:
    """Advisory: many models/quants can't reliably emit two tool_calls at once."""
    payload = {
        "messages": [
            {
                "role": "user",
                "content": (
                    "What's the weather in Paris AND Tokyo? "
                    "Call the tool once for each city."
                ),
            },
        ],
        "tools": [WEATHER_TOOL],
        "max_tokens": TOOL_MAX_TOKENS,
        "temperature": 0.0,
    }
    status, resp = client.chat(model, payload)
    if status != 200 or not isinstance(resp, dict):
        return TestResult(
            name="parallel_tool_calls",
            passed=False,
            advisory=True,
            detail=f"http {status}",
            raw=resp,
        )
    tcs = _tool_calls(resp)
    if len(tcs) < 2:
        # Did the model at least emit one tool_call? That's the "acceptable
        # degradation" path — still mark as failed (advisory).
        return TestResult(
            name="parallel_tool_calls",
            passed=False,
            advisory=True,
            detail=(
                f"model emitted {len(tcs)} tool_call(s); "
                "expected 2 (advisory only — serial round-tripping is acceptable)"
            ),
            raw=resp,
        )
    cities = []
    for tc in tcs:
        args = _parse_args(tc) or {}
        c = args.get("city")
        if isinstance(c, str):
            cities.append(c.strip().lower())
    if "paris" in cities and "tokyo" in cities:
        return TestResult(name="parallel_tool_calls", passed=True, advisory=True)
    return TestResult(
        name="parallel_tool_calls",
        passed=False,
        advisory=True,
        detail=f"two tool_calls emitted but cities were {cities!r}",
        raw=resp,
    )


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run_for_model(client: LlamaSwapClient, model: str) -> ModelReport:
    print(f"\n=== {model} ===", flush=True)
    report = ModelReport(model=model)

    def record(result: TestResult) -> None:
        flag = "PASS" if result.passed else ("WARN" if result.advisory else "FAIL")
        suffix = f" — {result.detail}" if result.detail else ""
        print(f"  [{flag}] {result.name}{suffix}", flush=True)
        report.results.append(result)

    # 1. basic + 2. roundtrip (roundtrip depends on the prior response)
    t0 = time.time()
    basic, basic_resp = test_basic_tool_call(client, model)
    record(basic)
    print(f"        (cold/first-request elapsed {time.time()-t0:.1f}s)", flush=True)

    record(test_tool_result_roundtrip(client, model, basic_resp))
    record(test_multi_tool_selection(client, model))
    record(test_no_tool_path(client, model))
    record(test_required_tool_choice(client, model))
    record(test_streaming_tool_call(client, model))
    record(test_parallel_tool_calls(client, model))

    return report


def print_summary(reports: list[ModelReport]) -> None:
    print("\n" + "=" * 78)
    print("SUMMARY")
    print("=" * 78)

    test_names = [
        "basic_tool_call",
        "tool_result_roundtrip",
        "multi_tool_selection",
        "no_tool_path",
        "required_tool_choice",
        "streaming_tool_call",
        "parallel_tool_calls",  # advisory
    ]

    # Header
    name_col = max(8, max((len(r.model) for r in reports), default=8))
    header = f"{'model'.ljust(name_col)}  " + "  ".join(
        n[:5].ljust(5) for n in test_names
    )
    print(header)
    print("-" * len(header))

    for rep in reports:
        cells = []
        result_by_name = {r.name: r for r in rep.results}
        for n in test_names:
            r = result_by_name.get(n)
            if r is None:
                cells.append("-".ljust(5))
            elif r.passed:
                cells.append("PASS ")
            elif r.advisory:
                cells.append("warn ")
            else:
                cells.append("FAIL ")
        print(f"{rep.model.ljust(name_col)}  " + "  ".join(cells))

    print()
    print("Legend: PASS ok | FAIL required test failed | warn advisory failure")
    print("Advisory tests: parallel_tool_calls")
    print()

    # Diagnostics for failures
    any_diag = False
    for rep in reports:
        for r in rep.results:
            if r.passed:
                continue
            if not any_diag:
                print("=" * 78)
                print("FAILURE DETAILS")
                print("=" * 78)
                any_diag = True
            kind = "advisory" if r.advisory else "required"
            print(f"\n[{rep.model}] {r.name} ({kind})")
            print(f"  detail: {r.detail}")
            if r.raw is not None:
                print(f"  raw   : {_short(r.raw)}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Tool-use validator for llama-swap")
    parser.add_argument(
        "models",
        nargs="*",
        help="Model name(s) to test. If omitted, all models from /v1/models are used.",
    )
    parser.add_argument(
        "--url",
        default=DEFAULT_URL,
        help=f"Base URL for the llama-swap endpoint (default {DEFAULT_URL})",
    )
    args = parser.parse_args()

    client = LlamaSwapClient(args.url)
    try:
        if args.models:
            models = args.models
        else:
            try:
                models = client.list_models()
            except httpx.HTTPError as e:
                print(f"error: failed to list models from {args.url}: {e!r}",
                      file=sys.stderr)
                return 2
            if not models:
                print(f"error: no models discovered at {args.url}", file=sys.stderr)
                return 2

        print(f"endpoint   : {args.url}")
        print(f"models     : {', '.join(models)}")
        print(f"timeouts   : cold={COLD_TIMEOUT}s warm={WARM_TIMEOUT}s")
        print(f"max_tokens : tool={TOOL_MAX_TOKENS} final={FINAL_MAX_TOKENS}")

        reports = []
        for m in models:
            try:
                reports.append(run_for_model(client, m))
            except Exception as e:  # pragma: no cover — never let one model kill the run
                print(f"  [FAIL] runner crash on {m}: {e!r}", flush=True)
                rep = ModelReport(model=m)
                rep.results.append(
                    TestResult(name="runner", passed=False, detail=f"crash: {e!r}")
                )
                reports.append(rep)

        print_summary(reports)

        any_required_failed = any(rep.required_failed > 0 for rep in reports)
        return 1 if any_required_failed else 0
    finally:
        client.close()


if __name__ == "__main__":
    sys.exit(main())
