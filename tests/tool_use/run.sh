#!/usr/bin/env bash
# tests/tool_use/run.sh
# ---------------------------------------------------------------------------
# Bootstrap a venv, install pinned deps, and run the tool-use test suite
# against the llama-swap endpoint. Any args are passed through to test_tools.py
# (e.g. a model name to restrict the run).
#
# Environment:
#   LLAMA_SWAP_URL          base URL (default http://localhost:11434)
#   COLD_TIMEOUT_SECS       first-request timeout per model (default 120)
#   WARM_TIMEOUT_SECS       subsequent-request timeout (default 60)
#   TOOL_TEST_MAX_TOKENS    cap on tool-call max_tokens (default 256)
#   TOOL_TEST_FINAL_TOKENS  cap on final-answer max_tokens (default 128)
# ---------------------------------------------------------------------------
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${SCRIPT_DIR}/.venv"
REQ_FILE="${SCRIPT_DIR}/requirements.txt"
STAMP_FILE="${VENV_DIR}/.requirements.sha256"

# Pick a python interpreter; prefer python3.
PYTHON_BIN="${PYTHON_BIN:-python3}"
if ! command -v "${PYTHON_BIN}" >/dev/null 2>&1; then
  echo "error: ${PYTHON_BIN} not found on PATH" >&2
  exit 2
fi

# Build venv if missing.
if [[ ! -x "${VENV_DIR}/bin/python" ]]; then
  echo "[run.sh] creating venv at ${VENV_DIR}"
  "${PYTHON_BIN}" -m venv "${VENV_DIR}"
fi

# Re-install deps when requirements.txt changes.
REQ_HASH="$(sha256sum "${REQ_FILE}" | awk '{print $1}')"
NEED_INSTALL=1
if [[ -f "${STAMP_FILE}" ]] && [[ "$(cat "${STAMP_FILE}")" == "${REQ_HASH}" ]]; then
  NEED_INSTALL=0
fi
if [[ "${NEED_INSTALL}" -eq 1 ]]; then
  echo "[run.sh] installing requirements"
  "${VENV_DIR}/bin/pip" install --quiet --upgrade pip
  "${VENV_DIR}/bin/pip" install --quiet -r "${REQ_FILE}"
  echo "${REQ_HASH}" > "${STAMP_FILE}"
fi

exec "${VENV_DIR}/bin/python" "${SCRIPT_DIR}/test_tools.py" "$@"
