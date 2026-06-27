---
name: protocol-selection
description: Choosing between WebRTC, RTSP, the native protocol, SHM, and CUDASHM. Use when user asks which transport to use, the trade-offs, what each protocol supports, or how to view a stream.
---

# Protocol Selection

## Overview

ovstream supports five transports in one library. You pick at runtime by passing the appropriate `ServerType` to `Server()` / `ovstream_create_server()`. You can run several simultaneously off the same CUDA buffer.

## Decision matrix

| Need                                            | Pick    |
|-------------------------------------------------|---------|
| Browser client, low setup friction              | WebRTC  |
| Industry-standard tools (VLC, ffplay), no input | RTSP    |
| Lowest latency, native StreamSDK client         | Native  |
| Same-machine, zero-encode, host-resident pixels (CPU consumer) | SHM     |
| Same-host, zero-encode, GPU-resident pixels (kernel/simulation consumer in another process or container) | CUDASHM |

If unsure, **start with WebRTC** — it has the broadest client compatibility (browsers, the bundled `examples/webrtc_client/`), supports input and message channels, and is what the examples default to.

## Feature support by protocol

| Feature                          | WebRTC | RTSP | Native | SHM | CUDASHM |
|----------------------------------|:------:|:----:|:------:|:---:|:-------:|
| Raw CUDA BGRA8 video             | ✓      | ✓    | ✓      | ✓   | ✓       |
| Pre-encoded H.264 / H.265        | ✓      | ✓    | ✓      | —   | —       |
| Pre-encoded AV1                  | ✓      | —    | ✓      | —   | —       |
| Audio (16-bit PCM)               | ✓      | —    | ✓      | —   | —       |
| Input from client (keyboard/mouse) | ✓    | —    | ✓      | ✓   | ✓       |
| Bidirectional messaging          | ✓      | —    | ✓      | ✓   | ✓       |
| Per-frame SEI metadata           | —      | ✓ (pre-encoded only) | —      | —   | —       |
| Multiple simultaneous clients    | depends | ✓   | depends | ✓ (multi-reader) | ✓ (multi-reader) |
| Browser-viewable                 | ✓      | —    | —      | —   | —       |
| Pixels delivered to consumer as  | encoded frames over network | encoded frames over network | encoded frames over network | host-mapped shared-memory bytes | CUDA device pointer (imported via `cudaIpcOpenMemHandle`) |
| Cross-machine                    | ✓      | ✓    | ✓      | —   | —       |
| Cross-container (same host)      | ✓      | ✓    | ✓      | requires `--ipc=host` | requires `--ipc=host --gpus all` |

## How to view each

- **WebRTC** — open [`examples/webrtc_client/index.html`](../../examples/webrtc_client/index.html), enter `host:signal_port`. Off-the-shelf WebRTC tooling (`webrtc-cli`, raw `RTCPeerConnection`) will **not** interoperate; the client must speak the StreamSDK signaling flavor that the bundled JS library implements.
- **RTSP** — any RTSP client. Quick checks: `ffplay rtsp://localhost:8554/stream`, or VLC → "Open Network Stream".
- **Native** — requires a native StreamSDK client; no browser equivalent. The bundled consumer is `ovstream.Client(ovstream.ClientType.NATIVE, server_ip=…)` (Python) or `ovstream_create_client(OVSTREAM_CLIENT_NATIVE, …)` (C); it connects over the network and decodes client-side via StreamSDK's NvStreamingMedia (which uses NVDEC on NVIDIA GPUs) into host BGRA8 frames, so the consumer needs its own NVIDIA GPU but can be on a different machine than the producer. The OpenCV viewer is `examples/python/native_client/main.py`; a headless C consumer is `examples/c/native_client/`. See `native-consumers` skill.
- **SHM** — programmatic only. `ovstream.Client(ovstream.ClientType.SHM, stream_name=…)` (Python) or `ovstream_create_client(OVSTREAM_CLIENT_SHM, …)` (C). The bundled OpenCV viewer is `examples/python/local_stream/main_viewer.py`. See `shm-consumers` skill.
- **CUDASHM** — programmatic only. `ovstream.Client(ovstream.ClientType.CUDASHM, stream_name=…)` (Python) or `ovstream_create_client(OVSTREAM_CLIENT_CUDASHM, …)` (C). The bundled OpenCV viewer is `examples/python/local_stream/main_cudashm_viewer.py` (it D2Hs each slot for screen display; real consumers do their work on the GPU directly). See `cudashm-consumers` skill.

## Picking multiple at once

Real apps often run two transports — e.g. WebRTC for interactive operators, RTSP for monitoring tools. The basic_stream example demonstrates this:

> **Source:** `examples/c/basic_stream/main.cu` snippet `stream-loop`

The same CUDA buffer is handed to every running server in the loop. Each server encodes / packetizes independently. The bottleneck is usually NVENC throughput, not CPU.

## Native vs WebRTC

Both use NVIDIA StreamSDK under the hood and share the signaling-port convention. The differences:

- **WebRTC** speaks ICE + DTLS + SRTP — designed to traverse NATs and run in a browser. Higher overhead, broader reach.
- **Native** is a leaner StreamSDK-proprietary protocol optimized for LAN-grade or controlled-environment latency. Lower overhead, requires a native client.

If you need a browser, you need WebRTC. If you control both ends and care about every millisecond, native is slightly leaner.

For cross-network deployments, both WebRTC and native need STUN (NAT discovery) and usually TURN (relay fallback). Supply them via `server.set_webrtc_ice_servers([...])` — see the `server-creation` skill for the API shape, pre-vs-post-start semantics, and the live-refresh path for time-limited TURN tokens.

## SHM vs CUDASHM

Both ship pixels to same-host consumers without encoding or networking. The difference is **where the pixels live when the consumer reads them**:

- **SHM** writes a `cudaMemcpy2D` D2H of each frame into a host-mapped shared-memory region. Consumers read CPU pixels directly. Ideal for compositors, screen recorders, browser-bridge processes (Electron N-API addons), and anything that ultimately needs CPU access to the bytes.
- **CUDASHM** keeps frames in GPU memory: the server pre-allocates a ring of CUDA buffers, exports IPC handles via `cudaIpcGetMemHandle`, and `cudaMemcpy2DAsync` D2Ds each new frame into the next slot. Consumers `cudaIpcOpenMemHandle` once at attach and read pixels directly from the imported device pointer — never staged through host memory. Ideal for simulation kernels, GPU post-processing, ML pipelines, and anything that wants the pixels as CUDA inputs.

Pick SHM if the consumer would have to upload to GPU anyway after reading. Pick CUDASHM if the consumer is already on the GPU and SHM would force a wasted host round-trip.

Important CUDASHM caveats:
- The consumer **must run in a separate process** from the producer. CUDA forbids `cudaIpcOpenMemHandle` in the process that called `cudaIpcGetMemHandle`. Docker containers on the same host are fine (with `--ipc=host --gpus all`).
- The consumer is responsible for keeping up — `slot_count` (default 4, range [2, 8]) is the ring depth; if the consumer's read kernel takes longer than the producer's wraparound interval, it reads garbage. Increase `cudashm_slot_count` for slower consumers.
- BGRA8 only (same as SHM).

## Common Pitfalls

- Don't expect input callbacks to fire on RTSP servers — RTSP has no input channel. Code that registers `on_input` on an RTSP server is fine (the registration succeeds) but the callback simply never fires.
- WebRTC signaling and stream ports are separate; both must be open. RTSP uses one port (the stream port doubles as the control port).
- SHM and CUDASHM stream names are case-sensitive and must match exactly between producer and reader. Pick something short and stable (e.g. `my-app-output`). The two backends use distinct OS-level path prefixes internally, so the same `stream_name` can be used for a parallel SHM + CUDASHM pair off the same producer with no collision.
- Multiple WebRTC, native, or RTSP servers in one process require **explicit unique ports** — there is no auto-increment. A second WebRTC server that reuses the default `49100` will fail to start with an address-in-use error. Either assign distinct `webrtc_signal_port` / `stream_port` per server, or use SHM / CUDASHM (where names are the only differentiator and have no port conflict to worry about).
- CUDASHM same-process attach fails (CUDA limitation). Running `main.py` + `main_cudashm_viewer.py` co-located in one Python script is not supported; launch the producer and the viewer in separate shells / processes.
