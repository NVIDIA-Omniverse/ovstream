---
name: composing-libraries
description: Composing ovstream with ovrtx, ovstage and ovui into one application — ownership of the scene, frames and CUDA streams, the per-tick order, one loop with two cadences, startup and shutdown order. Use when user asks how to build a streaming app from several OV libraries, how ovrtx output reaches ovstream, who owns the frame buffers, or in what order to create and tear down the renderer, UI and servers.
---

# Composing OV Libraries into a Streaming Application

## Overview

ovstream delivers frames; it does not render, own a scene or draw UI. An application built from the OV libraries gives each library one job and keeps the frame path on the GPU:

| Library | Owns | Hands over |
|---|---|---|
| ovstage | the USD scene, per-frame writes under ordinals | a stage the renderer attaches to |
| ovrtx | rendering, DLSS Frame Generation | RGBA8 render vars mapped to CUDA |
| ovui | the application chrome (headless Vulkan, full frame) | an RGBA8 image exported to CUDA |
| the application | CUDA stream, frame ring, cadence, input routing | BGRA8 frames with a CUDA event |
| ovstream | transports, encoding, the browser control channel | callbacks on a worker thread |

The reference implementation is `examples/python/app_stream`; the minimal ovrtx + ovstream pairing is `examples/python/ovrtx_stream`.

## One loop, two cadences

The application loop ticks at the delivered rate with `ovstream_utils.Loop`; a render step is owed at the base rate, which is the delivered rate divided by the frames the renderer returns per step (see the `frame-generation-streaming` skill). The renderer is stepped asynchronously and polled without blocking, at most one step in flight; a step owed while one is running is counted as missed rather than stalling presentation.

> **Source:** `examples/python/app_stream/app_stream/render.py` snippet `async-step`

## Per-tick order

Input first, so the render submit carries this tick's camera; then collect a finished step, submit the next one if owed, present one frame, publish.

> **Source:** `examples/python/app_stream/app_stream/app.py` snippet `tick-order`

## Frame ownership

Frames live in a ring the application owns, allocated once at the maximum size on a plain device allocation (not a CUDA memory pool). A frame is copied from the mapped render var into a slot on the application's CUDA stream, converted to BGRA8 in place, composited, and handed to ovstream with the slot's event as the sync hint. One conversion per delivered frame, after everything else has drawn into it.

> **Source:** `examples/python/app_stream/app_stream/frames.py` snippets `frame-ring` and `stage-frame`

The ovui chrome is drawn at the frame size around a blank viewport region and exported with `omni.ui.standalone.headless_frame`: a Vulkan-to-CUDA semaphore wait queued on the null stream, a copy onto the application's stream, a consumed signal after the composite. The application's stream is a blocking stream, so the null-stream operations bracket the copy correctly.

> **Source:** `examples/python/app_stream/app_stream/ui/host.py` snippet `export-panel`

## Startup and shutdown order

Startup: stage, renderer attached to the stage, scene populated, frame pipeline, servers (callbacks registered first), then the loop. Shutdown mirrors it: servers first, so no callback arrives during teardown; then any in-flight step is finished; then the application's CUDA resources; then the UI and the renderer; then `ovstream.shutdown()`. Every renderer output the application still references must be released before the renderer is destroyed.

> **Source:** `examples/python/app_stream/app_stream/app.py` snippet `shutdown-order`

## Common Pitfalls

- Uploading the rendered frame into the UI toolkit to draw a viewport widget costs a host round trip when the toolkit has no GPU image path; render at the viewport's size and blit the scene into the exported UI frame instead.
- An SPG output on the same RenderProduct as `LdrColor` disables frame generation, so ovrtx's own BGRA8 output is only for applications that do not want generation.
- Reconfiguring the pacing loop resets its statistics; only reconfigure on a sustained change in frames per step.
- Callbacks from ovstream arrive on a worker thread; queue them and apply on the loop thread so scene writes never race the render submit.
