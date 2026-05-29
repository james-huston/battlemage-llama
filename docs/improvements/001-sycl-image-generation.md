# Story 001: SYCL image generation via stable-diffusion.cpp

## Status

Review

## Story

**As a** solo developer running battlemage-llama on an Arc Pro B70,
**I want** on-demand text-to-image generation served through the same llama-swap
endpoint,
**so that** I can use the web UI's image section (and the OpenAI
`/v1/images/generations` API) for image generation on my Intel GPU — swapped in
on demand alongside my coding models, with no separate stack to run.

## Acceptance Criteria

1. The container image builds **stable-diffusion.cpp's server with the SYCL
   backend** (same oneAPI toolchain as llama.cpp), producing a server binary on
   `PATH` inside the image.
2. An image model is declarable in `models.yaml` (e.g. `engine: sd-server`) and
   `make models-apply` generates a correct llama-swap config block for it —
   using the sd-server command, **not** the `llama-server` flags.
3. `POST /v1/images/generations` for that model (and the web UI's image section)
   returns a generated image; llama-swap loads/unloads the sd-server upstream on
   demand exactly like an LLM (respecting the swap / single-resident model).
4. The image model runs on the **B70 via SYCL** (confirmed in server logs /
   device usage), and a default-resolution generation completes in a reasonable
   time for the chosen model.
5. The image model is registered in LiteLLM by `make sync-litellm` with sensible
   `model_info` (e.g. `mode: image_generation`, `supports_function_calling:
   false`), **without breaking or re-pointing the existing LLM entries**.
6. `make test-models` and the rest of the validation flow handle the non-chat
   model gracefully (it is not false-`FAIL`ed by the chat-only smoke test).
7. README + `models.yaml` document the image-gen setup so others can replicate.

## Tasks / Subtasks

- [x] **Spike — pin the stable-diffusion.cpp specifics** (AC: 1, 3)
  - [x] Server binary = `sd-server` (CMake target). Exposes OpenAI
        `/v1/images/generations`, `/v1/images/edits`, `/v1/models` **and** SDAPI
        `/sdapi/v1/...` — so llama-swap's OpenAI image proxy routes directly.
  - [x] SYCL flag = `-DSD_SYCL=ON` (sets `GGML_SYCL=ON`); same icx/icpx toolchain.
  - [x] Initial model = **SDXL base 1.0** (single safetensors, ~6.9 GB, fits with
        room, good quality, no separate t5/clip files). Flux.1 is a follow-up
        (needs `--diffusion-model` + `--vae` + `--t5xxl` + clip side-files).
- [x] **Dockerfile — build + install the sd.cpp SYCL server** (AC: 1)
  - [x] Clone stable-diffusion.cpp (`SD_CPP_REF`, `--recurse-submodules`);
        `cmake -DSD_SYCL=ON -DGGML_SYCL_F16=ON` + icx/icpx; build the `sd-server`
        target; install to `/opt/sd-cpp/bin` (+ `*.so` to `/opt/sd-cpp/lib`,
        ldconfig); added to `PATH`
  - [x] Added `SD_CPP_REF` build-arg; **also** `-DCMAKE_CXX_FLAGS=-fno-sycl-id-queries-fit-in-int`
        (required — see Validation findings, the 1024² IM2COL fix)
- [x] **Manifest + generator — support an image engine** (AC: 2)
  - [x] `engine` field added (default `llama-server`; `sd-server` for images)
  - [x] `scripts/apply-models.py` branches on `engine` — emits the sd-server
        block (`--model`/`--listen-ip`/`--listen-port`/`checkEndpoint: /v1/models`),
        keeps the llama-server block for LLMs
- [x] **Add the image model** (AC: 3, 4)
  - [x] Downloaded `sd_xl_base_1.0.safetensors` to `/models/sdxl/`; added the
        `models.yaml` entry (`engine: sd-server`); `make models-apply`
- [x] **LiteLLM integration** (AC: 5)
  - [x] `sync-litellm` registered `sdxl` with `mode: image_generation`,
        `supports_function_calling: false`; +1 add, 0 re-point/delete (LLMs untouched)
- [x] **Validation** (AC: 3, 4, 6)
  - [x] `POST /v1/images/generations` via llama-swap returns a valid PNG
        (512² 5 s, 768² 13 s, **1024² 30 s**); the UI image section drives the
        same endpoint
  - [x] sd-server logs confirm `[level_zero:gpu] Intel Arc Pro B70`; `make status`
        shows it resident (~6.8 GiB VRAM)
  - [x] `make test-models` auto-skips `sdxl` (engine ≠ llama-server)
- [x] **Docs** (AC: 7)
  - [x] README image-gen section; `models.yaml` notes; troubleshooting entries
  - [x] Tracker updated; story → Review

## Dev Notes

- **stable-diffusion.cpp** ([leejet/stable-diffusion.cpp](https://github.com/leejet/stable-diffusion.cpp)):
  ggml-based, **supports the SYCL backend**, loads GGUF (SD1.5 / SDXL / Flux), and
  ships a server + embedded web UI (added ~April 2026). Build mirrors this repo's
  llama.cpp step in the [Dockerfile](../../Dockerfile) (icx/icpx + a `SD_*` SYCL
  flag). Pin the ref like `LLAMA_CPP_REF`.
- **llama-swap** (this stack, v217) proxies image endpoints — OpenAI
  `/v1/images/generations` + `/v1/images/edits`, and SDAPI `/sdapi/v1/txt2img`,
  `/sdapi/v1/img2img`, `/sdapi/v1/loras`. The UI "image" section drives these.
  The spike must confirm which endpoint the sd.cpp server exposes and that
  llama-swap's image proxy matches it.
- **Generator coupling**: [`scripts/apply-models.py`](../../scripts/apply-models.py)
  currently hardcodes the llama-server cmd (`--port`/`--model`/`-ngl 99`/`--device
  SYCL0`/`-sm none`/`--jinja`/...). Image models need a different command, so the
  cleanest change is an `engine` switch in the block builder (default unchanged).
- **VRAM / swap**: only one model is resident at a time (`-sm none`, single GPU),
  so the image model swaps in like any LLM. SDXL (~7 GB) / quantized Flux fit the
  32 GB B70 with headroom.
- **Cost model**: image generation is **per-image, not per-token**, so the
  `decode_tps`-derived cost in [`models.yaml`](../../models.yaml) doesn't apply.
  Either set a flat per-image cost in the `litellm:` block or omit cost for image
  entries — decide in the LiteLLM task.
- **References**: [`docs/upgrading.md`](../upgrading.md) (SYCL build + pin
  conventions), [`Dockerfile`](../../Dockerfile) (the llama.cpp SYCL build to
  mirror), [`config/llama-swap.example.yaml`](../../config/llama-swap.example.yaml).

### Spike findings (2026-05-29, source inspection of leejet/stable-diffusion.cpp@master)

- Server target is **`sd-server`** (`examples/server/CMakeLists.txt`). It serves
  OpenAI (`/v1/images/generations`, `/v1/images/edits`, `/v1/models`), SDAPI
  (`/sdapi/v1/txt2img|img2img|loras`), and a native `/sdcpp/v1/...`. llama-swap's
  OpenAI image proxy → `/v1/images/generations` works without an adapter.
- **`-DSD_SYCL=ON`** in `CMakeLists.txt` (line ~92) flips `GGML_SYCL=ON`; build
  with `icx`/`icpx` exactly like the llama.cpp step.
- Launch flags: `--model <checkpoint>` (single-file SD/SDXL), `--listen-ip`,
  `--listen-port` (llama-swap injects `${PORT}`), `--threads`, `--diffusion-fa`
  (flash attn). Flux/SD3 use split files: `--diffusion-model` + `--vae` +
  `--t5xxl` + clip — deferred to a follow-up.
- Initial model: **SDXL base 1.0** safetensors (sd.cpp loads `.safetensors`
  directly; no GGUF conversion needed). Expected sd-server cmd:
  `sd-server --model /models/sdxl/sd_xl_base_1.0.safetensors --listen-ip 127.0.0.1 --listen-port ${PORT} --diffusion-fa`

### Testing

- **Functional**: `curl -s localhost:11434/v1/images/generations -d '{"model":
  "<image-model>","prompt":"a red cube on a table"}'` returns an image
  (base64 or URL); confirm it decodes to a valid image file.
- **UI**: the web UI image section generates and displays an image.
- **Backend**: server logs show the SYCL/B70 device; `make status` shows the
  sd-server resident; record VRAM (xpu-smi) and wall-clock generation time.
- **Regression**: existing LLMs still serve; `make test-models` stays green (image
  model excluded/tolerated); `make sync-litellm` is idempotent and leaves LLM
  entries unchanged.

### Validation findings (2026-05-29, on the rebuilt image, Arc Pro B70)

Three Battlemage/SYCL issues surfaced during bring-up and are now handled:

1. **llama-swap health check** — sd-server doesn't serve `/health`, so llama-swap's
   default probe timed out (180 s) and killed the process. Fix: the generated
   sd-server block sets `checkEndpoint: /v1/models` (sd-server answers it once up).
2. **`--diffusion-fa` crashes** sd-server *during generation* on Xe2 (`exit 1`).
   Fix: the generator omits it; opt back in via `extra:` if a future build fixes it.
3. **1024² VAE decode** crashed with `Provided range/offset does not fit in int …
   Error OP IM2COL` (int32 index overflow in the SYCL conv). Fix: build sd.cpp with
   `-DCMAKE_CXX_FLAGS=-fno-sycl-id-queries-fit-in-int`.

Measured after the fixes (SDXL base 1.0, 20-step Euler A, via `POST /v1/images/generations`):
512² ≈ 5 s, 768² ≈ 13 s, **1024² ≈ 30 s** → valid PNGs; ~6.8 GiB VRAM resident.

## Change Log

| Date | Version | Description | Author |
|------|---------|-------------|--------|
| 2026-05-29 | 0.1 | Initial draft | James Huston / Claude |
| 2026-05-29 | 1.0 | Implemented + validated on the B70 (512²/768²/1024²). Status → Review. | Claude |

## Dev Agent Record

### Completion Notes

- sd-server (stable-diffusion.cpp) builds with SYCL alongside llama.cpp; SDXL
  base 1.0 served on demand via llama-swap at `/v1/images/generations`.
- Follow-ups: Flux.1 (split vae/t5/clip), and re-test `--diffusion-fa` when
  sd.cpp/Battlemage flash-attn stabilizes.

### File List

- `Dockerfile` — sd-server SYCL build (`SD_CPP_REF`, `-DSD_SYCL=ON`,
  `-fno-sycl-id-queries-fit-in-int`); `/opt/sd-cpp/bin` on PATH
- `scripts/apply-models.py` — `engine: sd-server` block + `checkEndpoint`
- `scripts/test-models.py` — skip non-chat (image) models
- `models.yaml` — `sdxl` entry (`engine: sd-server`, image_generation model_info)
- `README.md`, `docs/troubleshooting.md` — image-gen docs + Battlemage findings

## QA Results

- Functional: 512²/768²/1024² return valid PNGs via the llama-swap proxy. PASS
- Backend: sd-server logs show the B70 `[level_zero:gpu]`; `make status` shows it
  resident (~6.8 GiB). PASS
- Regression: `sync-litellm` added only `sdxl` (LLMs untouched); `test-models`
  auto-skips `sdxl`. PASS
