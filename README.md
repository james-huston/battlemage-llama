# battlemage-llama

A Docker-based, SYCL-accelerated stack for running local LLMs on Intel Arc GPUs — specifically designed around the **Intel Arc Pro B70 (Battlemage G31)** with 32 GB of VRAM. Wraps [llama.cpp](https://github.com/ggml-org/llama.cpp) with the [llama-swap](https://github.com/mostlygeek/llama-swap) proxy for on-demand model switching via an OpenAI-compatible API.

Drops in where Ollama would sit on port 11434, keeps the same Ollama-style model-swap ergonomics, but uses Intel's native SYCL backend and XMX matrix engines for significantly better performance than Vulkan on Battlemage.

## Why this exists

When I put an Arc Pro B70 into a Linux workstation in April 2026, none of the "just works" options actually worked:

- **Ollama + Vulkan** ran, but at ~22 tok/s on a 30B MoE model — well below what the card's 608 GB/s memory bandwidth should deliver.
- **Intel's `ipex-llm-inference-cpp-xpu` container** (the obvious first choice) crashed during SYCL init because its bundled compute-runtime pre-dated the B70. The IPEX-LLM project was archived in January 2026 and isn't being updated for new silicon.
- **Intel's `llm-scaler-vllm`** worked but is vLLM — optimized for many-concurrent-users throughput, not solo-dev ergonomics. No model swapping, clunky per-model container restarts.
- **Native-host llama.cpp build** worked and hit ~59 tok/s on the same 30B model (a 2.7× speedup), but required installing Intel oneAPI (~2.5 GB), configuring `/etc/ld.so.conf.d/` entries, and generally making a mess of the host system.

This repo is the cleanup: the native-host build moved into a Dockerfile, with all the oneAPI + compute-runtime + llama.cpp toolchain self-contained in a single image. The host stays pristine — all it needs is the Intel GPU kernel driver (`xe`) and Docker.

## Performance (Arc Pro B70, Qwen3-Coder 30B-A3B Q4_K_M)

| Backend | tok/s (decode) | Notes |
| --- | ---: | --- |
| Ollama + Vulkan (Mesa) | 22 | Known [llama.cpp perf gap on Xe2](https://github.com/ggml-org/llama.cpp/issues/21517) |
| Ollama + flash-attn + q8 KV cache | 16 | Regression on Battlemage — don't do this |
| **battlemage-llama (this repo)** | **59** | SYCL + XMX, matches reported B70 benchmarks |

Prompt prefill is similarly faster (roughly 300–700 tok/s for long prompts thanks to `-DGGML_SYCL_F16=ON` on the XMX units — prefill on tiny prompts is dominated by fixed overhead and is not representative).

## Hardware support

Built and tested on:

- **Intel Arc Pro B70** (BMG-G31, 32 GB) ✓
- Should also work on:
  - Arc Pro B60 / B50 (same Battlemage family)
  - Arc B580 / B570 (consumer Battlemage)
  - Arc A-series (Alchemist, Xe1) — older but SYCL-supported

GPUs other than Intel Arc won't benefit from this setup. For NVIDIA, use llama.cpp's CUDA builds; for AMD, ROCm or Vulkan.

## Prerequisites

On the **host**:

- Linux with a recent kernel that has the `xe` driver (Ubuntu 25.10 / kernel 6.17+ is known-good; Ubuntu 24.04 also works via the Intel graphics PPA).
- [Intel GPU kernel driver installed and loaded](docs/host-setup.md) — `/dev/dri/renderD128` (or similar) must exist and the card must be visible in `lspci` with the `xe` driver bound.
- Docker Engine with Compose (`docker compose version` ≥ v2).
- Your user added to the `render` and `video` groups.

No Intel oneAPI install on the host — the container provides its own.

Check your render/video GIDs:

```bash
getent group render video
```

If they're not `993` and `44`, copy `.env.example` to `.env` and set `RENDER_GID` / `VIDEO_GID`.

## Quick start

```bash
git clone https://github.com/james-huston/battlemage-llama.git
cd battlemage-llama

# Adjust GIDs / model paths if needed
cp .env.example .env
$EDITOR .env

# Copy the example config and list your models
cp config/llama-swap.example.yaml config/llama-swap.yaml
./scripts/find-gguf-blobs.sh   # discovers Ollama blob paths on your host

# Paste the blob paths from find-gguf-blobs.sh into config/llama-swap.yaml

# Build and start (first build takes 10-20 min — pulls oneAPI base, compiles llama.cpp)
docker compose up -d --build

# Verify the B70 is visible to SYCL inside the container
docker compose exec llama-swap sycl-ls
# Expect a [level_zero:gpu] line with your B70 (device ID 0xe223 for Arc Pro B70)

# Smoke test
curl http://localhost:11434/v1/models | jq
curl http://localhost:11434/v1/chat/completions \
    -H "Content-Type: application/json" \
    -d '{"model":"qwen3-coder-30b","messages":[{"role":"user","content":"hello"}]}'
```

The first request to a given model takes 10-30 seconds (cold start — llama-swap spawns llama-server, which mmaps the GGUF and initializes SYCL kernels). Subsequent requests against the same model are instant. Switching models unloads the old one and starts the new one.

## Architecture

```
Clients  (LiteLLM, Continue.dev, Open WebUI, scripts, ...)
    │
    ▼  OpenAI-compatible /v1/chat/completions etc.
┌───────────────────────────────────────────────────┐
│  Container: battlemage-llama                      │
│                                                   │
│   llama-swap  :11434   ← /config/llama-swap.yaml  │
│       │                                           │
│       │ spawns / stops on demand per request      │
│       ▼                                           │
│   llama-server  :12800+  (SYCL backend, -ngl 99)  │
│       │                                           │
└───────┼───────────────────────────────────────────┘
        ▼  /dev/dri passthrough
   Intel Arc Pro B70 (Battlemage G31)
```

## Bring your own GGUFs

You do NOT need Ollama installed. The default `docker-compose.yml` bind-mounts `/opt/apps/ollama-models` because that's a convenient place to get GGUFs if you've already pulled them via Ollama — llama.cpp reads Ollama's content-addressed blobs natively.

If you downloaded GGUFs from Hugging Face directly, point `MODELS_DIR` at wherever they live (set in `.env`) and adjust the `--model` paths in your `config/llama-swap.yaml` accordingly. Inside the container, whatever you bind-mount shows up at `/models/`.

## Managing models declaratively (`models.yaml`)

`models.yaml` (repo root) is the **source of truth** for what we run and where
each GGUF came from. Each entry records the install source (an Ollama blob
`path:`, or a HF `repo:` + `file:`) and the llama-swap runtime params (`ctx`,
`template`, `reasoning` / `reasoning_format`, `temp`, `top_p`). `enabled: false`
keeps a model documented as a candidate without installing or serving it.

Entries can also carry **LiteLLM metadata**: `decode_tps` (measured decode
tok/s) and a `litellm:` block (`description`, `supports_function_calling`,
`supports_tool_choice`, `supports_reasoning`, …), plus a top-level `cost:` block.
`make sync-litellm` turns these into LiteLLM `model_info` — including per-token
costs derived from `decode_tps` (see [Syncing to LiteLLM](#syncing-models-to-a-litellm-proxy)).

```bash
# Edit models.yaml, then:
make models-apply              # download any missing enabled GGUF + regenerate the config
make models-apply DRY_RUN=1    # preview the plan + generated config, write nothing
```

`make models-apply` downloads what's missing and **regenerates**
`config/llama-swap.yaml` from the enabled entries (a `.bak` is kept). That file
is now a generated artifact — edit `models.yaml`, not the config. It also reports
GGUF dirs in `MODELS_DIR` that no enabled model references, so stale downloads are
easy to spot. Follow with `make sync-litellm` and `make test-models`. Requires
PyYAML (`pip install pyyaml`).

## Adding models ad-hoc with `make`

For a quick one-off (without editing `models.yaml`), `make add-model` wraps
`scripts/add-model.sh` to download a GGUF from Hugging Face into `MODELS_DIR` and
*append* a ready-to-run block (Battlemage defaults — `-ngl 99 --device SYCL0
-sm none --jinja`) to `config/llama-swap.yaml`. Note the next `make models-apply`
will overwrite such appends, so fold anything you want to keep into `models.yaml`:

```bash
# Download + register in one step
make add-model \
    REPO=unsloth/GLM-4.7-Flash-GGUF \
    FILE=GLM-4.7-Flash-Q4_K_XL.gguf \
    NAME=glm-4.7-flash-q4 DIR=glm-4.7-flash OUT=Q4_K_XL.gguf \
    CTX=131072 TEMPLATE=glm-4.7-flash.jinja REASONING=1 TEMP=0.6 TOP_P=0.95

make list-models            # show aliases already in the config
make help                   # all targets + the full variable list
```

| Var | Meaning |
| --- | --- |
| `REPO` / `FILE` | Hugging Face repo id and filename (`FILE` may be a glob/split set — needs the `hf` / `huggingface-cli`; otherwise `curl`/`wget` fetches a single file) |
| `NAME` | model alias (the OpenAI `model` id) |
| `DIR` / `OUT` | subdir under `MODELS_DIR` (default `NAME`) and save-as filename (default the repo filename) |
| `CTX` | `-c` context size — keep dense 24B Mistrals at `65536` (they segfault at 128k on SYCL) |
| `TEMPLATE` | a Jinja file in `templates/` → `--chat-template-file` |
| `REASONING=1`, `TEMP`, `TOP_P`, `EXTRA` | add `--reasoning-format deepseek`, samplers, or extra flags |

Other entrypoints: `make download-model` (fetch only), `make add-config`
(register an already-present GGUF, e.g. an Ollama blob via `MODEL_PATH=`), and
`make find-blobs` (the original Ollama blob mapper). Add `DRY_RUN=1` to preview
the config block without writing it. llama-swap hot-reloads the config, so the
next request to the new alias spawns it — then validate with
`./tests/tool_use/run.sh <NAME>`.

## Syncing models to a LiteLLM proxy

If you front this stack with [LiteLLM](https://github.com/BerriAI/litellm),
`scripts/sync-litellm.py` (`make sync-litellm`) makes LiteLLM mirror what
llama-swap serves. It reads `GET {upstream}/v1/models`, then **adds** missing
models, **deletes** LiteLLM-managed models no longer served, and **re-points**
any whose registration drifted. Models baked into LiteLLM's static `config.yaml`
(not DB-managed) are left untouched.

Each model is registered with the **`openai/` provider** + an `/v1` `api_base`,
and enriched with `model_info` drawn from `models.yaml` (shown in LiteLLM's Model
Hub UI and used for routing/validation):

- **`description`** and the **`supports_function_calling` / `supports_tool_choice` /
  `supports_reasoning` / `supports_vision`** capability flags — from each entry's
  `litellm:` block. (So e.g. a code-only model can be flagged "no tool calling.")
- **`max_input_tokens`** — from the entry's `ctx`.
- **`mode: chat`** (fleet default).
- **`input_cost_per_token` / `output_cost_per_token`** — *derived* per model from
  its `decode_tps` and the manifest's top-level `cost:` block, which prices GPU
  time as electricity × a hardware-amortization multiplier:
  ```yaml
  cost:
    watts: 250          # GPU draw under load
    usd_per_kwh: 0.18   # your electricity rate
    amortization: 3     # capital + wear multiplier over raw electricity
    input_factor: 4     # prefill is ~4x faster than decode -> input = output/4
  ```
  `output $/token = watts/1000 × (1/decode_tps/3600) × usd_per_kwh × amortization`.
  Change a knob and the next sync re-prices the whole fleet (slower models cost
  more per token).

```bash
# Put the admin key in gitignored .env (LITELLM_API_KEY=sk-...), then:
make sync-litellm DRY_RUN=1          # preview the plan, change nothing
make sync-litellm                    # add new, re-point drifted, delete stale
```

| Var / env | Meaning |
| --- | --- |
| `LITELLM_API_KEY` / `LITELLM_MASTER_KEY` | LiteLLM admin key (env or `.env`; never printed). Required. |
| `LITELLM` / `LITELLM_URL` | LiteLLM proxy base URL (default `http://localhost:4000`) |
| `UPSTREAM` / `UPSTREAM_URL` | llama-swap base URL the script reads `/v1/models` from (default `http://localhost:11434`) |
| `API_BASE` / `MODEL_API_BASE` | `api_base` baked into each LiteLLM model. **If LiteLLM runs on another host, this must be routable from there** — use the model server's IP/hostname, not `localhost` (e.g. `http://10.0.0.5:11434/v1`). |
| `NO_DELETE=1` | only add/re-point, never delete |
| `RESET=1` | delete ALL DB-managed models first, then re-add (clean rebuild) |

Run it after `make models-apply` to keep LiteLLM in lockstep. The `model_info`
enrichment (descriptions, flags, costs) needs PyYAML (`pip install pyyaml`);
without it the model list still syncs.

> **Provider gotcha:** llama-swap is OpenAI-compatible, *not* Ollama. Models in
> LiteLLM must use the `openai/` provider with an `/v1` `api_base` — an
> `ollama`/`ollama_chat` provider pointed at port 11434 fails with
> `Ollama_chatException`. `sync-litellm` registers the right provider; see
> [`docs/troubleshooting.md`](docs/troubleshooting.md).

> **Admin vs inference keys:** LiteLLM's `model/*` management API and chat
> inference use different key scopes. A management-only virtual key can sync
> models but gets a 403 on `/v1/chat/completions` — so `make test-models VIA=litellm`
> needs a key allowed to run inference (or the master key), not just the admin key
> used for syncing.

## Smoke-testing models

`scripts/test-models.py` (`make test-models`) cycles through every model an
endpoint advertises, sends a real chat query to each (which forces llama-swap to
cold-load it), and reports pass/fail — handy for confirming a fresh model works
or bisecting which registered models are broken.

```bash
make test-models                       # test every model on llama-swap directly
make test-models VIA=litellm           # test each model through the LiteLLM proxy
make test-models MODELS=glm-4.7-flash-q4   # just one (or a space-separated subset)
```

Because only one model fits in VRAM at a time, it's sequential and each model is
a cold start (expect ~10-30s apiece). A model that loads but returns no visible
text — usually a thinking model that spent the token budget reasoning — is
reported as `WARN`, not `FAIL`; bump `MAX_TOKENS` for those. Exit code is
non-zero if any model fails. Knobs: `VIA`, `BASE`, `API_KEY`, `MAX_TOKENS`,
`TIMEOUT`, `PROMPT` (also Python 3 stdlib only).

## Documentation

- [`docs/host-setup.md`](docs/host-setup.md) — host driver / kernel setup for Battlemage on Ubuntu 25.10
- [`docs/troubleshooting.md`](docs/troubleshooting.md) — common issues and diagnostics
- Upstream: [llama.cpp SYCL docs](https://github.com/ggml-org/llama.cpp/blob/master/docs/backend/SYCL.md), [llama-swap configuration](https://github.com/mostlygeek/llama-swap/blob/main/config.example.yaml)

## Contributing

Issues and PRs welcome. I'm particularly interested in:

- Testing reports from other Arc GPUs (B50, B60, A770, etc.)
- Performance tuning flags that actually help on Xe2 / Xe1
- Fallback Dockerfile paths for hosts where the Intel graphics apt repo doesn't yet have a B70-aware compute-runtime

## License

MIT. See [LICENSE](LICENSE).

## Credits

- [ggml-org/llama.cpp](https://github.com/ggml-org/llama.cpp) — the inference engine.
- [mostlygeek/llama-swap](https://github.com/mostlygeek/llama-swap) — the model-swap proxy.
- [intel/oneapi-containers](https://github.com/intel/oneapi-containers) — the base image.
- Intel for shipping a competitively-priced 32GB workstation GPU that actually works on Linux.
