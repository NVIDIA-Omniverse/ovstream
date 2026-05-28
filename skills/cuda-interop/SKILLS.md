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
name: cuda-interop
description: CUDA buffer allocation, pitch alignment, BGRA channel order, and ctypes-bound cudart usage. Use when user asks about CUDA interop, pitched allocations, frame layout, or zero-copy GPU integration.
---

# CUDA Interop

## Overview

ovstream consumes raw video frames as **BGRA8 CUDA buffers** directly from device memory. No CPU copy, no upload — the SDK reads the GPU pointer you hand it and encodes via NVENC.

The frame descriptor (`VideoFrame` / `ovstream_video_frame_t`) holds:

- `buffer` — a CUDA device pointer.
- `width`, `height` — pixel dimensions.
- `pitch_bytes` — row stride in bytes (≥ `width * 4`).

## Allocate with `cudaMallocPitch`

NVENC tolerates the buffer being row-padded for DMA alignment. Let CUDA pick the pitch:

> **Source:** `examples/c/basic_stream/main.cu` snippet `cuda-buffer-alloc`

`cudaMallocPitch(&buf, &pitch, widthBytes, height)` returns a device pointer plus the actual pitch CUDA picked (often `width * 4` rounded up to a 256 / 512-byte multiple). Pass that exact `pitch` into the `VideoFrame.pitch_bytes` field.

## Python: ctypes-bound cudart

The Python wheel bundles `cudart64_*.dll` / `libcudart.so` alongside `ovstream.dll`, so a Python app can use ctypes to call `cudaMalloc` / `cudaMemset` / `cudaFree` directly without any extra CUDA Python package. The example discovers the bundled CUDA runtime via `ovstream._bindings._find_library()` and loads it with ctypes.

> **Source:** `examples/python/basic_stream/main.py` snippet `bundled-cudart`

If you'd rather use a higher-level CUDA Python package, [`CuPy`](https://cupy.dev), [`PyCUDA`](https://documen.tician.de/pycuda/), or [`NVIDIA Warp`](https://developer.nvidia.com/warp-python) all expose `__cuda_array_interface__` or a `.data_ptr()` accessor that yields a raw device pointer — pass that as `VideoFrame.buffer`. See the `ovrtx_stream` example for a Warp-based version.

## BGRA, not RGBA

ovstream's CUDA video format is BGRA8 (blue first), not RGBA8. If your producer outputs RGBA, swizzle on the GPU before submitting. (At the time of writing, ovrtx's `LdrColor` render var is RGBA8 — a producer-side BGRA8 path is in flight; until that lands, the `ovrtx_stream` example does the swizzle itself.) Sketch:

```cpp
// CUDA kernel sketch
uint8_t* px = buffer + y * pitch + x * 4;
px[0] = rgba[2];  // B
px[1] = rgba[1];  // G
px[2] = rgba[0];  // R
px[3] = rgba[3];  // A
```

A future ovrtx release may emit BGRA8 directly, removing the swizzle step.

## Lifetime / re-use pattern

The recommended pattern is **one long-lived CUDA buffer that you re-fill in place** each frame, not a per-frame allocation. ovstream does not copy the buffer; the encoder reads it asynchronously after `stream_video` returns, so naive per-frame `cudaMalloc` / `cudaFree` risks freeing the buffer while NVENC is still reading.

A long-lived buffer is also cheaper — no allocator churn.

## Synchronization

`stream_video` does **not** synchronize internally by default — the caller is responsible for making sure the buffer's pixels are visible to the encoder when the call lands. Two patterns:

- **Caller pre-syncs.** Leave `frame.sync` zero-initialized (the default). The contract is "the buffer is safe to read on entry to `stream_video`". `cudaDeviceSynchronize` between your render kernel and `stream_video` is sufficient (and is what `basic_stream` does for simplicity).
- **Caller hands a sync hint.** Set `frame.sync.wait_event` (a `cudaEvent_t` recorded on your stream after the producer kernel) or `frame.sync.stream` (the CUDA stream the kernel ran on). ovstream chains on the event/stream without a global device sync. SHM uses `cudaStreamWaitEvent` and avoids any host block; RTSP / WebRTC host-block on the event (or stream) before handing the buffer to the encoder. `wait_event` takes precedence when both are set.

The sync hint is the right pattern for production render loops that already manage their own CUDA streams — it avoids the global `cudaDeviceSynchronize` cost between producer and ovstream.

## Multi-server zero-copy

When you feed multiple servers from one buffer (e.g. WebRTC + RTSP simultaneously, see `basic_stream` example), each server reads the same device pointer. No extra copies — NVENC encodes once per server, but the source data is shared.

## Key Types / Functions

| Python | C |
|--------|---|
| `ovstream.VideoFrame(buffer=ptr, width=w, height=h, pitch_bytes=p)` | `ovstream_video_frame_t { .buffer = ptr, .width = w, .height = h, .pitch_bytes = p }` |
| Any ctypes / CuPy / Warp device pointer (int) | `void*` device pointer |

## Common Pitfalls

- **BGRA, not RGBA.** This is the #1 source of "my stream looks blue-tinted" reports.
- **Don't pass a host pointer.** `buffer` must be a CUDA *device* pointer. ovstream does not check (passing a host pointer crashes inside NVENC).
- **Don't free the buffer during streaming.** Wait for `stop()` (or at least one `stream_video` call after the last one you care about) before `cudaFree`.
- **`pitch * height`, not `width * 4 * height`.** If your frame producer asserts on `pitch == width * 4`, you may need to allocate with plain `cudaMalloc` instead. That works too, but you lose the alignment benefit.
- **Default CUDA context.** ovstream uses the calling thread's current CUDA context. If you've set a non-default context, make sure it's current on the thread that calls `stream_video`.
