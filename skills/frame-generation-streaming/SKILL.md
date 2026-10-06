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
name: frame-generation-streaming
description: Streaming DLSS Frame Generation output from ovrtx through ovstream — enabling it on the RenderProduct, classifying the frames each step returns, pacing generated frames into the delivered rate, detecting whether it engaged, and what to report. Use when user asks how to stream frame generation, why the stream shows 30 fps with generation on, how many frames a render step returns, or why generation did not engage.
license: LicenseRef-NvidiaProprietary
author: NVIDIA ovstream
tags:
  - ovstream
  - ovrtx
  - dlss
  - frame-generation
tools:
  - Read
  - Grep
---

# Streaming Frame Generation

## Overview

ovrtx enables DLSS Frame Generation from two attributes on the RenderProduct: `bool omni:rtx:dlss:frameGeneration` and `int omni:rtx:dlss:frameGenerationInterpolatedFrameCount`. When it engages, every render step returns N+1 frames: the interpolated frames first, the simulated frame last, with strictly increasing times that end at the step boundary. ovstream knows nothing about generation; the application presents the frames one per tick at the delivered rate.

Generation is a runtime finding. It needs the real-time path tracing render mode, `LdrColor` as the only render var on the product, both dimensions at least 126, an Ada or newer GPU and a supported driver; whether it engages on a given GPU, driver and display configuration is verified by counting frames, never assumed.

## Enable

Author the two attributes on the RenderProduct in the layer that defines it, and rewrite them at runtime through ovstage when the user toggles generation.

> **Source:** `examples/python/app_stream/app_stream/assets/render_products.usda` and `examples/python/app_stream/app_stream/scene.py` snippet `runtime-product-writes`

## Classify every step

Treat the frame contract as a specification and check it: the last frame ends at the step end, interpolated frames are evenly spaced, and exactly one frame per step was rendered. Timing alone cannot separate a generated frame from a camera firing twice per step; ovrtx marks generated frames with a `progression` of 0 (never rendered), so a step with more than one rendered frame is a scheduling error, not generation.

> **Source:** `examples/python/app_stream/app_stream/contract.py` snippet `classify-frames`

## Pace into the delivered rate

The delivered rate is the user-facing knob (60 Hz by default); the base render rate is the delivered rate divided by the observed frames per step. With one interpolated frame the renderer steps at 30 Hz and the loop presents one frame per 60 Hz tick. Fanning all N+1 frames into one tick is wrong: the encoder receives a burst and the client sees no benefit. Detection watches the last few steps and reconfigures the cadence only on a sustained change.

> **Source:** `examples/python/app_stream/app_stream/app.py` snippet `fg-detection`

## Report

Keep the rates apart: render steps per second, frames per second from the renderer, presents per second into the stream. Generated frames per second is never reported as simulation or input-response frequency. To measure what a client actually receives, give every delivered frame a readable identity: the example's `--marker` draws a checksummed pixel block carrying frame id, ordinal and class, and the browser test client reads it back to count unique frames per class and to time input against the render that consumed it (`examples/python/app_stream/app_stream/marker.py`). Generation adds roughly one base-frame interval of presentation latency (about 33 ms at a 30 Hz base), so report that next to the smoothness gain. `tools/qualify_fg.py` in the example runs the rate profiles and writes the evidence.

## Common Pitfalls

- An SPG render var (for example ovrtx's BGRA8 `Swizzle` output) on the same RenderProduct disables generation; convert in the application instead.
- The first step after a renderer reset may return a single frame; settle for a few steps before deciding.
- Requesting more interpolated frames than the GPU supports (one on Ada, three on Blackwell) leaves generation off.
- A missing frame in the middle of a step is a contract violation; surface it rather than presenting what arrived.
