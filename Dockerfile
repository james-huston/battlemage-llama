# syntax=docker/dockerfile:1.6
#
# battlemage-llama — SYCL-accelerated llama.cpp + llama-swap for Intel Arc Pro B70
# (and other Battlemage / Arc GPUs). Self-contained: no host oneAPI install needed.
#
# Build:  docker compose build
# Run:    docker compose up -d
# Docs:   https://github.com/james-huston/battlemage-llama

ARG ONEAPI_IMAGE_TAG=2025.3.0-0-devel-ubuntu24.04
FROM intel/oneapi-basekit:${ONEAPI_IMAGE_TAG}

# -----------------------------------------------------------------------------
# System + Intel graphics runtime
#
# The base image already registers the Intel graphics APT repository
# (repositories.intel.com/gpu/ubuntu noble unified). We install the current
# Level Zero GPU driver + OpenCL ICD from it. As of April 2026, this channel
# ships compute-runtime 26.09+, which recognizes Battlemage B70 (PCI 0xe223).
#
# If this image fails to detect your B70 (run `docker compose run --rm
# llama-swap sycl-ls` — look for `[level_zero:gpu]` with your device ID),
# override with a newer compute-runtime by rebuilding with a manual install.
# See docs/troubleshooting.md for the fallback procedure.
# -----------------------------------------------------------------------------
ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        git cmake ninja-build build-essential pkg-config \
        ca-certificates curl wget \
        libze-intel-gpu1 libze1 intel-opencl-icd clinfo && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# -----------------------------------------------------------------------------
# Build llama.cpp with SYCL backend
#
# LLAMA_CPP_REF defaults to 'master' (always latest). Pin to a specific tag
# like 'b5678' for reproducible builds: docker compose build --build-arg
# LLAMA_CPP_REF=b5678
#
# -DGGML_SYCL=ON         enables the SYCL backend for Intel GPUs
# -DGGML_SYCL_F16=ON     uses FP16 on XMX engines — ~2.4x faster prompt prefill
# -DCMAKE_BUILD_TYPE=Release  standard optimized build
# -----------------------------------------------------------------------------
ARG LLAMA_CPP_REF=master
RUN git clone --depth 1 --branch ${LLAMA_CPP_REF} \
        https://github.com/ggml-org/llama.cpp.git /tmp/llama.cpp && \
    cd /tmp/llama.cpp && \
    cmake -B build -G Ninja \
        -DGGML_SYCL=ON \
        -DGGML_SYCL_F16=ON \
        -DCMAKE_C_COMPILER=icx \
        -DCMAKE_CXX_COMPILER=icpx \
        -DCMAKE_BUILD_TYPE=Release && \
    cmake --build build --config Release -j $(nproc) && \
    cmake --install build --prefix=/opt/llama-cpp && \
    echo "/opt/llama-cpp/lib" > /etc/ld.so.conf.d/llama-cpp.conf && \
    ldconfig && \
    rm -rf /tmp/llama.cpp

# -----------------------------------------------------------------------------
# Install llama-swap
#
# Override version at build time if needed:
#   docker compose build --build-arg LLAMA_SWAP_VERSION=200
# -----------------------------------------------------------------------------
ARG LLAMA_SWAP_VERSION=198
RUN wget -qO /tmp/llama-swap.tar.gz \
        "https://github.com/mostlygeek/llama-swap/releases/download/v${LLAMA_SWAP_VERSION}/llama-swap_${LLAMA_SWAP_VERSION}_linux_amd64.tar.gz" && \
    mkdir -p /opt/llama-swap/bin && \
    tar -C /opt/llama-swap/bin -xzf /tmp/llama-swap.tar.gz llama-swap && \
    rm /tmp/llama-swap.tar.gz && \
    /opt/llama-swap/bin/llama-swap --version

# -----------------------------------------------------------------------------
# Runtime configuration
# -----------------------------------------------------------------------------
ENV PATH=/opt/llama-swap/bin:/opt/llama-cpp/bin:${PATH}

# Hide CPU OpenCL from llama.cpp so it doesn't enumerate the Ryzen as a GPU.
# level_zero:* = "any/all Level-Zero GPUs" — the B70 shows up here.
ENV ONEAPI_DEVICE_SELECTOR=level_zero:*

# Some Level Zero init paths check sysman capabilities.
ENV ZES_ENABLE_SYSMAN=1

# llama-swap listen port — matches Ollama's default so LiteLLM / clients that
# expected Ollama on 11434 don't need reconfiguration.
EXPOSE 11434

# Default: run llama-swap with hot-reload on config changes.
# Config file is expected to be bind-mounted at /config/llama-swap.yaml.
CMD ["/opt/llama-swap/bin/llama-swap", \
     "--config", "/config/llama-swap.yaml", \
     "--listen", "0.0.0.0:11434", \
     "--watch-config"]
