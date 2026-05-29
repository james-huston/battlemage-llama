# Story 002: Civitai model downloads (proving model: CyberRealistic Pony)

## Status

Draft

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

- [ ] **Spike — pin the Civitai download API specifics** (AC: 1, 2)
  - [ ] Confirm `GET https://civitai.com/api/v1/model-versions/{versionId}`
        response shape and whether `files[].primary` is reliable for picking
        the checkpoint to fetch
  - [ ] Confirm the direct download URL pattern
        (`/api/download/models/{versionId}` vs `files[].downloadUrl`) and
        how the signed-CDN redirect plays with `curl -L` + Bearer auth
  - [ ] Decide on the v1 syntax: **`civitai:<modelVersionId>`** (explicit pin,
        reproducible — recommended) vs `civitai:<modelId>` (auto-latest, easier
        but version-drifts). The user-visible Civitai URL `civitai.com/models/<modelId>/…`
        is the model id; downloads use a different version id.
  - [ ] Note any gating that even auth doesn't bypass (some files require
        accepting terms on the web UI first)
- [ ] **`scripts/add-model.sh` — Civitai branch** (AC: 1, 2, 3, 7, 8)
  - [ ] Detect `REPO=civitai:<id>` prefix; make `FILE` optional (defaults to
        the version's primary file)
  - [ ] Fetch version metadata via `curl`; pick the primary `.safetensors`;
        download with `curl -L -C - …` and Bearer auth when `CIVITAI_API_KEY`
        is set
  - [ ] Save under `OUT=` if given, else the metadata `name`; verify the
        `SHA256` hash if Civitai provides one
  - [ ] Map HTTP 401/403/404 to the clear hints from AC8; never log the API key
- [ ] **Manifest / generator integration** (AC: 4)
  - [ ] Document `repo: civitai:<id>` in `models.yaml`'s schema-comment header
  - [ ] Verify `apply-models.py` needs no code change (it shells out to
        `add-model.sh` — if not, branch the download path)
- [ ] **`.env.example`** (AC: 6)
  - [ ] Add a commented `CIVITAI_API_KEY=` block with a one-line explanation
- [ ] **Add CyberRealistic Pony** (AC: 5)
  - [ ] `make add-model REPO=civitai:<versionId> NAME=cyberrealistic-pony \
        DIR=cyberrealistic-pony` to download
  - [ ] Add the `models.yaml` entry (`engine: sd-server`, `litellm.description`
        notes the Pony score-tag prompt convention), `make models-apply`,
        `make sync-litellm`
  - [ ] Smoke test: `POST /v1/images/generations` returns a valid PNG;
        `make status` shows the model resident
- [ ] **Regression** (AC: 5, 7)
  - [ ] An HF download via `add-model.sh` still works (e.g. one tiny test or
        rely on the unchanged HF code path)
  - [ ] `make test-models` still green (image models skipped);
        `make sync-litellm` idempotent on existing LLM entries
- [ ] **Docs** (AC: 6, 8)
  - [ ] README: an "Adding a Civitai model" subsection under image generation
        with the `civitai:` syntax, the `.env` key, and the Pony prompt
        convention
  - [ ] `docs/troubleshooting.md`: Civitai 401/403/404 entries
  - [ ] Flip this story to `Review`, update the tracker

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

## Change Log

| Date | Version | Description | Author |
|------|---------|-------------|--------|
| 2026-05-29 | 0.1 | Initial draft (planning; PR stacked on story 001) | James Huston / Claude |

## Dev Agent Record

### Completion Notes

### File List

## QA Results
