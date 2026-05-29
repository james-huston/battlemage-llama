# Improvements (BMAD stories)

Planned and in-progress improvements to battlemage-llama, tracked as
[BMAD](https://github.com/bmad-code-org/BMAD-METHOD)-style stories: we **plan
first** (write the story), **track progress** inside each story via the
`Tasks / Subtasks` checkboxes, and **track overall progress** in the table below.

## How this works

- One Markdown file per improvement, named `NNN-short-slug.md` (zero-padded,
  monotonic). Copy [`_TEMPLATE.md`](_TEMPLATE.md) to start a new one.
- Each story carries a **Status** that moves through:
  `Draft → Approved → InProgress → Review → Done`.
- Acceptance Criteria define "done"; Tasks/Subtasks are the checklist we tick off
  as we implement; Dev Notes capture the technical context so the work is
  self-contained.
- Keep the table below in sync whenever a story's status changes.

## Status

| # | Story | Status | Summary |
|---|-------|--------|---------|
| [001](001-sycl-image-generation.md) | SYCL image generation (stable-diffusion.cpp) | Draft | On-demand text-to-image through the llama-swap UI/`/v1/images/generations`, served on the B70 via SYCL |

_Overall: 0 done / 1 total._
