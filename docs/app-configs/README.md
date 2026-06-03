# App configs

Reference copies of the **client app configurations** that point at this stack's
LiteLLM endpoint (`llm.araxia.cc`). These are the configs that live outside the
repo on each machine (e.g. `~/.config/opencode/`); the copies here are tracked so
the setup is documented, reviewable, and reproducible.

> These are **reference copies, not the live files.** Editing a file here does
> not change the running app — copy it back to the real location (see each
> section). None of these copies contain secrets: API keys are referenced via
> environment variables, never hard-coded.

## ⚠️ Keep these in sync with the main config

**The model lists in these app configs are a downstream mirror of
[`models.yaml`](../../models.yaml).** Whenever you add or remove a model from the
stack, update **every** app config in this folder too — otherwise opencode (and
any other client) will list models that no longer exist, or miss new ones.

When you add/remove a model, the full checklist is:

1. Edit `models.yaml` (the source of truth).
2. `make models-apply` → regenerate `config/llama-swap.yaml`.
3. `make sync-litellm` → add/remove it in LiteLLM.
4. **Update each app config in `docs/app-configs/`** (this folder) — add/remove
   the model entry — **and copy each one back to its live location.**

The set of models a client should list = the **enabled, non-image** entries in
`models.yaml` (i.e. `engine: llama-server`, not `sd-server`). Image-generation
models (`sdxl`, `cyberrealistic-pony`) are not chat models and must be omitted
from chat-client configs like opencode.

## Tracked configs

| App | File here | Live location | Notes |
|-----|-----------|---------------|-------|
| [opencode](https://opencode.ai) | [`opencode.jsonc`](./opencode.jsonc) | `~/.config/opencode/opencode.jsonc` | All coding-capable models. See below. |

---

## opencode

[`opencode.jsonc`](./opencode.jsonc) registers the LiteLLM endpoint as a custom
provider (`@ai-sdk/openai-compatible`) and lists every coding-capable model.

### Setup

1. Copy the file to the live location:
   ```bash
   cp docs/app-configs/opencode.jsonc ~/.config/opencode/opencode.jsonc
   ```
2. Export the API key in the shell that launches opencode (see the env-var note
   below):
   ```bash
   export OPENCODE_LITELLM_API_KEY="<your-litellm-key>"
   ```
3. Launch `opencode`; models appear as `litellm/<model-name>`.

### Gotchas (learned the hard way)

- **Use `https://`, not `http://`, for `baseURL`.** `llm.araxia.cc` answers
  `http` with a `308` redirect to `https`. HTTP clients **drop the
  `Authorization` header across a cross-scheme redirect**, so the key never
  reaches LiteLLM and you get `Authentication Error, No api key passed in.` —
  which looks like a missing key but is actually the redirect eating it.
- **`limit` needs both `context` *and* `output`.** opencode's schema rejects a
  `limit` with only `context` (`SchemaError: Missing key … ["limit"]["output"]`),
  and that failure cascades into `4 of 5 requests failed` at startup.
- **Keep `output` modest — it's a runaway throttle.** `output` is the per-response
  `max_tokens`. On a single-GPU stack a too-high cap is dangerous: a rambling
  model generates to the cap, and decode *also slows sharply at high context*
  (e.g. the 35B drops from ~79 t/s to ~21 t/s at 131k), so a 16k response can take
  10+ min and outlast client timeouts. We cap at **16384**. Pair with LiteLLM
  `num_retries: 0` (see [`scripts/sync-litellm.py`](../../scripts/sync-litellm.py))
  so a timed-out call isn't re-fired. See
  [the investigation](../investigations/qwen3.6-35b-high-context.md).
- **Keep client `limit.context` *below* the server's `-c`.** If they're equal,
  prompt + generation can tip over `n_ctx` mid-stream (`Context size has been
  exceeded`). We leave headroom (e.g. 35B server `-c 262144`, client `229376`).
- **The key must come from `options.apiKey`.** For a custom `npm` provider,
  opencode does **not** auto-apply a key stored via `/connect` (auth.json). Use
  `"apiKey": "{env:OPENCODE_LITELLM_API_KEY}"` in `options`.
- **The env var must be visible to the shell that launches opencode.** Putting
  `export OPENCODE_LITELLM_API_KEY=…` only in `~/.profile` is not enough — GUI
  terminals usually start *non-login interactive* shells, which source
  `~/.bashrc` (not `~/.profile`). Put the export where your interactive shell
  reads it (`~/.bashrc`), or have `~/.bashrc` source `~/.profile`. If the var is
  missing at launch, `{env:…}` resolves to an empty string and you're back to the
  `No api key passed in.` error.

### Model entry fields

Each model carries the capabilities opencode needs to drive it correctly:

- `tool_call` — `true` for agentic models; **`false` for `qwen2.5-coder-32b-q4`**
  (top code-gen but emits tool calls as plain JSON, so it's chat/edit-only).
- `reasoning` — `true` for the `-thinking` / reasoning models (exposes the
  reasoning stream).
- `limit.context` — set *below* the server's `-c` (headroom; see the gotcha above).
- `limit.output` — capped at **16384** (runaway throttle; see the gotcha above).

### Model routing (agents)

opencode has no content-based auto-router, but it routes by **agent**, which is
enough to keep the slow heavy-thinker out of the hot loop:

```jsonc
"model": "litellm/qwen3-coder-30b",   // default — the build/execute agent
"agent": {
  "plan": { "model": "litellm/qwen3.6-35b-a3b-q4-thinking" }  // read-only planning
}
```

- **`build`** (default agent: runs commands, edits, the tight agentic loop) → a
  fast, concise coder (`qwen3-coder-30b`, ~83 t/s). Ordinary "run these commands"
  work uses this **automatically** — no manual switching.
- **`plan`** (opencode's read-only planning agent, toggled with Tab) → the 35B
  thinker, for deep reasoning where slowness is tolerable (no command loop).

Why this matters: a heavy default-thinker in the build loop generates long
reasoning every turn *and* decodes slowly as context fills — see
[the investigation](../investigations/qwen3.6-35b-high-context.md).
