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
