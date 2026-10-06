---
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary
#
# NVIDIA CORPORATION, its affiliates and licensors retain all intellectual
# property and proprietary rights in and to this material, related
# documentation and any modifications thereto. Any use, reproduction,
# disclosure or distribution of this material and related documentation
# without an express license agreement from NVIDIA CORPORATION or
# its affiliates is strictly prohibited.
name: resizable-streams
description: Client-driven resize on WebRTC/native streams end to end — enabling webrtc_resizable, receiving resize requests, debouncing and clamping, re-rendering at the new size, and the buffer rules (persistent maximum-size allocation, no CUDA memory pools). Use when user asks how to honour browser resize requests, why a resizable stream never decodes, why frames must come from cudaMalloc, or how to change a renderer's resolution at runtime.
license: LicenseRef-NvidiaProprietary
author: NVIDIA ovstream
tags:
  - ovstream
  - resize
  - webrtc
  - native
tools:
  - Read
  - Grep
---

# Resizable Streams

## Overview

A WebRTC/native server started with `webrtc_resizable=True` treats `width` / `height` as the maximum and takes the current resolution from each frame's dimensions. The SDK never resizes on its own: it delivers the client's validated request through `on_resize_request` and the application re-renders at the new size. SHM and CUDASHM behave this way always; RTSP never does. See the `stream-messages` skill for the message flow and the `streaming-frames` skill for the frame contract.

`webrtc_fit_client=True` delivers the resolution each connecting client requests through the same `on_resize_request` callback, so the first frame already matches it. On a non-resizable stream it instead becomes the fixed resolution, and the buffer rules below do not apply.

## Buffer rules

With frame copying disabled the encoder registers the application's buffer directly, which imposes two rules:

- The buffer is a persistent allocation at the maximum size; each frame is a sub-region with the maximum pitch, so a resize never reallocates. The encoder may still read a slot after `stream_video` returns, so keep a small ring rather than rewriting the slot just submitted.
- The allocation is plain device memory (`cudaMalloc` / `cudaMallocPitch`). The encoder cannot register memory from a CUDA memory pool (`cudaMallocAsync`, Warp's default allocator); `stream_video` rejects such frames on a resizable stream with an explicit error. In Warp, allocate under `wp.ScopedMempool(device, False)`.

> **Source:** `examples/python/app_stream/app_stream/frames.py` snippets `frame-ring` and `stage-frame`

## Register, debounce, clamp, apply

`on_resize_request` is registered before `start()` (the config handshake tells the client whether resize is available). Requests are queued to the main thread, debounced until they stop arriving, clamped to the alignment rules (width a multiple of 8, even height) and the frame-generation minimum, and applied by rewriting the RenderProduct's `resolution` attribute; the next render step produces the new size and the frame dimensions carry it to the encoder.

> **Source:** `examples/python/app_stream/app_stream/streaming.py` snippet `register-control-channel`, `app_stream/app.py` snippet `apply-resize`, `app_stream/scene.py` snippet `runtime-product-writes`

## Verify

`examples/webrtc_client/playwright_client.py --signal-port 49100 --app-stream-check --resize WxH` (needs `pip install playwright` and Google Chrome) connects a real Chrome, requests the size and checks the video element follows. Or connect the bundled `examples/webrtc_client` page by hand, enable Fit to window and confirm the decoded size follows the window.

## Common Pitfalls

- A resizable stream that connects but never decodes, with `nvstPushStreamData` errors after the first frames, is the memory-pool case above.
- Without `webrtc_resizable` the server refuses resize requests and the browser merely scales the video; that looks like a resize but the encoder resolution is unchanged.
- Resizable mode disables StreamSDK's QoS-driven scaling; pair it with `on_qos` if the application wants to follow the recommended resolution.
- Provision the maximum realistically; encoder memory is allocated at the maximum.
