# Investigation: Gemma 4 (12B / 26B-A4B / 31B) setup & tuning on the B70

**Status:** 🔬 Active — started 2026-06-04
**Hardware:** Arc Pro B70 (32 GB, SYCL, Q4_K_M, `--device SYCL0 -sm none`).
**Models:** Google Gemma 4, added to `models.yaml` 2026-06-04.

Tracks the **starting values** for each Gemma 4 model and **every change** we make
as we benchmark and tune — so the reasoning is recorded, not lost. Applies the
lessons from the [Qwen3.6-35B high-context investigation](qwen3.6-35b-high-context.md).

## The family (verified from unsloth GGUF repos)

| Model | Arch | Quant / VRAM | Native ctx | Released |
|---|---|---|---|---|
| `gemma-4-12b` | dense, multimodal (text+image+**audio**, encoder-free) | Q4_K_M / 7.1 GB | 256K | 2026-06-03 |
| `gemma-4-26b-a4b` | **MoE, 4B active** / 26B loaded; "advanced reasoning" | UD-Q4_K_M / 16.9 GB | 256K | 2026-03-31 |
| `gemma-4-31b` | dense (flagship) | Q4_K_M / 18.3 GB | 256K | 2026-03-31 |

All use **hybrid attention** — interleaved local sliding-window (512/1024) +
global attention, final layer always global → cheap KV at long context (similar
to Qwen3.6's hybrid DeltaNet). All ship a **draft model for speculative decoding**
(future optimization). All multimodal (mmproj) but **run text-only here**.

## Starting values (2026-06-04)

| Model | ctx | temp | top_k | top_p | min_p | sampler order | tools | reasoning |
|---|---|---|---|---|---|---|---|---|
| gemma-4-12b | 65536 | 1.0 | 64 | 0.95 | 0.0 | temperature;top_p;top_k | ❓ off | ❓ off |
| gemma-4-26b-a4b | 65536 | 1.0 | 64 | 0.95 | 0.0 | temperature;top_p;top_k | ❓ off | ❓ off |
| gemma-4-31b | 32768 | 1.0 | 64 | 0.95 | 0.0 | temperature;top_p;top_k | ❓ off | ❓ off |

### Why these values (Qwen lessons applied)

- **Sampling is Gemma-specific, NOT Qwen's.** Gemma docs want **temp 1.0, top_k 64,
  top_p 0.95, min_p 0** with sampler order `temperature;top_p;top_k`. (Qwen used
  temp 0.6 — copying it here would be wrong.) The order is set explicitly because
  Gemma is order-sensitive. Passed via `extra:` (no spaces in the samplers token,
  so no llama-swap arg-split/quoting crash — unlike the Qwen `--chat-template-kwargs`
  JSON issue).
- **ctx started MODERATE, not 256K.** The 35B investigation showed decode falls off
  a cliff with context depth and the window fills from the model's own output.
  Sliding-window attention makes Gemma's KV cheaper, so we'll likely *raise* ctx —
  but only after the decode-vs-context bench (exp 2), not blindly.
- **Dense 31B gets the lowest ctx (32k).** Dense decode is slower and KV bigger
  (cf. we capped the dense Qwen 27B lower than the A3B MoE).
- **Tools/reasoning start OFF, to be verified.** Gemma has historically been
  *prompt-based* for function calling (emits JSON in content, not structured
  tool_calls — the failure mode that sank Qwen2.5-Coder). Don't assume; run the
  tool-use suite and flip only if it passes. Same for a deepseek-style think
  channel on the 26B "reasoning" model.
- **VRAM headroom:** the 12B (7 GB) and 26B (17 GB) leave lots of room on the 32 GB
  B70 — Q5_K_M/Q6_K are easy quality bumps if wanted. 31B Q5_K_M (21.7 GB) also fits.

## Verification plan

- [ ] **exp 1 — load + smoke:** download, `make models-apply`, load each on SYCL0,
      confirm `/v1` responds with Gemma sampling.
- [ ] **exp 2 — decode-vs-context bench:** `llama-bench` tg at depths
      (0/32k/65k/131k) per model → replace placeholder `decode_tps`, decide how
      high ctx can safely go (esp. the cheap-KV sliding-window angle).
- [ ] **exp 3 — tool-use suite:** run `tests/tool_use` on each. Flip
      `supports_function_calling` only on pass. Expect possible prompt-based
      failures.
- [ ] **exp 4 — reasoning check:** does the 26B (or any) emit a separable think
      channel? If so, set `reasoning_format`. Else leave off.
- [ ] **exp 5 — speculative decoding:** try the Gemma draft models via
      `--model-draft` for a decode speedup (no quality loss) — advanced, optional.
- [ ] Sync to LiteLLM; mirror into the opencode app-config per the sync rule.

## Change log

| Date | Model | Change | Reason / result |
|---|---|---|---|
| 06-04 | all | Initial entries; ctx 64k/64k/32k, Gemma sampling, tools/reasoning off | Starting values (above). Placeholder decode_tps (60/50/20) pending bench. |

## Open questions

- How high can ctx go before the decode cliff bites, given sliding-window KV? (exp 2)
- Do any Gemma 4 models do structured tool calling, or only prompt-based? (exp 3)
- Is the 26B "reasoning" a separable think channel or just capability? (exp 4)
- Worth wiring the speculative-decoding draft models? (exp 5)
- Quant bump for the 12B/26B given VRAM headroom (Q5/Q6)?
