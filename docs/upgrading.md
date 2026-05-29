# Upgrading the core stack (and validating on Battlemage)

A runbook for bumping the pieces this image bundles — **llama.cpp**, **llama-swap**,
and the **Intel GPU driver** (compute-runtime + IGC + gmmlib) — and confirming the
new build still works on an Arc Pro B70 (Xe2/Battlemage) before you trust it.

Battlemage is new silicon and SYCL support is moving fast, so it's worth re-checking
upstream every few weeks: real fixes land often (see the [watch-list](#battlemage-watch-list)).

## What's pinned, and where

Everything lives in [`Dockerfile`](../Dockerfile) build args:

| Component | ARG | Notes |
| --- | --- | --- |
| llama.cpp | `LLAMA_CPP_REF` | `master` (latest at build time) or a tag like `b9409` for reproducibility |
| llama-swap | `LLAMA_SWAP_VERSION` | release number, e.g. `217` |
| compute-runtime | `COMPUTE_RUNTIME_VERSION` | Level-Zero GPU driver, e.g. `26.18.38308.1` |
| IGC (graphics compiler) | `IGC_VERSION` + `IGC_BUILD` | e.g. `2.34.4` + `21428` (the `+BUILD` suffix) |
| gmmlib | `GMMLIB_VERSION` | shipped *with* the compute-runtime release, e.g. `22.10.0` |
| oneAPI base image | `ONEAPI_IMAGE_TAG` | the SYCL compiler toolchain; bump cautiously (affects codegen) |

Known-good snapshot (2026-05-29): llama.cpp `master`, llama-swap `217`,
compute-runtime `26.18.38308.1`, IGC `2.34.4+21428`, gmmlib `22.10.0`,
oneAPI `2025.3.0-...-devel-ubuntu24.04`.

## 1. Check what's current upstream

```bash
# Latest releases of the pinned components
for r in mostlygeek/llama-swap intel/compute-runtime intel/intel-graphics-compiler; do
  curl -s "https://api.github.com/repos/$r/releases/latest" \
    | python3 -c "import sys,json; d=json.load(sys.stdin); print(f\"$r: {d['tag_name']}  {d['published_at'][:10]}\")"
done

# llama.cpp newest tagged build
curl -s "https://api.github.com/repos/ggml-org/llama.cpp/releases/latest" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print('llama.cpp:', d['tag_name'], d['published_at'][:10])"
```

Then scan llama.cpp for Battlemage-relevant changes — search issues/PRs for `SYCL`,
`Xe2`, `Battlemage`, `B70`, and check whether anything on the [watch-list](#battlemage-watch-list)
flipped to closed/fixed:

```bash
# State of a specific issue (state_reason: completed == fixed)
curl -s "https://api.github.com/repos/ggml-org/llama.cpp/issues/21517" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['state'], d.get('state_reason'), '-', d['title'])"
```

## 2. Get the exact version strings

The driver `.deb` URLs need exact strings the "latest release" call doesn't give you —
the IGC `+BUILD` suffix and the gmmlib version bundled in the compute-runtime release:

```bash
# IGC: grab the +BUILD suffix from the asset names
curl -s "https://api.github.com/repos/intel/intel-graphics-compiler/releases/tags/v2.34.4" \
  | python3 -c "import sys,json; [print(a['name']) for a in json.load(sys.stdin)['assets'] if a['name'].endswith('.deb')]"
#   -> intel-igc-core-2_2.34.4+21428_amd64.deb   ==> IGC_VERSION=2.34.4  IGC_BUILD=21428

# compute-runtime: confirm asset names + which gmmlib ships with it
curl -s "https://api.github.com/repos/intel/compute-runtime/releases/tags/26.18.38308.1" \
  | python3 -c "import sys,json; [print(a['name']) for a in json.load(sys.stdin)['assets'] if a['name'].endswith('.deb')]"
#   -> libigdgmm12_22.10.0_amd64.deb              ==> GMMLIB_VERSION=22.10.0
```

> The Intel apt repo (`repositories.intel.com/gpu/ubuntu`) historically lags upstream
> and has shipped compute-runtime builds that don't recognize the B70 (`sycl-ls`
> reports `Platforms: 0`). This image installs the `.deb`s straight from GitHub
> releases for that reason — keep doing that.

## 3. Update the pins

Edit the ARG defaults in [`Dockerfile`](../Dockerfile) (or pass `--build-arg`s):

```dockerfile
ARG COMPUTE_RUNTIME_VERSION=26.18.38308.1
ARG IGC_VERSION=2.34.4
ARG IGC_BUILD=21428
ARG GMMLIB_VERSION=22.10.0
...
ARG LLAMA_SWAP_VERSION=217
```

Leave `LLAMA_CPP_REF=master` to track latest, or pin a tag (`b9409`) if you want the
exact build to be reproducible.

## 4. Rebuild

```bash
docker compose build          # compiles llama.cpp with SYCL; minutes on a fast box
docker compose up -d           # recreate the container on the new image
```

## 5. Validate

Do **not** skip this — Battlemage SYCL has had regressions that silently corrupt
output (see the watch-list). Type checks pass; only running the models proves it.

```bash
# a) GPU enumerates with the NEW driver. Expect a [level_zero:gpu] line for the
#    B70; the bracketed build (e.g. [1.15.38308+1]) should match the new
#    compute-runtime version. "Platforms: 0" => driver doesn't know the card.
docker compose exec llama-swap sycl-ls

# b) Versions are what you pinned
docker compose exec llama-swap /opt/llama-cpp/bin/llama-server --version
docker compose exec llama-swap llama-swap --version

# c) COHERENCE + serving — every model cold-loads and answers. A model that
#    returns garbage/empty is the corruption-regression signal.
make test-models

# d) Tool calling still works for the agentic models
./tests/tool_use/run.sh qwen3-coder-30b glm-4.7-flash-q4 gpt-oss-20b

# e) If you front with LiteLLM, re-mirror and test through the proxy
make sync-litellm
#    (test-models VIA=litellm needs an inference-capable key, not the mgmt key)
```

A clean run looks like `make test-models` reporting all models `OK` (a `WARN` for a
thinking model that hit the token cap is fine; a `FAIL` or gibberish reply is not).

After a good upgrade, **re-benchmark anything the watch-list says may have changed**
(e.g. Q8_0 speed) and update the stale notes in
[`config/llama-swap.example.yaml`](../config/llama-swap.example.yaml) and the README
performance table.

## 6. If something regresses

The new image *replaces* `battlemage-llama:latest`, but rolling back is just reverting
the pins and rebuilding:

```bash
git checkout -- Dockerfile          # or: git revert <the pin-bump commit>
docker compose build && docker compose up -d
```

Pinning `LLAMA_CPP_REF` to the last known-good tag (instead of `master`) is the
cleanest way to isolate a llama.cpp-side regression.

> **Do not reach for `GGML_SYCL_DISABLE_OPT=1` as a "fix."** It's been suggested as a
> workaround for B70 output-corruption bugs, but it disables the SYCL *reorder
> optimization* — the very thing that makes Q4_K_M and Q8_0 fast on Xe2. Using it
> trades correctness for a large speed loss. Prefer pinning llama.cpp to a known-good
> tag and filing/tracking the upstream issue.

## Battlemage watch-list

Issues that shaped this repo's defaults — re-check their status when upgrading, since
several have already been fixed and more will be:

| Area | Issue/PR | Status (2026-05) | Implication |
| --- | --- | --- | --- |
| Q8_0 ~4x slower than Q4 on Xe2 | [#21517](https://github.com/ggml-org/llama.cpp/issues/21517) / [#21527](https://github.com/ggml-org/llama.cpp/pull/21527) | **fixed + confirmed** | Measured on B70 (Ministral-14B): Q8_0 ~1.6x slower than Q4 (32 vs 53 t/s), not 4x. Q8_0 is viable now. |
| `GGML_SYCL_F16` weight corruption on B70 | [#21893](https://github.com/ggml-org/llama.cpp/issues/21893) | **fixed** | This image builds with `-DGGML_SYCL_F16=ON`; validate output after rebuild |
| "Brutally bad SYCL perf on Battlemage" | [#22413](https://github.com/ggml-org/llama.cpp/issues/22413) | **fixed** | General Xe2 throughput improvements |
| Dense 24B Mistral segfault at `-c 131072` | (local finding) | open | Cap Magistral/Devstral-2 at `65536` |
| MXFP4 (gpt-oss) decode speed on SYCL | — | works, unbenchmarked | `llama-bench` to confirm throughput |
| `tool_choice:required` sampler 400 (Qwen3.5-35B-A3B) | (local finding) | open | Flagged `supports_tool_choice: false` in `models.yaml` |

When upstream closes one of these, the upgrade payoff is concrete — rebuild, re-test
with the commands above, and retire the corresponding workaround.
