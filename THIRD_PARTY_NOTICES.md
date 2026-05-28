# Third-Party Notices

ovstream binary distributions bundle a small number of third-party components alongside the NVIDIA-authored library. This document is a **discoverable summary** of those components and the licenses they ship under. The legal-grade binding texts, full source archives where required, and per-version attributions live in the `PACKAGE-LICENSES/` directory inside every installed wheel and every extracted GitHub Release archive — refer to those for the authoritative texts.

## Bundled components

### NVIDIA StreamSDK

- **License:** Proprietary, NVIDIA Corporation. StreamSDK falls under the same NVIDIA Proprietary terms as ovstream itself — see `PACKAGE-LICENSES/ovstream-LICENSE.txt` for the binding text. There is no separate StreamSDK license file.
- **What it is:** The streaming engine that powers ovstream's WebRTC and native protocols. Provides hardware-accelerated H.264 / H.265 / AV1 encoding via NVENC, signaling, and bidirectional data channels.
- **OSS components carried by StreamSDK:** the StreamSDK server libraries that ovstream redistributes statically link or dynamically depend on a handful of upstream OSS components — OpenSSL, POCO, libsrtp, ENet, Opus, spdlog, libguarded, libre, and STUN Client. The aggregated upstream license texts for those components live at `PACKAGE-LICENSES/oss-licenses-streamsdk-server.md` inside the wheel / release archive.

### GStreamer (≥ 1.24)

- **License:** LGPL-2.1-or-later.
- **What it is:** The multimedia framework that powers ovstream's RTSP protocol. Provides the RTSP server, the appsrc pipeline, and the runtime element loader. The bundle includes several supporting LGPL components alongside GStreamer itself — glib, gst-orc, gst-plugins-base, gst-plugins-good, gst-rtsp-server, libgettext, libiconv, and zlib — each redistributed pristine from upstream.
- **Where to find the binding text:** `PACKAGE-LICENSES/gstreamer-LICENSE.txt`, plus the per-component `*-LICENSE.txt` / `*-LICENSE.LIB` files in the same directory.
- **Source availability:** the upstream sources are available unmodified from each project's official site. We can also supply source on written request to the contact in `ovstream-LICENSE.txt`.

### `gstnvenc` (derivative of `gst-plugins-bad`)

- **License:** LGPL-2.1-or-later.
- **What it is:** ovstream bundles its own NVENC encoder GStreamer plugin (`gstnvenc.dll` / `libgstnvenc.so`), built from upstream `gst-plugins-bad` encoder sources. Encoder-only build (no decoders), so it does not depend on `gstcodecs` / `gstcodecparsers`.
- **Source archive:** because this is an LGPL derivative, the corresponding source is shipped alongside the binary inside `PACKAGE-LICENSES/gstnvenc-source.zip` for compliance with the LGPL's source-availability requirement.
- **Where to find the binding text:** `PACKAGE-LICENSES/gst-plugins-bad-LICENSE.txt`.

### DLPack

- **License:** [Apache License 2.0](https://github.com/dmlc/dlpack/blob/main/LICENSE).
- **What it is:** A single-header C ABI for cross-framework tensor sharing (the `DLTensor` struct and friends). Used by ovstream's `OVSTREAM_VIDEO_INPUT_TENSOR` path to accept frames from any DLPack-speaking producer (Warp, CuPy, PyTorch, ovrtx, etc.). The header lives in ovstream's source tree for implementation use only — it is not redistributed as a standalone file, but its type definitions are compiled into the shipped binary.
- **Attribution:** `dlpack — https://github.com/dmlc/dlpack — Copyright (c) 2017 by Contributors — Licensed under Apache License 2.0`.
- **Where to find the binding text:** `PACKAGE-LICENSES/dlpack-LICENSE.txt`.

### CUDA runtime (`cudart`)

- **License:** [NVIDIA CUDA Toolkit End-User License Agreement](https://docs.nvidia.com/cuda/eula/index.html). The redistributable runtime components (`cudart`) ship under ovstream's own NVIDIA Proprietary terms — see `PACKAGE-LICENSES/ovstream-LICENSE.txt` — as permitted by the CUDA EULA's redistribution clause.
- **What it is:** The runtime portion of the CUDA Toolkit, required for the zero-copy CUDA buffer streaming path. Loaded dynamically at runtime; the NVIDIA display driver provides the matching CUDA driver API.

## Why these components and not others

ovstream is intentionally minimal at runtime. Other libraries that appear during *development* of ovstream (build tooling, test harnesses, documentation generators) are not redistributed and are therefore not listed here.

## Reporting an issue with this notice

If a component is missing, mis-licensed, or out of date, please file an issue at [github.com/NVIDIA-Omniverse/ovstream/issues](https://github.com/NVIDIA-Omniverse/ovstream/issues).
