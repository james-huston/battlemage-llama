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
# System packages + Level Zero loader
#
# `libze1` = the generic Level Zero loader (from intel/level-zero). It's
# separate from libze-intel-gpu1 (the Battlemage driver implementation).
# We install libze1 from the oneAPI/intel-graphics apt repo since that's
# well-maintained; the GPU-specific driver we'll override below.
# -----------------------------------------------------------------------------
ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        git cmake ninja-build build-essential pkg-config \
        ca-certificates curl wget clinfo \
        libze1 && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# -----------------------------------------------------------------------------
# Intel graphics driver (compute-runtime + IGC) from GitHub releases
#
# We bypass Intel's apt repository (repositories.intel.com/gpu/ubuntu noble)
# because its 'unified' channel lags upstream — as of April 2026 it ships
# 25.18, which does NOT recognize Battlemage B70 (device 0xe223). Observed
# failure mode: `sycl-ls` reports `Platforms: 0` inside the container.
#
# Installing directly from GitHub gives us 26.18.38308.1 (2026-05), known-good
# on the B70. (Earlier 26.09.37435.1 also worked; bumped for newer-silicon fixes.)
#
# To bump versions:
#   * https://github.com/intel/compute-runtime/releases → COMPUTE_RUNTIME_VERSION
#   * https://github.com/intel/intel-graphics-compiler/releases → IGC_VERSION + IGC_BUILD
#   * libigdgmm12 version can float independently; pin whatever the compute-runtime
#     release notes recommend (usually stable across several compute-runtime releases).
#
# Override at build time:
#   docker compose build \
#     --build-arg COMPUTE_RUNTIME_VERSION=26.11.xxxxx.x \
#     --build-arg IGC_VERSION=2.31.0 \
#     --build-arg IGC_BUILD=21000
# -----------------------------------------------------------------------------
ARG COMPUTE_RUNTIME_VERSION=26.18.38308.1
ARG IGC_VERSION=2.34.4
ARG IGC_BUILD=21428
ARG GMMLIB_VERSION=22.10.0

RUN mkdir -p /tmp/neo && cd /tmp/neo && \
    # IGC (Intel Graphics Compiler)
    wget -q "https://github.com/intel/intel-graphics-compiler/releases/download/v${IGC_VERSION}/intel-igc-core-2_${IGC_VERSION}+${IGC_BUILD}_amd64.deb" && \
    wget -q "https://github.com/intel/intel-graphics-compiler/releases/download/v${IGC_VERSION}/intel-igc-opencl-2_${IGC_VERSION}+${IGC_BUILD}_amd64.deb" && \
    # GMM library (graphics memory manager)
    wget -q "https://github.com/intel/compute-runtime/releases/download/${COMPUTE_RUNTIME_VERSION}/libigdgmm12_${GMMLIB_VERSION}_amd64.deb" && \
    # compute-runtime: Level Zero GPU driver + OpenCL ICD + ocloc compiler tool
    wget -q "https://github.com/intel/compute-runtime/releases/download/${COMPUTE_RUNTIME_VERSION}/libze-intel-gpu1_${COMPUTE_RUNTIME_VERSION}-0_amd64.deb" && \
    wget -q "https://github.com/intel/compute-runtime/releases/download/${COMPUTE_RUNTIME_VERSION}/intel-opencl-icd_${COMPUTE_RUNTIME_VERSION}-0_amd64.deb" && \
    wget -q "https://github.com/intel/compute-runtime/releases/download/${COMPUTE_RUNTIME_VERSION}/intel-ocloc_${COMPUTE_RUNTIME_VERSION}-0_amd64.deb" && \
    # Install — apt handles conflicts with whatever the base image shipped
    apt-get update && \
    apt-get install -y --no-install-recommends ./*.deb && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/* /tmp/neo && \
    # Verify the override took effect
    dpkg -l libze-intel-gpu1 intel-opencl-icd

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
ARG LLAMA_SWAP_VERSION=217
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
