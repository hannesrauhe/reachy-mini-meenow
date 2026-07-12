#!/usr/bin/env bash
# Cross-build the GStreamer Rust WebRTC plugin for aarch64 (Raspberry Pi / Debian
# trixie) from any desktop, using a Debian trixie arm64 container.
#
# On an x86_64 desktop this uses QEMU emulation (slow but exact); on an arm64 host
# it runs natively. Requires Docker. The build itself lives in build-gst-webrtc.sh.
#
# Env overrides are forwarded: GST_PLUGINS_RS_TAG, PLUGINS, JOBS. Output tarball is
# written to ./dist (override with OUTDIR).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUTDIR="${OUTDIR:-$REPO_ROOT/dist}"
IMAGE="${IMAGE:-debian:trixie}"
mkdir -p "$OUTDIR"

if ! command -v docker >/dev/null 2>&1; then
  echo "error: docker is required" >&2; exit 1
fi

# Ensure arm64 can run (native on arm64 hosts; QEMU/binfmt on x86_64).
if ! docker run --rm --platform linux/arm64 "$IMAGE" true 2>/dev/null; then
  echo "==> Registering QEMU arm64 emulation (needs privileged docker)"
  docker run --privileged --rm tonistiigi/binfmt --install arm64
fi

exec docker run --rm --platform linux/arm64 \
  -e GST_PLUGINS_RS_TAG -e PLUGINS -e JOBS \
  -v "$REPO_ROOT:/src:ro" -v "$OUTDIR:/out" \
  "$IMAGE" bash -c 'OUTDIR=/out PREFIX=/opt/gst-plugins-rs bash /src/scripts/build-gst-webrtc.sh'
