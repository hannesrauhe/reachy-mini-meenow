#!/usr/bin/env bash
# Build the GStreamer Rust WebRTC plugin (gst-plugins-rs) for the host architecture.
#
# Designed to run inside a Debian trixie environment whose glibc + GStreamer match
# the target (Reachy Mini Lite on a Raspberry Pi running Raspberry Pi OS 64-bit /
# Debian trixie). Follows the official Reachy Mini instructions:
#   https://huggingface.co/docs/reachy_mini/en/SDK/gstreamer-installation
#
# Env overrides:
#   GST_PLUGINS_RS_TAG  gst-plugins-rs git tag           (default: 0.14.1)
#   PLUGINS             space-separated cargo -p packages (default: gst-plugin-webrtc)
#   PREFIX              install prefix                    (default: /opt/gst-plugins-rs)
#   OUTDIR              where the tarball is written      (default: $PWD/dist)
#   JOBS                cargo --jobs N (set 1 on low-RAM hosts; default: unset)
set -euo pipefail

GST_PLUGINS_RS_TAG="${GST_PLUGINS_RS_TAG:-0.14.1}"
PLUGINS="${PLUGINS:-gst-plugin-webrtc}"
PREFIX="${PREFIX:-/opt/gst-plugins-rs}"
OUTDIR="${OUTDIR:-$PWD/dist}"
JOBS="${JOBS:-}"

SUDO=""; [ "$(id -u)" -eq 0 ] || SUDO="sudo"

echo "==> Installing build dependencies (apt)"
export DEBIAN_FRONTEND=noninteractive
$SUDO apt-get update
$SUDO apt-get install -y --no-install-recommends \
  build-essential curl ca-certificates git pkg-config python3 \
  libssl-dev libglib2.0-dev libcairo2-dev libgirepository1.0-dev \
  libgstreamer1.0-dev libgstreamer-plugins-base1.0-dev libgstreamer-plugins-bad1.0-dev

echo "==> GStreamer version: $(pkg-config --modversion gstreamer-1.0 2>/dev/null || echo unknown)"

if ! command -v cargo >/dev/null 2>&1; then
  echo "==> Installing Rust (rustup)"
  curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --profile minimal
fi
export PATH="${CARGO_HOME:-$HOME/.cargo}/bin:$PATH"

echo "==> Installing cargo-c"
cargo install cargo-c --locked

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
echo "==> Cloning gst-plugins-rs @ ${GST_PLUGINS_RS_TAG}"
git clone --depth 1 --branch "$GST_PLUGINS_RS_TAG" \
  https://gitlab.freedesktop.org/gstreamer/gst-plugins-rs.git "$WORK/gst-plugins-rs"
cd "$WORK/gst-plugins-rs"

$SUDO mkdir -p "$PREFIX"
$SUDO chown "$(id -u):$(id -g)" "$PREFIX"

JOBSARG=(); [ -n "$JOBS" ] && JOBSARG=(--jobs "$JOBS")
for pkg in $PLUGINS; do
  echo "==> Building $pkg (cargo cinstall, release)"
  cargo cinstall -p "$pkg" --prefix="$PREFIX" --release "${JOBSARG[@]}"
done

TRIPLE="$(gcc -dumpmachine)"   # e.g. aarch64-linux-gnu
PLUGIN_DIR="$PREFIX/lib/$TRIPLE/gstreamer-1.0"
echo "==> Installed plugins in $PLUGIN_DIR:"
ls -1 "$PLUGIN_DIR" 2>/dev/null || echo "  (expected .so not found at $PLUGIN_DIR — check cargo-c libdir)"

mkdir -p "$OUTDIR"
TARBALL="$OUTDIR/gst-plugins-rs-${GST_PLUGINS_RS_TAG}-${TRIPLE}.tar.gz"
tar -C "$(dirname "$PREFIX")" -czf "$TARBALL" "$(basename "$PREFIX")"
echo "==> Wrote $TARBALL"
ls -lh "$TARBALL"
