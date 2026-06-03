# Investigation: Qwen3.6-35B-A3B at high context in agentic (opencode) use

**Status:** 🔬 Active — started 2026-06-03
**Model:** `qwen3.6-35b-a3b-q4-thinking` (hybrid Gated-DeltaNet + MoE, A3B, thinking-by-default)
**Client:** opencode → LiteLLM → llama-swap → llama-server (SYCL, Arc Pro B70 32 GB)

## Symptom

After bumping the model's context to its native **262144 (256K)**, real agentic
opencode sessions become unusable:

- Individual `POST /v1/chat/completions` run **6–14 minutes** each.
- Some fail mid-stream with `litellm.MidStreamFallbackError: … Context size has
  been exceeded` (llama-server `n_past` reached `n_ctx=262144` *during*
  generation).
- Earlier (before the per-request fixes) a single stuck request also pegged the
  GPU for ~20 min via a LiteLLM retry storm — see [[gpu-lock-from-runaway-generation-and-litellm-retries]].

The user's own inputs are tiny ("run these commands"), so the prompt is **not**
being exhausted by user text — the context fills from elsewhere.

## Evidence

### llama-swap request durations (2026-06-03)
```
POST /v1/chat/completions  500  14m19s   ← "context exceeded" mid-stream
POST /v1/chat/completions  500  14m18s
POST /v1/chat/completions  500  10m13s
POST /v1/chat/completions  200  10m23s   ← succeeded, but 10 min
POST /v1/chat/completions  200   6m08s
```

### Architecture (from GGUF metadata)
- `context_length=262144`, `block_count=40`, `full_attention_interval=4` →
  **10 full-attention (KV-bearing) layers + 30 DeltaNet/SSM (fixed-state) layers**.
- `head_count_kv=2`, `key/value_length=256`. f16 KV ≈ 2.5 GiB @131k, 5.0 GiB @262k.
- At 262k: ~26.7 GB VRAM total (fits the 32 GB B70).

### Decode speed vs context depth (llama-bench tg, SYCL0, -ngl 99)
| depth (n_past) | decode t/s |
|---:|---:|
| 0 | _pending_ |
| 32768 | _pending_ |
| 131072 | _pending_ |
| 229376 | _pending_ |

_(benchmark running 2026-06-03; fills in the decode falloff that explains the
multi-minute requests.)_

## Hypotheses

1. **Context fills from the model's own output, not user input.** It's a heavy
   default-thinker; each turn it generates up to the output cap (much of it
   `<think>` reasoning) and opencode keeps that in history (possibly preserving
   reasoning across turns), so after a few turns the window approaches 262k.
2. **Decode slows sharply at high context** (attention over a near-full KV, even
   with only 10 attention layers), so a single response takes 10+ min.
3. **Zero headroom between client and server.** opencode `limit.context` was set
   == server `-c` (262144), so prompt + generation can tip over `n_ctx`
   mid-stream → "context exceeded".
4. **256K fits in VRAM but is impractical for interactive agentic use** with this
   model: it fills fast and decodes slowly. "Fits" ≠ "usable".

## Experiment log

| # | Date | Change | Result |
|---|------|--------|--------|
| 1 | 06-03 | Bump server `ctx` 131072 → 262144; verified loads at 26.7 GB, replies OK on a trivial prompt | Loads fine; trivial prompts fast. Real agentic sessions then showed the 6–14 min / context-exceeded failures. |
| 2 | 06-03 | opencode `limit.output` 32768 → 16384 (runaway throttle); LiteLLM `num_retries: 0` + `timeout: 900` | Stopped the retry-storm GPU lock. Did **not** fix slow/long generations at high context. |
| 3 | 06-03 | opencode (live) `limit.context` 262144 → 229376 for the 35B (7/8 headroom) | Partial — gives margin against the hard "context exceeded" crash, but does nothing for decode slowness or the model over-generating. Likely not the real fix. |
| 4 | _next_ | Quantify decode-vs-depth (llama-bench) | _running_ |

## Current config state (⚠️ drift to reconcile)

- **Server** (`models.yaml` on `main`): `qwen3.6-35b-a3b` `ctx: 262144`.
- **Live** opencode (`~/.config/opencode/opencode.jsonc`): 35B `context: 229376`,
  all outputs `16384`.
- **Tracked** opencode (`docs/app-configs/opencode.jsonc` on `main`): 35B
  `context: 131072`, outputs `32768` — **stale**. The 256K-bump and 16384-cap
  commits were pushed to the `docs/app-configs` branch *after* PR #6 had already
  merged, so they never reached `main` (orphaned on the branch). Reconcile once
  this investigation settles on a final value.

## Candidate fixes (to test)

- **Lower the 35B's interactive context** to a value that decodes fast and
  compacts sooner (candidates: 65536 / 98304 / 131072). Keep 262144 available
  server-side for batch use if wanted.
- **Lower the output cap further** (16384 → 8192) to bound per-turn generation and
  slow context growth — at some truncation risk for a reasoner.
- **Steer agentic command-running to a faster, non-rambling model**
  (`qwen3-coder-30b` ~83 t/s, or `qwen3.6-27b`) rather than a verbose thinker.
- **Anti-degeneration sampling** (e.g. `--repeat-penalty` / DRY) to curb the
  rambling/looping the A3B is prone to (we saw an emoji-repeat loop earlier).
- **Check whether opencode preserves `reasoning_content` across turns** — if so,
  that's a major context amplifier for a thinking model and worth disabling.

## Open questions

- How fast does decode actually fall off with depth on this hybrid model? (exp #4)
- Is the context filling from preserved reasoning, accumulated tool output, or
  just long answers? (need `/slots` or `/metrics` — currently disabled; consider
  enabling `--slots`/`--metrics` on llama-server for visibility.)
- Does `qwen3.6-27b` (dense, non-thinking) avoid the rambling and stay fast?

## Conclusion

_TBD — update when the candidate fixes are tested._
