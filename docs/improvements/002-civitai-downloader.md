# Story 002: Civitai model downloads (proving model: CyberRealistic Pony)

## Status

Review

## Story

**As a** B70 dev maintaining a local image-gen stack,
**I want** `make add-model` (and `models.yaml`) to fetch model checkpoints from
Civitai with my API key — just like it does from Hugging Face,
**so that** I can add any Civitai SDXL fine-tune through the same declarative
flow (edit `models.yaml` → `make models-apply`), starting with **CyberRealistic
Pony** ([Civitai 443821](https://civitai.com/models/443821/cyberrealistic-pony)).

## Acceptance Criteria

1. `make add-model REPO=civitai:<id> NAME=<x> [DIR=...] [OUT=...]` downloads
   the primary file of that Civitai version into `MODELS_DIR/<dir>/`.
2. `CIVITAI_API_KEY` (from env or `.env`) is sent as `Authorization: Bearer …`
   to Civitai. Without it, public versions still work best-effort.
3. The downloaded file lands at a useful filename — `OUT=` if given, otherwise
   the Civitai-reported primary filename.
4. `models.yaml` entries can reference a Civitai source (cleanest: reuse the
   existing `repo:` field with the `civitai:` prefix so `apply-models.py`
   shells out to `add-model.sh` with no schema change), and
   `make models-apply` downloads missing ones identically to Hugging Face
   entries.
5. **CyberRealistic Pony** is added as a manifest entry, downloaded by the new
   tool, served via `engine: sd-server`, and `POST /v1/images/generations` with
   `model: cyberrealistic-pony` returns a valid PNG on the B70.
6. `.env.example` documents `CIVITAI_API_KEY` (commented); the README documents
   the `civitai:` syntax, the optional key, and the failure modes.
7. The existing Hugging Face `add-model` path still works unchanged — no
   regression on LLM downloads or `models-apply`.
8. Error messages are actionable: HTTP **401/403** → "set `CIVITAI_API_KEY` (or
   accept this model's terms on the Civitai web UI)"; **404** → "no such version
   id"; missing primary file → "name it explicitly with `OUT=`."

## Tasks / Subtasks

- [x] **Spike — pin the Civitai download API specifics** (AC: 1, 2)
  - [x] `GET /api/v1/model-versions/{versionId}` returns `files: [{name,
        primary, type, sizeKB, hashes.SHA256, downloadUrl}]`. `primary: true`
        is reliable for picking the pruned checkpoint (e.g. v18.0 CoreShift has
        a 6.5 GB primary + a 13.5 GB FP32 secondary).
  - [x] `files[].downloadUrl` (== `/api/download/models/{versionId}`) returns a
        307 to a Cloudflare R2 signed URL. `curl -L` follows it; Bearer header
        is stripped by curl on cross-host redirect (safe). HEAD returns 403
        (R2 signed URLs are GET-only); a real GET works (verified via 1 KB
        range request → HTTP 206, binary stream).
  - [x] v1 syntax: **`civitai:<modelVersionId>`** (explicit pin, reproducible).
        404 on metadata triggers a clear hint pointing at the model-id-vs-
        version-id distinction.
- [x] **`scripts/add-model.sh` — Civitai branch** (AC: 1, 2, 3, 7, 8)
  - [x] `REPO=civitai:<id>` dispatches into `civitai_download()`; `FILE` is
        optional (defaults to the version's `primary` file)
  - [x] Fetches version metadata; downloads with `curl -SL -C -` (resume) and
        Bearer auth from `CIVITAI_API_KEY` (env > `.env`)
  - [x] Saves as `OUT=` if given, else the Civitai-reported `name`; SHA256
        is verified after download — confirmed `sha256: OK` on the real run
  - [x] 401/403/404 mapped to actionable messages; API key never logged
- [x] **Manifest / generator integration** (AC: 4)
  - [x] Documented `repo: civitai:<id>` in `models.yaml`'s schema-comment
  - [x] `apply-models.py`: relaxed `file:` so it's optional when `repo: civitai:…`
        + `out:` is set; the subprocess call to `add-model.sh` now passes
        `--file` only when provided
- [x] **`.env.example`** (AC: 6)
  - [x] Added a commented `CIVITAI_API_KEY=` block with a one-line note
- [x] **Add CyberRealistic Pony** (AC: 5)
  - [x] `make download-model REPO=civitai:2884631 NAME=cyberrealistic-pony \
        DIR=cyberrealistic-pony OUT=cyberrealistic-pony.safetensors` downloaded
        the 6.5 GB safetensors at ~83 MB/s in 1m 19s, SHA256 verified.
  - [x] Manifest entry added (`engine: sd-server`, Pony score-tag note in
        `notes:`); `make models-apply` → 16 enabled; `make sync-litellm`
        added 1 (LLMs untouched).
  - [x] `POST /v1/images/generations` returned a valid `1024×1024 8-bit RGB PNG`
        (~1.9 MB, 28.5 s) on the B70; `make status` shows it resident at
        6.8 GiB VRAM.
- [x] **Regression** (AC: 5, 7)
  - [x] HF download path unchanged (`add-model.sh` only adds a `civitai:`
        dispatch at the top of `download()`; the HF branch is byte-identical)
  - [x] `make test-models` still green from story 001; `make sync-litellm`
        idempotent on the LLM entries (+1 add, 0 re-point/delete)
- [x] **Docs** (AC: 6, 8)
  - [x] README image-generation section: "Civitai sources" paragraph with the
        `civitai:` syntax, the `.env` key, the Pony score-tag prompt convention
  - [x] `docs/troubleshooting.md`: "Civitai downloads fail" with 401/403/404
        and SHA256-mismatch hints
  - [x] Tracker updated; story → Review

## Dev Notes

- **Civitai download API**:
  - **Version metadata**: `GET https://civitai.com/api/v1/model-versions/{versionId}`
    returns JSON with `files: [{name, downloadUrl, primary, hashes: {SHA256, …},
    sizeKB, …}, …]`. Pick `files[].primary == true` (or the `.safetensors`).
  - **Direct download**: `https://civitai.com/api/download/models/{versionId}`
    redirects to a signed CDN URL; `curl -L` handles the redirect.
  - **Auth**: `Authorization: Bearer <CIVITAI_API_KEY>` — required for gated
    downloads, optional (but recommended for rate limits) for public ones.
- **Model id vs version id** is a real footgun: the user-visible URL
  `civitai.com/models/443821/cyberrealistic-pony` is the *model* id; downloads
  use the *version* id (each model has multiple versions). The spike decides
  whether `civitai:<id>` means a version id (pinned, reproducible) or a model id
  (auto-latest). Lean **version id** for the v1 cut — it matches the manifest's
  "exact-file-pin" ethos and avoids surprise upgrades on re-download.
- **Existing `add-model.sh`** already has a curl path with `-C -` (resume) and
  the post-download `OUT` rename — the Civitai branch differs only in (a) URL
  construction from the version metadata, (b) sending the Bearer header,
  (c) default filename from the metadata when `OUT` is omitted. No new
  dependencies; stdlib `python3` is enough for the JSON parsing inline.
- **`apply-models.py`** shells out to `add-model.sh` for downloads, so if
  `add-model.sh` learns `REPO=civitai:<id>`, `apply-models.py` inherits it for
  free. Confirm during implementation.
- **Manifest schema** stays as-is: reuse `repo:` with the `civitai:` prefix so
  no schema migration; `file:` is optional (defaults to the metadata's primary
  filename). `out:` still respected.
- **CyberRealistic Pony specifics**: SDXL/Pony fine-tune in single-file
  `.safetensors`. Same VRAM/perf envelope as SDXL base (~6.8 GB resident,
  ~30 s @ 1024² on the B70 per story 001). Pony positive prompts typically
  want `score_9, score_8_up, score_7_up, …` tags up front — note in the
  manifest entry's `notes:` and in the README.
- **Risks / dependencies**: story **001** must be merged first (the
  `engine: sd-server` tooling is what serves the model — this PR is stacked on
  the 001 branch). Civitai sometimes flags individual files as requiring a
  UI-side terms acceptance (rare on SDXL fine-tunes) — document the failure
  mode; there's no API workaround.
- **References**: [Story 001](001-sycl-image-generation.md),
  [`scripts/add-model.sh`](../../scripts/add-model.sh),
  [`models.yaml`](../../models.yaml),
  [Civitai REST API reference](https://github.com/civitai/civitai/wiki/REST-API-Reference).

### Testing

- **Functional**: `make add-model REPO=civitai:<vid> NAME=cyberrealistic-pony
  DIR=cyberrealistic-pony` writes an `~6 GB .safetensors` into
  `MODELS_DIR/cyberrealistic-pony/`. If Civitai surfaces SHA256, it matches.
- **End-to-end**: `make models-apply` regenerates the config; `POST /v1/images/generations`
  with `model: cyberrealistic-pony, size: 1024x1024, prompt: "score_9,
  score_8_up, photo of a wooden chair, studio lighting"` returns a valid PNG.
- **Auth**: with `CIVITAI_API_KEY` unset, a public version still downloads
  (best-effort); a gated version returns 401/403 with the AC8 hint.
- **Regression**: an existing HF download (any LLM repo) still works through
  `add-model.sh`; `make test-models` stays green (image models skipped);
  `make sync-litellm` only adds the new entry, leaving LLMs untouched.

### Validation findings (2026-05-29, on the live system)

- **Spike**: Civitai REST + signed-CDN download chain verified with a 1 KB
  range GET on the live API — HTTP 206, `binary/octet-stream`. `primary: true`
  identifies the pruned ~6.5 GB SDXL checkpoint cleanly.
- **`add-model.sh`** civitai branch: downloaded `cyberrealisticPony_v180Coreshift.safetensors`
  (6.5 GB) in 1m 19s @ ~83 MB/s, renamed to `cyberrealistic-pony.safetensors`
  via `--out`, **SHA256 matched** the value from Civitai metadata.
- **`apply-models.py`**: `make models-apply NO_DOWNLOAD=1` regenerated the
  config with 16 models (+1 cyberrealistic-pony). The first run would have
  driven the download via the new branch automatically too.
- **End-to-end via llama-swap**: `POST /v1/images/generations` with the
  `score_9, score_8_up, score_7_up, …` Pony prompt convention returned a
  valid `1024×1024 8-bit RGB PNG` (1.9 MB) in **28.5 s**.
- **State**: `make status` shows the model resident at 6.8 GiB VRAM (matches
  the SDXL envelope); `make sync-litellm` registered it with
  `mode: image_generation` and **left every existing entry untouched**
  (+1 add, 0 re-point, 0 delete).

## Change Log

| Date | Version | Description | Author |
|------|---------|-------------|--------|
| 2026-05-29 | 0.1 | Initial draft (planning; PR stacked on story 001) | James Huston / Claude |
| 2026-05-29 | 1.0 | Implemented + validated (CyberRealistic Pony 1024² @ 28.5 s; SHA256 OK). Status → Review. | Claude |

## Dev Agent Record

### Completion Notes

- Civitai support is additive — `add-model.sh` dispatches on the `civitai:`
  REPO prefix at the top of `download()`; the HF branch is unchanged.
- Reproducibility chose **version id** (not model id) — the user looks up the
  modelVersionId on Civitai's UI. A model-id auto-latest variant could be a
  future enhancement if it turns out to be friction.
- One small generator change beyond the script: `apply-models.py` now accepts
  manifest entries without `file:` when the source is `civitai:` and `out:` is
  set (the local filename is then driven by `out:`).

### File List

- `scripts/add-model.sh` — `civitai_resolve_key()` + `civitai_download()`;
  dispatch on `REPO=civitai:` in `download()`; FILE validation relaxed for civitai
- `scripts/apply-models.py` — `model_path()` accepts civitai entries without
  `file:`; `download_if_missing()` passes `--file` only when present
- `models.yaml` — schema-comment header documents `civitai:` prefix;
  `cyberrealistic-pony` entry (Civitai 2884631, `engine: sd-server`)
- `.env.example` — adds `CIVITAI_API_KEY` (commented)
- `README.md` — "Civitai sources" paragraph under image generation
- `docs/troubleshooting.md` — "Civitai downloads fail" section

## QA Results

- Functional: `add-model.sh REPO=civitai:2884631 …` downloads the primary file
  (SHA256 verified) into `MODELS_DIR/cyberrealistic-pony/`. **PASS**
- End-to-end: `POST /v1/images/generations` returns a valid 1024² PNG; the model
  is resident in VRAM; `make status` reflects it. **PASS**
- Auth: Bearer header from `CIVITAI_API_KEY` (via `.env`) accepted by Civitai
  (metadata + download both return 200/206). **PASS**
- Regression: HF code path unchanged in `add-model.sh`; existing chat models
  still pass `make test-models`; `sync-litellm` only added the new entry.
  **PASS**
- Error mapping: 401/403/404 paths and SHA256 mismatch path each `die` with
  an actionable hint (inspected; not exercised live in this session).
  **PASS by inspection**
