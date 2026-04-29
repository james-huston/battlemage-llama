# Tool-use test suite

Black-box validator that probes the llama-swap endpoint to confirm whether each
served model implements OpenAI-style tool / function calling. The suite hits
`/v1/chat/completions` directly (no SDK) so what you see is exactly what the
server emits.

## Running

```bash
# All models, default endpoint (http://localhost:11434)
./tests/tool_use/run.sh

# A single model
./tests/tool_use/run.sh qwen3-coder-30b

# A different endpoint
LLAMA_SWAP_URL=http://some-host:11434 ./tests/tool_use/run.sh
```

`run.sh` handles venv creation and pip install on first run.

### Environment knobs

| Variable | Default | Purpose |
|---|---|---|
| `LLAMA_SWAP_URL` | `http://localhost:11434` | Base URL of the OpenAI-compatible endpoint |
| `COLD_TIMEOUT_SECS` | `120` | Timeout for the first request to a model (cold-start) |
| `WARM_TIMEOUT_SECS` | `60` | Timeout for subsequent requests to the same model |
| `TOOL_TEST_MAX_TOKENS` | `256` | `max_tokens` cap on tool-call requests |
| `TOOL_TEST_FINAL_TOKENS` | `128` | `max_tokens` cap on final-answer requests |

Exit code is non-zero if any **required** test fails. `parallel_tool_calls`
is advisory-only.

## What each test checks

| Test | What it sends | What we expect |
|---|---|---|
| `basic_tool_call` | "What's the weather in Paris?" + `get_weather` tool | `choices[0].message.tool_calls[0].function.name == "get_weather"`; arguments parse as JSON; `city` ~ `"Paris"` |
| `tool_result_roundtrip` | Replays the tool call and feeds back `role: tool` content `"sunny, 20C"` | A final assistant message with non-empty content that references the tool result (sunny / 20 / weather / etc.) |
| `multi_tool_selection` | Two tools (`get_weather`, `get_time`); asks only for the time | Model picks `get_time` |
| `no_tool_path` | Tools provided but asks "What is 2+2?" | Plain text answer containing `4`, no `tool_calls` |
| `required_tool_choice` | `tool_choice: "required"`, ambiguous prompt | Model emits a tool call anyway |
| `streaming_tool_call` | Same as `basic_tool_call` but `stream: true` | The assembled `delta.tool_calls` stream produces `get_weather(city=Paris)` |
| `parallel_tool_calls` | "Weather in Paris AND Tokyo?" | Two tool calls in one response. **Advisory** — many quants/templates serialize this; failures here only emit a `warn` and don't affect exit code |

## Interpreting failures

The most common reason a Qwen / Llama family model fails the basic tool-call
test on a llama-swap stack is that the server isn't using a Jinja chat
template that emits structured tool calls. Symptoms and fixes:

- **`basic_tool_call` returns content like `<tool_call>{...}` or `<json>{...}` instead of `tool_calls`** — the server is using its built-in default template (or no template at all) and llama.cpp can't parse the model's tool-call syntax back into structured JSON. **Fix:** add `--jinja` to the `llama-server` command line in `config/llama-swap.yaml`. For models whose embedded chat template doesn't support tools, additionally pass `--chat-template-file` pointing at a tool-aware template (see `models/templates/` in upstream llama.cpp, or use `--chat-template <name>` for a built-in like `qwen2.5-coder`). After editing the config, llama-swap will hot-reload it; the next request to the model triggers a respawn.
- **`tool_result_roundtrip` fails but `basic_tool_call` passes** — the chat template isn't rendering `role: tool` messages correctly back into the prompt, so the model doesn't see the tool output. Same fix: `--jinja` and a tool-aware template.
- **`streaming_tool_call` fails but `basic_tool_call` passes** — llama.cpp's streaming tool-call parser is sensitive to the exact tag format. Check the `llama-server` version (`/v1/models` won't tell you, but `docker compose logs llama-swap` will print the build). Newer builds parse `<tool_call>...</tool_call>` style streams correctly.
- **`required_tool_choice` fails** — older llama.cpp builds didn't honor `tool_choice: "required"`. Upgrade llama.cpp.
- **`parallel_tool_calls` warns** — usually fine; many small quants emit one tool call at a time and call again after seeing the result. Only worry about it if your downstream client (LiteLLM, etc.) actually relies on parallel calls.
- **`no_tool_path` fails because the model called a tool anyway** — the model is over-eager. Often a prompt-engineering or temperature issue rather than a server config issue. The suite uses `temperature=0` to minimize this.
- **A "thinking" model (e.g. `qwen3-30b-a3b`) returns empty `content` and `finish_reason: length`** — it ran out of `max_tokens` while still inside its `reasoning_content` and never got to produce visible output. Re-run with a higher `TOOL_TEST_FINAL_TOKENS` (e.g. `512` or `1024`) for these models. The suite keeps the default low so the run finishes quickly on non-thinking models.

## Files

- `run.sh` — bootstrap venv, install deps, run `test_tools.py`
- `test_tools.py` — the actual test runner
- `requirements.txt` — pinned dependencies (`httpx`)
- `.venv/` — created by `run.sh`, gitignored

## Notes

- We deliberately do **not** use the OpenAI SDK; the suite is meant to surface
  raw server behavior including malformed responses.
- `temperature` is pinned to `0.0` for reproducibility.
- The first request to any given model uses the cold timeout (default 120s)
  to absorb llama-swap's spin-up of the underlying `llama-server` process.
