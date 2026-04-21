# Host setup for Intel Arc Pro B70 on Linux

Steps below get an Intel Arc Pro B70 (or any Battlemage / Xe2 card) recognized by the Linux kernel's `xe` driver and accessible to Docker. Only the kernel-side driver is needed on the host — userspace (oneAPI, compute-runtime, llama.cpp) all lives inside the container.

Tested on **Ubuntu 25.10 (Questing)** with kernel 6.17. Should also work on Ubuntu 24.04 via the Intel graphics PPA (older kernel + backports required) — see Intel's [client GPU docs](https://dgpu-docs.intel.com/driver/client/overview.html) for other distros.

## BIOS / firmware

Two BIOS settings matter:

- **Resizable BAR (ReBAR)**: enable. Required for Arc GPUs to expose all 32GB of VRAM. Without it the card will be severely bandwidth-limited.
- **CSM (Compatibility Support Module)**: disable. UEFI-only boot. Arc cards don't ship legacy VBIOS.

Verify ReBAR is actually active post-boot:

```bash
sudo lspci -vv -s 03:00.0 | grep -E "BAR|Region"
# Look for "Region" entries with sizes matching your VRAM (32GB → 32G BAR)
```

## Kernel driver: xe

Ubuntu 25.10 ships kernel 6.17 which has the `xe` driver for Battlemage baked in. Verify:

```bash
lspci -k -s 03:00.0
# Kernel driver in use: xe
```

If you see `i915` instead, you're on an older kernel that doesn't yet have `xe` for this silicon. Either upgrade to Ubuntu 25.10+ or add the `kobuk-team/intel-graphics` PPA:

```bash
sudo add-apt-repository ppa:kobuk-team/intel-graphics
sudo apt update
sudo apt install -y linux-generic-hwe-*  # or whatever current kernel package applies
sudo reboot
```

## Group membership

You need to be in the `render` and `video` groups to access `/dev/dri/renderD*`:

```bash
sudo usermod -aG render,video $USER
# Log out and back in, or:
newgrp render
```

Sanity check:

```bash
ls -la /dev/dri/
# Expect: crw-rw---- root render ... renderD128
```

## Docker

Install via the standard Docker Engine packages. On Ubuntu 25.10, the Noble (24.04) repo works — no Questing packages yet as of this writing:

```bash
# See https://docs.docker.com/engine/install/ubuntu/ for current instructions.
# Key steps:
sudo apt install -y ca-certificates curl
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
    -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc

# Use 'noble' codename (24.04) since Docker hasn't published for 'questing' yet
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
    https://download.docker.com/linux/ubuntu noble stable" | \
    sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin

sudo usermod -aG docker $USER
```

## Verify GPU visibility

Before building battlemage-llama, confirm everything is wired up:

```bash
# Kernel driver bound to the right device
lspci -k -s 03:00.0
# → Kernel driver in use: xe

# /dev/dri entries exist
ls -la /dev/dri/
# → renderD128 (primary) and possibly more

# Current user can access /dev/dri (after logout/login)
groups | grep -E "render|video"
```

If those three checks pass, you're ready. Proceed to the main [README](../README.md) for the container build.

## What you do NOT need to install on the host

This repo specifically tries to keep your host clean. You do **not** need:

- Intel oneAPI base toolkit (the container has its own)
- Intel compute-runtime packages (the container has its own)
- The Intel graphics apt repository on the host (only the kernel driver matters)
- `libze-intel-gpu1`, `intel-opencl-icd`, `clinfo`, etc. (container)
- `/etc/ld.so.conf.d/` entries for oneAPI libs (container)

If you installed any of these trying to make a native-host setup work and want to clean them up, apt purge them now — they're not needed for the container path.
