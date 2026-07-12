# Prebuilt GStreamer WebRTC plugin for Raspberry Pi (aarch64)

Running the Reachy Mini daemon needs the GStreamer **Rust WebRTC plugin**
(`webrtcsink` / `webrtcsrc` from [`gst-plugins-rs`](https://gitlab.freedesktop.org/gstreamer/gst-plugins-rs)),
which is not packaged for Debian and is slow to compile on a Raspberry Pi 3. These
files build it **off-device** and let you drop the result onto the Pi.

Target: **Raspberry Pi OS 64-bit (Debian trixie), aarch64.** The plugin is built
inside a `debian:trixie` arm64 container so its glibc and GStreamer (1.26) match the
Pi exactly. Pinned to `gst-plugins-rs` tag **0.14.1** and plugin `gst-plugin-webrtc`,
per the [official Reachy Mini instructions](https://huggingface.co/docs/reachy_mini/en/SDK/gstreamer-installation).

## Option A — GitHub Action (download prebuilt binaries)

The repo is public, so the workflow runs on a free native arm64 runner.

1. **Actions → "Build GStreamer WebRTC plugin (aarch64)" → Run workflow** (optionally
   set a different `gst-plugins-rs` tag). When it finishes, download the
   **`gst-plugin-webrtc-aarch64-trixie`** artifact — a
   `gst-plugins-rs-<tag>-aarch64-linux-gnu.tar.gz`.
2. Or push a tag `gst-webrtc-<tag>` (e.g. `gst-webrtc-0.14.1`) to also attach the
   tarball to a GitHub **Release**.

> If your fork cannot use arm64 runners, build locally with Option B instead.

## Option B — Cross-build locally with Docker

Requires Docker. On an x86_64 desktop this uses QEMU (slower); on an arm64 host it
runs natively.

```bash
# one-time on x86_64: enable arm64 emulation (the script does this automatically)
scripts/build-gst-webrtc-in-docker.sh
# -> writes dist/gst-plugins-rs-0.14.1-aarch64-linux-gnu.tar.gz

# knobs:
GST_PLUGINS_RS_TAG=0.14.1 PLUGINS="gst-plugin-webrtc" scripts/build-gst-webrtc-in-docker.sh
```

`scripts/build-gst-webrtc.sh` is the inner build (apt deps → rustup → cargo-c →
`cargo cinstall -p gst-plugin-webrtc`). Run it directly only inside a Debian trixie
aarch64 environment.

## Install on the Raspberry Pi

1. Copy and extract the tarball into `/opt` (it unpacks to `/opt/gst-plugins-rs`):

   ```bash
   scp gst-plugins-rs-0.14.1-aarch64-linux-gnu.tar.gz pi@reachy-mini.local:/tmp/
   ssh pi@reachy-mini.local
   sudo tar -C /opt -xzf /tmp/gst-plugins-rs-0.14.1-aarch64-linux-gnu.tar.gz
   ```

2. Install the GStreamer **runtime** packages the plugin needs at run time (the
   prebuilt `.so` is not enough on its own):

   ```bash
   sudo apt-get update
   sudo apt-get install -y \
     gstreamer1.0-plugins-good gstreamer1.0-plugins-bad gstreamer1.0-nice \
     gstreamer1.0-alsa libnice10 libportaudio2 python3-gi python3-gi-cairo
   ```

3. Point GStreamer at the plugin and verify:

   ```bash
   echo 'export GST_PLUGIN_PATH=/opt/gst-plugins-rs/lib/aarch64-linux-gnu:$GST_PLUGIN_PATH' >> ~/.bashrc
   source ~/.bashrc
   gst-inspect-1.0 webrtcsink   # should print the element, not "No such element"
   ```

   `webrtcsink` resolving confirms the daemon's earlier
   *"Failed to create webrtcsink element. Is the GStreamer webrtc rust plugin
   installed?"* is resolved.

## Notes

- **Version match**: build the plugin against the same GStreamer major/minor as the
  Pi. Building inside `debian:trixie` guarantees this. If your Pi image differs,
  change the container image accordingly.
- **Extra plugins**: set `PLUGINS="gst-plugin-webrtc gst-plugin-rtp"` if a pipeline
  needs the Rust RTP payloaders too; the WebRTC element alone matches the official
  instructions.
