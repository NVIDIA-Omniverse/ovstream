<!--
SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: LicenseRef-NvidiaProprietary

NVIDIA CORPORATION, its affiliates and licensors retain all intellectual
property and proprietary rights in and to this material, related
documentation and any modifications thereto. Any use, reproduction,
disclosure or distribution of this material and related documentation
without an express license agreement from NVIDIA CORPORATION or
its affiliates is strictly prohibited.
-->
---
name: protocol-selection
description: Choosing between WebRTC, RTSP, the native protocol, and SHM. Use when user asks which transport to use, the trade-offs, what each protocol supports, or how to view a stream.
---

# Protocol Selection

## Overview

ovstream supports four transports in one library. You pick at runtime by passing the appropriate `ServerType` to `Server()` / `ovstream_create_server()`. You can run several simultaneously off the same CUDA buffer.

## Decision matrix

| Need                                            | Pick    |
|-------------------------------------------------|---------|
| Browser client, low setup friction              | WebRTC  |
| Industry-standard tools (VLC, ffplay), no input | RTSP    |
| Lowest latency, native StreamSDK client         | Native  |
| Same-machine, zero-encode, zero-network         | SHM     |

If unsure, **start with WebRTC** — it has the broadest client compatibility (browsers, the bundled `examples/webrtc_client/`), supports input and message channels, and is what the examples default to.

## Feature support by protocol

| Feature                          | WebRTC | RTSP | Native | SHM |
|----------------------------------|:------:|:----:|:------:|:---:|
| Raw CUDA BGRA8 video             | ✓      | ✓    | ✓      | ✓   |
| Pre-encoded H.264 / H.265        | ✓      | ✓    | ✓      | —   |
| Pre-encoded AV1                  | ✓      | —    | ✓      | —   |
| Audio (16-bit PCM)               | ✓      | —    | ✓      | —   |
| Input from client (keyboard/mouse) | ✓    | —    | ✓      | ✓   |
| Bidirectional messaging          | ✓      | —    | ✓      | ✓   |
| Per-frame SEI metadata           | —      | ✓ (pre-encoded only) | —      | —   |
| Multiple simultaneous clients    | depends | ✓   | depends | ✓ (multi-reader) |
| Browser-viewable                 | ✓      | —    | —      | —   |

## How to view each

- **WebRTC** — open [`examples/webrtc_client/index.html`](../../examples/webrtc_client/index.html), enter `host:signal_port`. Off-the-shelf WebRTC tooling (`webrtc-cli`, raw `RTCPeerConnection`) will **not** interoperate; the client must speak the StreamSDK signaling flavor that the bundled JS library implements.
- **RTSP** — any RTSP client. Quick checks: `ffplay rtsp://localhost:8554/stream`, or VLC → "Open Network Stream".
- **Native** — requires a native StreamSDK client. No browser equivalent.
- **SHM** — programmatic only. `ovstream.ShmClient` (Python) or `ovstream_shm_client_*` (C). See `shm-consumers` skill.

## Picking multiple at once

Real apps often run two transports — e.g. WebRTC for interactive operators, RTSP for monitoring tools. The basic_stream example demonstrates this:

> **Source:** `examples/c/basic_stream/main.cu` snippet `stream-loop`

The same CUDA buffer is handed to every running server in the loop. Each server encodes / packetizes independently. The bottleneck is usually NVENC throughput, not CPU.

## Native vs WebRTC

Both use NVIDIA StreamSDK under the hood and share the signaling-port convention. The differences:

- **WebRTC** speaks ICE + DTLS + SRTP — designed to traverse NATs and run in a browser. Higher overhead, broader reach.
- **Native** is a leaner StreamSDK-proprietary protocol optimized for LAN-grade or controlled-environment latency. Lower overhead, requires a native client.

If you need a browser, you need WebRTC. If you control both ends and care about every millisecond, native is slightly leaner.

## Common Pitfalls

- Don't expect input callbacks to fire on RTSP servers — RTSP has no input channel. Code that registers `on_input` on an RTSP server is fine (the registration succeeds) but the callback simply never fires.
- WebRTC signaling and stream ports are separate; both must be open. RTSP uses one port (the stream port doubles as the control port).
- SHM stream names are case-sensitive and must match exactly between producer and reader. Pick something short and stable (e.g. `my-app-output`).
- Multiple WebRTC, native, or RTSP servers in one process require **explicit unique ports** — there is no auto-increment. A second WebRTC server that reuses the default `49100` will fail to start with an address-in-use error. Either assign distinct `webrtc_signal_port` / `stream_port` per server, or use SHM (where names are the only differentiator and have no port conflict to worry about).
