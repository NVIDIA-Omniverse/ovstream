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
name: remote-input-to-ui
description: Routing browser input from ovstream to an ovui interface and a camera — the worker-thread hand-off, pointer routing by region, keyboard focus, injecting into ovui, and cursor forwarding. Use when user asks how to make a streamed ovui panel clickable, how to map ovstream key codes to ImGui keys, how to decide whether input goes to the UI or the scene, or how to forward the cursor shape.
license: LicenseRef-NvidiaProprietary
author: NVIDIA ovstream
tags:
  - ovstream
  - input
  - ovui
  - browser
tools:
  - Read
  - Grep
---

# Remote Input to an ovui Interface

## Overview

ovstream delivers `InputEvent`s and composed text on its worker thread; ovui's input must be written on the thread that ticks it, before the tick. The application queues events, drains them once per tick, decides per event whether the UI or the scene camera receives it, and injects the UI's share through `omni.ui._ui._inject_*`.

## Hand-off and routing

Pointer events go to whichever surface the pointer is over: the chrome (everything outside the viewport) receives ovui injection in window coordinates, the viewport drives the camera. Keyboard and text follow the last click, so a click on the panel gives it keyboard focus and a click in the scene takes it back. When the pointer leaves the panel, ovui is sent a move out of bounds and a left-button release so no drag stays stuck.

> **Source:** `examples/python/app_stream/app_stream/app.py` snippet `input-routing`

## Injecting into ovui

Mouse position first, then buttons and wheel; keyboard events as ImGui keys; composed text (`Server.on_unicode`) as text input. The ovstream key codes are StreamSDK's NVST codes; the example carries a compact table to ImGui keys.

> **Source:** `examples/python/app_stream/app_stream/ui/host.py` snippet `inject-input` and `examples/python/app_stream/app_stream/ui/keymap.py`

## Cursor

Forward the cursor shape with `Server.set_cursor` whenever it changes: an arrow over the chrome, a crosshair over the scene, a hand while dragging. Send only on change; the SDK resets the name to `Arrow` on `stop()`.

> **Source:** `examples/python/app_stream/app_stream/streaming.py` snippet `cursor-and-state`

## Common Pitfalls

- Widget callbacks run inside the ovui tick; tick after injecting and before the render submit so a toggle in the chrome is published under the same ordinal as the input that caused it.
- Coordinates arrive in stream pixel space and the SDK rescales its extents when a resizable stream changes size; recompute the panel rectangle on resize.
- `Ctrl` and `Alt` combinations are accelerators, not text; do not synthesise characters for them.
- Register `on_input` and `on_unicode` before `start()` so no early event is lost.
