# Second Arc Pro B70: capacity/concurrency win, and vLLM vs llama.cpp

**Date:** 2026-06-04 · **Status:** Research / decision-deferred (revisit later)

**Question.** Is it worth dropping a **second Arc Pro B70** (Battlemage G31, 32 GB)
into the existing single-B70 SYCL server (→ 64 GB total), and does **vLLM**
serve a 2-GPU Arc box better than our current **llama.cpp + llama-swap**?

**Method.** Deep-research fan-out: 6 angles, 23 sources fetched, 105 claims
extracted, 25 adversarially verified (20 confirmed, 5 killed). Votes below are
verifier tallies (e.g. `3-0` = 3 confirm, 0 refute). Sources + dates inline.

---

## TL;DR

The vLLM hunch is **half right**: vLLM beats llama.cpp on Arc **only under real
concurrency**, which a solo/small-team box rarely sustains. On **single-stream
decode llama.cpp wins** (~59 vs ~13.85 t/s), and vLLM's tensor-parallel TP=2 on
dual-B70 is **not turn-key today** (open crash bug #41663). So the 2nd B70 is
worth it mainly as a **capacity + two-instance-concurrency** play —
**stay on llama.cpp/llama-swap** unless we become genuinely throughput-bound.

---

## 1. llama.cpp multi-GPU on Arc = layer-split only (no tensor parallelism)

- SYCL backend supports `-sm none` and `-sm layer`; **`-sm row` is unsupported
  and segfaults on dual-B70.** *(3-0 — [llama.cpp SYCL.md](https://github.com/ggml-org/llama.cpp/blob/master/docs/backend/SYCL.md),
  [PMZFX dual-B70 benchmarks](https://github.com/PMZFX/intel-arc-pro-b70-benchmarks) 2026-04-21)*
- **Implication:** two cards = **capacity, not a faster single model.** Layer-split
  spreads a big model across both cards' VRAM, but only one card computes per token,
  so a model that already fits on one B70 does **not** decode faster when split.
- Real dual-B70 layer-split numbers (PMZFX, 2026-04-21):
  **Qwen3-Coder-80B-A3B → 43.4 t/s**, **Llama-3.3-70B → 11.5 t/s** — models that
  *don't* fit on one 32 GB card. That capacity is the win.
- **Refuted:** "llama.cpp can't shard a single model across GPUs at all" → **false (0-3)**.
  It can (layer-split); it just isn't tensor-parallel.
- **Refuted:** "must set `GGML_SYCL_DISABLE_OPT=1` or output is corrupted on B70" →
  **did not survive (1-2)**. Not a blanket requirement.

## 2. vLLM on Arc B-series: strong under load, fragile at TP=2

- Intel's `llm-scaler-vllm` fork officially supports Arc B-series with **TP / PP / DP**,
  PagedAttention, continuous batching. *(3-0 — [vLLM/Intel blog 2025-11-11](https://blog.vllm.ai/2025/11/11/intel-arc-pro-b.html),
  [intel/llm-scaler README](https://github.com/intel/llm-scaler/blob/main/vllm/README.md))*
- Scales hard with concurrency: **~1495 t/s** GPT-OSS-120B at TP=4, 100 concurrent
  requests (4× B-series). *(3-0)*
- **Catch for us:** default **TP=2 on dual-B70 crashes** — [vLLM issue #41663](https://github.com/vllm-project/vllm/issues/41663)
  **OPEN (2026-05-04)**; only fragile env-var workarounds run it, ~362 t/s @ 50-concurrency. *(3-0)*
- **Refuted (not as bad as claimed):** "TP=2 fails completely / GPF" → **0-3** (workarounds exist);
  "llm-scaler-vllm is demo-only, not production" → **0-3** (more mature than that).
  Net: **fragile, not broken.**

## 3. The deciding numbers: single-stream vs concurrent

| Scenario | llama.cpp (SYCL) | vLLM (XPU) |
|---|---|---|
| **Single request** (solo coding) | **~59 t/s** ✅ | ~13.85 t/s |
| **High concurrency** (50–100 reqs) | flat (no batching) | **~362–1495 t/s** ✅ |

*(single-stream 2-1; concurrency 3-0)* — vLLM's per-request overhead only amortizes
when many requests overlap. **Below ~1–4 concurrent requests vLLM never helps, and
llama.cpp is faster.** The "vLLM 6–14× faster on prefill" claim **did not survive (1-2)** —
don't bank on a prefill blowout.

## 4. The 2nd B70 itself

**Pros**
- **64 GB total** → run 70–80B models (layer-split) the single card can't hold
  (Qwen3-Coder-80B @ 43 t/s is genuinely usable).
- **2× concurrent throughput the easy way:** run **two llama-swap instances, one
  pinned per card** (`SYCL0` / `SYCL1`) → two models hot at once, no vLLM, no TP
  fragility. Likely the biggest practical win for our workflow.
- Keeps the whole `models.yaml` → `make models-apply` → llama-swap → LiteLLM-sync
  pipeline intact.

**Cons / unverified**
- A single mid-size model **won't get faster** (layer-split ≠ tensor-parallel).
- **Power (~250 W), PCIe lane/slot, and cost were NOT substantiated** by surviving
  sources — verify against the board's 2nd x16 slot and PSU headroom before buying.
- vLLM's TP=2 path to actually speed up *one* model is the fragile one (#41663).

## 5. Recommendation (deferred — revisit)

1. **The 2nd B70 is worth it** — as **capacity + two-instance concurrency**, not
   "make my model 2× faster." It buys 70–80B models *or* two resident models (one/card).
2. **Stay on llama.cpp + llama-swap.** Wins single-stream (our real usage), keeps
   hot-swapping, and two-instances-one-per-card gives concurrency without vLLM's
   fragility. The "vLLM does better" intuition holds **only for sustained multi-user
   concurrency**, which a solo/small-team box rarely reaches.
3. **Hybrid is the later escape hatch:** if one model becomes hot *and* concurrent
   (e.g. exposing the coder to a team), add vLLM as a **side-service for just that
   model** while llama-swap handles the rest — watching #41663 and VRAM contention.

## Caveats

- Strongest vLLM numbers are from the **24 GB B60, not the 32 GB B70**, and are
  **vendor benchmarks on MoE models** → optimistic ceilings.
- Dual-B70 figures are **hobbyist-sourced** (PMZFX) → direction holds, exact numbers soft.
- **High time-sensitivity:** the Intel XPU stack moves monthly; the TP=2 crash
  (#41663) could be fixed any week and would materially improve vLLM's case —
  **re-check before buying.**

## Addendum (2026-06-04): measured opencode concurrency — it's serial

One open question below was *"what concurrency does our workload actually reach?"* —
since vLLM only helps above ~1–4 concurrent requests. We measured it directly from
the llama-swap logs during a live opencode coding session.

**Method.** Parsed 60 consecutive `POST /v1/chat/completions` from the llama-swap
log, reconstructing each request's start (`completion_timestamp − duration`) and
checking whether any request began before the previous one finished.

**Result: 60 requests, 0 overlaps.** Every request starts *after* the prior one
completes, with a consistent ~250–350 ms gap:

```
...ends 19:32:14.280 → next starts 19:32:14.531   (251 ms gap)
...ends 19:32:16.555 → next starts 19:32:16.799   (244 ms gap)
requests: 60 | concurrent (overlapping) starts: 0
```

That gap is opencode executing the turn's tool calls locally (bash/read/edit) and
assembling the next message. The agentic loop is strictly **send → generate → run
tools → send next** — one in-flight LLM request at a time, by design. Even an
assistant turn with *multiple* tool calls runs those tools locally and only fires
the next LLM request once they all return.

**Implication — vLLM is the wrong tool for this workload.** With zero concurrency
there is nothing for vLLM's continuous batching to batch; we'd simply inherit its
*worse* single-stream decode (~13.85 vs ~59 t/s). The long requests in the trace
(72 s, 120 s) are single big-prompt **prefills** (the plan-execution latency), not
concurrency — and vLLM wouldn't parallelize one request (its prefill-advantage
claim was the one this report *refuted*, 1-2).

**Two further notes.**
- The current llama-server runs **`-np 1`** (single slot — no `--parallel` flag),
  so even if opencode *did* fire concurrent requests they would queue and serialize
  anyway. Today, concurrency = queuing = worse latency.
- Concurrency would only appear from **parallel subagents**, **multiple
  sessions/users**, or aux title/summary calls (none overlapped here). The cheap
  first lever for *light* concurrency is **`-np 2`** on the single B70 (2 slots,
  128K KV → 2×64K) — not vLLM, and not a 2nd card.

This is concrete backing for the recommendation above: **single-stream serial is
our real pattern → stay on llama.cpp + llama-swap.** vLLM remains a "sustained
many-request load" play we don't currently reach.

## Open questions for the revisit

- Real measured throughput of a *stable* vLLM TP=2 on two B70 under realistic concurrency?
- For a model fitting one 32 GB card, do two independent llama.cpp/llama-swap instances
  beat one TP vLLM for two concurrent users?
- What concurrency does our workload actually reach? (below 1–4 reqs, vLLM batching is moot)
- Does a hybrid cause VRAM contention when both backends target the same two cards?

## Key sources

- [llama.cpp SYCL.md](https://github.com/ggml-org/llama.cpp/blob/master/docs/backend/SYCL.md) — split-mode support (primary)
- [PMZFX/intel-arc-pro-b70-benchmarks](https://github.com/PMZFX/intel-arc-pro-b70-benchmarks) — dual-B70 layer-split numbers, 2026-04-21 (hobbyist)
- [vLLM/Intel blog](https://blog.vllm.ai/2025/11/11/intel-arc-pro-b.html), 2025-11-11 — Arc B-series TP/PP/DP + 1495 t/s (primary)
- [vLLM issue #41663](https://github.com/vllm-project/vllm/issues/41663) — TP=2 crash, OPEN 2026-05-04 (primary)
- [intel/llm-scaler vllm README](https://github.com/intel/llm-scaler/blob/main/vllm/README.md) — fork scope/maturity (primary)
- [EmbeddedLLM B60 benchmarks](https://embeddedllm.com/blog/benchmarking-llm-inference-intel-arc-pro-b60) — vLLM Arc throughput (primary, B60 not B70)
