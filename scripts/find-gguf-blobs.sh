#!/usr/bin/env bash
#
# find-gguf-blobs.sh
#
# Map Ollama model names to their GGUF blob file paths. Output is formatted
# so you can copy-paste the container paths directly into your llama-swap
# config.yaml.
#
# Run this on the HOST (not inside the container).
#
# Usage:
#   ./scripts/find-gguf-blobs.sh [ollama-models-dir]
#
# Defaults:
#   ollama-models-dir = /opt/apps/ollama-models
#
# Requires: sudo (to read manifest files owned by the ollama user), python3

set -euo pipefail

MODELS_DIR="${1:-/opt/apps/ollama-models}"

if [[ ! -d "${MODELS_DIR}/manifests" ]]; then
    cat >&2 <<EOF
error: ${MODELS_DIR}/manifests does not exist.

Is Ollama installed and has it downloaded any models? If you keep models
somewhere other than /opt/apps/ollama-models, pass the path as the first
argument:
    $0 /path/to/ollama-models

EOF
    exit 1
fi

# Header
cat <<EOF
# Model → blob mapping for battlemage-llama / llama-swap
#
# Paths in the "Container Path" column are what you put into your
# llama-swap.yaml — they reference the bind-mounted /models/ directory
# inside the container.
#
EOF

printf "%-38s  %-76s  %s\n" "Ollama model" "Container path (for llama-swap.yaml)" "Size"
printf "%-38s  %-76s  %s\n" "------------" "-----------------------------------" "----"

shopt -s nullglob
count=0
for manifest in "${MODELS_DIR}"/manifests/registry.ollama.ai/library/*/*; do
    model_name=$(echo "$manifest" | awk -F/ '{print $(NF-1)":"$NF}')

    # Pull the GGUF layer digest from the manifest JSON. Skip manifests that
    # don't have a model layer (e.g. cloud-hosted models) or malformed JSON.
    blob=$(sudo cat "$manifest" 2>/dev/null | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
except Exception:
    sys.exit(0)
layers = d.get('layers') or []
for l in layers:
    if 'application/vnd.ollama.image.model' in l.get('mediaType', ''):
        print(l['digest'].replace('sha256:', 'sha256-'))
        break
" || true)

    if [[ -z "${blob}" ]]; then
        continue
    fi

    host_path="${MODELS_DIR}/blobs/${blob}"
    container_path="/models/blobs/${blob}"
    size=$(sudo du -h "${host_path}" 2>/dev/null | awk '{print $1}' || echo "?")

    printf "%-38s  %-76s  %s\n" "${model_name}" "${container_path}" "${size}"
    count=$((count + 1))
done

echo
if [[ $count -eq 0 ]]; then
    echo "No local GGUF models found under ${MODELS_DIR}." >&2
    echo "(Cloud-only models like *:cloud are skipped — they have no local blob.)" >&2
    exit 1
fi

echo "Found ${count} local model(s). Paste the container paths above into"
echo "config/llama-swap.yaml under the appropriate model entries."
