# Troubleshooting

Quick reference for issues I actually hit while building this. Grouped roughly from "first boot" problems to "it works but something's weird."

## `sycl-ls` inside the container doesn't show your B70

```bash
docker compose exec llama-swap sycl-ls
```

You expect a line like:

```
[level_zero:gpu][level_zero:0] Intel(R) oneAPI Unified Runtime over Level-Zero V2, Intel(R) Graphics [0xe223] ...
```

### No `level_zero:gpu` line at all

- Check `/dev/dri` is actually being passed through:
  ```bash
  docker compose exec llama-swap ls -la /dev/dri/
  ```
  If empty, the `devices:` mapping in `docker-compose.yml` isn't taking effect. Usually a permissions issue on the host — make sure the user running `docker compose` is in the `docker` group.

- Check the render/video GIDs inside the container match the host:
  ```bash
  getent group render video                                  # on host
  docker compose exec llama-swap id                          # inside container
  ```
  If they don't match, set `RENDER_GID` / `VIDEO_GID` in `.env` and `docker compose up -d --force-recreate`.

### Level Zero is present but shows a different device ID than expected

The `libze-intel-gpu1` package from Intel's apt repo inside the container might be too old to recognize your card. As of April 2026, the `noble unified` channel ships compute-runtime 26.09+ which knows about the B70 (device ID `0xe223`). If yours is older, you'll see `[0xe223]` render as literal unknown-device text, or the card will be missing entirely.

Fallback: override `libze-intel-gpu1` with a manual install of a newer compute-runtime in the Dockerfile. Add this RUN block just before the llama.cpp build, adjusting the version to [whatever's latest on GitHub](https://github.com/intel/compute-runtime/releases):

```dockerfile
RUN mkdir -p /tmp/neo && cd /tmp/neo && \
    COMPUTE_VER=26.09.37435.1 && \
    IGC_VER=2.30.1 && \
    wget -q https://github.com/intel/intel-graphics-compiler/releases/download/v${IGC_VER}/intel-igc-core-2_${IGC_VER}+18391_amd64.deb && \
    wget -q https://github.com/intel/intel-graphics-compiler/releases/download/v${IGC_VER}/intel-igc-opencl-2_${IGC_VER}+18391_amd64.deb && \
    wget -q https://github.com/intel/compute-runtime/releases/download/${COMPUTE_VER}/libze-intel-gpu1_${COMPUTE_VER}_amd64.deb && \
    wget -q https://github.com/intel/compute-runtime/releases/download/${COMPUTE_VER}/libze1_${COMPUTE_VER}_amd64.deb && \
    wget -q https://github.com/intel/compute-runtime/releases/download/${COMPUTE_VER}/intel-opencl-icd_${COMPUTE_VER}_amd64.deb && \
    apt-get update && \
    apt-get install -y --no-install-recommends ./*.deb && \
    cd / && rm -rf /tmp/neo
```

(Check filenames on the releases page — Intel tweaks them.)

## Models fail with `exit status 127`

That's "command not found" from the spawned llama-server. Check:

```bash
docker compose exec llama-swap ls -la /opt/llama-cpp/bin/llama-server
docker compose exec llama-swap /opt/llama-cpp/bin/llama-server --help
```

If `--help` prints help text, the binary and its libs are fine — the issue is elsewhere in your llama-swap config. Most likely cause: typo in the `cmd:` block, or a blob path that doesn't exist under `/models/`.

If `--help` errors with `error while loading shared libraries:` you have a dynamic linker problem. The Dockerfile runs `ldconfig` after install, so this shouldn't happen with an unmodified image. If you modified the Dockerfile, make sure `/etc/ld.so.conf.d/llama-cpp.conf` still exists and contains `/opt/llama-cpp/lib`.

## Models load but inference is slow (< 30 tok/s on a 30B model)

Several common causes:

- **You're on Q8_0 instead of Q4_K_M**. Battlemage has a 4× kernel regression on Q8_0 ([llama.cpp #21517](https://github.com/ggml-org/llama.cpp/issues/21517)). Use Q4_K_M quants.
- **You added `-ctk q8_0 -ctv q8_0`** to the cmd block. Don't — KV cache quantization regresses decode throughput on current SYCL kernels.
- **You're on a non-trivially old commit of llama.cpp**. The SYCL backend moves fast. Rebuild with a recent tag/master.
- **Vulkan is winning the backend race**. If `sycl-ls` shows the B70 but llama-server logs show it picking Vulkan instead, force with `--device SYCL0` in the cmd block (should already be in the example config).

Sanity check: run `llama-bench` directly inside the container for ground-truth numbers without llama-swap or client overhead.

```bash
docker compose exec llama-swap /opt/llama-cpp/bin/llama-bench \
    -m /models/blobs/sha256-<your-model> \
    -ngl 99 \
    -t 1 \
    --device SYCL0
```

Expected ballpark for Qwen3 30B-A3B Q4_K_M: ~400+ t/s on prefill (pp512), ~55-60 t/s on decode (tg128).

## First request times out

llama-swap's `healthCheckTimeout` default is 180s. The example config already sets this, but if you have a really large model (e.g. 70B) or a slow disk, the first mmap + SYCL kernel compile can exceed that. Bump it:

```yaml
healthCheckTimeout: 300
```

Also check disk read speed — if GGUFs live on a spinning HDD, first-load is painful.

## Can't reach port 11434 from another machine on the LAN

- Confirm llama-swap is actually listening on all interfaces, not just localhost:
  ```bash
  ss -tlnp | grep 11434
  # Want: LISTEN 0.0.0.0:11434, NOT 127.0.0.1:11434
  ```
  The compose file binds `"11434:11434"` which exposes on all host interfaces — should be fine.

- Check the host firewall:
  ```bash
  sudo ufw status | grep 11434         # if using ufw
  sudo iptables -L -n | grep 11434     # raw check
  ```
  Allow LAN if needed:
  ```bash
  sudo ufw allow from 192.168.0.0/16 to any port 11434
  ```

## `llama-swap` config changes aren't being picked up

The container runs llama-swap with `--watch-config`, so editing `config/llama-swap.yaml` on the host should hot-reload automatically. If it doesn't:

- Make sure you're editing the right file. The bind-mount is `./config/llama-swap.yaml:/config/llama-swap.yaml:ro` — that's relative to where you run `docker compose`. Check inside the container:
  ```bash
  docker compose exec llama-swap cat /config/llama-swap.yaml
  ```

- Some editors (VS Code on Windows via WSL, for instance) write files via rename-and-replace, which can confuse inotify. Force a reload by bouncing the service:
  ```bash
  docker compose restart llama-swap
  ```

## GPU is used but only at ~50% utilization

This is normal for LLM decode — it's memory-bandwidth-bound, not compute-bound, so the compute engines sit partially idle waiting on VRAM reads. Look at power draw instead: if the B70 is pulling 100W+ and the fan is spinning at 1200+ RPM, it's working hard.

Prompt prefill on a long prompt (several thousand tokens) should saturate the compute engines — if you want to see the card really light up, send a big input.

## Older errors that shouldn't happen with this repo, but just in case

- **`sycl::_V1::exception: No device of requested type available`** — this was the failure mode of `intelanalytics/ipex-llm-inference-cpp-xpu`. Their compute-runtime predated Battlemage. This repo's image builds on a newer base; if you somehow still see this, follow the "fallback compute-runtime install" in the section above.
- **`/opt/intel/oneapi/setvars.sh` does not exist** — only happens if you're using a `runtime` flavor of the oneAPI image instead of `devel`. The Dockerfile here uses `devel` intentionally.
- **`runner crashed` during Ollama GPU discovery** — not applicable, this repo doesn't use Ollama. But if you previously set `OLLAMA_VULKAN=1` on the host and it silently fell back to CPU, nothing in this container will be affected by that.
