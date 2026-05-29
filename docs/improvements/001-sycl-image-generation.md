# Story 001: SYCL image generation via stable-diffusion.cpp

## Status

Draft

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

- [ ] **Spike — pin the stable-diffusion.cpp specifics** (AC: 1, 3)
  - [ ] Confirm the server binary name and the HTTP API llama-swap routes to:
        OpenAI `/v1/images/generations` vs Automatic1111 SDAPI
        `/sdapi/v1/txt2img` (the llama-swap README lists both paths)
  - [ ] Confirm the SYCL CMake flag (expected `-DSD_SYCL=ON`, mirroring
        llama.cpp's `-DGGML_SYCL=ON`) and any extra oneAPI components needed
  - [ ] Choose an initial model that fits 32 GB and is fast enough on Xe2
        (candidates: Flux.1-schnell GGUF, or SDXL GGUF) and record VAE/clip needs
- [ ] **Dockerfile — build + install the sd.cpp SYCL server** (AC: 1)
  - [ ] Clone stable-diffusion.cpp at a pinned ref; `cmake -DSD_SYCL=ON
        -DCMAKE_C_COMPILER=icx -DCMAKE_CXX_COMPILER=icpx`; install server to
        `/opt/sd-cpp/bin`; add to `PATH`
  - [ ] Add `SD_CPP_REF` build-arg (default a pinned tag) — record it in
        [`docs/upgrading.md`](../upgrading.md) alongside the other pins
- [ ] **Manifest + generator — support an image engine** (AC: 2)
  - [ ] Add an `engine` field to `models.yaml` entries (default `llama-server`;
        `sd-server` for images)
  - [ ] `scripts/apply-models.py`: branch the cmd-block template on `engine` —
        emit the sd-server invocation (model path, port, host, SYCL device) for
        image entries, keep the llama-server block for LLMs
- [ ] **Add the image model** (AC: 3, 4)
  - [ ] Download the chosen GGUF (extend `make add-model` if VAE/clip side-files
        are needed); add the `models.yaml` entry; `make models-apply`
- [ ] **LiteLLM integration** (AC: 5)
  - [ ] `sync-litellm`: register with `mode: image_generation` + flags via the
        `litellm:` block; confirm idempotent and that LLM entries are untouched
- [ ] **Validation** (AC: 3, 4, 6)
  - [ ] `curl /v1/images/generations` returns a valid image; UI image section works
  - [ ] Confirm SYCL/B70 usage in logs; record VRAM + generation time;
        `make status` shows the loaded sd-server
  - [ ] Ensure `make test-models` skips or tolerates the image model
- [ ] **Docs** (AC: 7)
  - [ ] README image-gen section; `models.yaml` notes; troubleshooting entry if needed
  - [ ] Flip this story to `Done` and update the README tracker

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

## Change Log

| Date | Version | Description | Author |
|------|---------|-------------|--------|
| 2026-05-29 | 0.1 | Initial draft | James Huston / Claude |

## Dev Agent Record

### Completion Notes

### File List

## QA Results
