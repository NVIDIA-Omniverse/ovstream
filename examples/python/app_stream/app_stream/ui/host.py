# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary
#
# NVIDIA CORPORATION, its affiliates and licensors retain all intellectual
# property and proprietary rights in and to this material, related
# documentation and any modifications thereto. Any use, reproduction,
# disclosure or distribution of this material and related documentation
# without an express license agreement from NVIDIA CORPORATION or
# its affiliates is strictly prohibited.

"""Headless ovui host: renders the application chrome with Vulkan and exports it to CUDA.

ovui's headless export is an opaque RGBA8 image the size of its window, so the
window is the whole streamed frame: the chrome is drawn around a blank viewport
region and the application composites the rendered scene into that region.
`omni.ui` is imported here, after the environment is set, so `--no-ui` runs never
load it.
"""

from __future__ import annotations

import glob
import os
import sys
from typing import Callable, List

import ovstream
import warp as wp

from .keymap import to_imgui_key
from .layout import Rect


def _prepare_environment() -> None:
    os.environ.setdefault("OMNIUI_HEADLESS", "1")
    os.environ.setdefault("OMNIUI_BACKEND", "vulkan")
    if sys.platform == "win32":
        # The ovui wheel's extension module links DLLs that are not on the search path by default.
        for path in sys.path:
            ui_scene = os.path.join(path, "omni", "ui_scene")
            if os.path.isdir(ui_scene):
                os.add_dll_directory(ui_scene)
                cudart = glob.glob(os.path.join(path, "**", "cudart64_12.dll"), recursive=True)
                if cudart:
                    os.add_dll_directory(os.path.dirname(cudart[0]))
                break


_prepare_environment()
import omni.ui as ui  # noqa: E402
from omni.ui import standalone  # noqa: E402
from omni.ui.standalone import headless_frame  # noqa: E402

# ovstream mouse buttons -> ImGui button indices.
_BUTTONS = {ovstream.MouseButton.LEFT: 0, ovstream.MouseButton.RIGHT: 1, ovstream.MouseButton.MIDDLE: 2}


class UiHost:
    """Owns the standalone ovui backend, the chrome window and the CUDA export."""

    def __init__(self, width: int, height: int, cuda_device: int, build: Callable[[int, int], object]):
        standalone.init("app_stream", width, height, max_fps=None)
        self.width, self.height = width, height
        self.view = build(width, height)
        for _ in range(2):          # first ticks create the swapchain-less framebuffer
            standalone._tick_one_frame()
        if not headless_frame.init():
            raise RuntimeError("ovui headless frame export failed to initialise")
        if headless_frame.extent() != (width, height):
            raise RuntimeError(f"ovui export extent {headless_frame.extent()} != frame {(width, height)}")
        self.device = wp.get_device(f"cuda:{cuda_device}")
        self._alloc()
        self._exported = False

    def _alloc(self) -> None:
        with wp.ScopedDevice(self.device):
            self.image = wp.zeros((self.height, self.width, 4), dtype=wp.uint8)   # RGBA8 export target

    def resize(self, width: int, height: int, rebuild: Callable[[int, int], object]) -> None:
        """Resize the ovui window and its export, then rebuild the chrome for the new frame."""
        self.consumed()
        standalone.set_window_size(width, height)
        if not headless_frame.resize(width, height):
            raise RuntimeError(f"ovui export resize to {width}x{height} failed")
        self.width, self.height = width, height
        self.view = rebuild(width, height)
        standalone._tick_one_frame()
        self._alloc()

    # -- input -----------------------------------------------------------
    # [snippet:inject-input]
    def inject(self, events: List[object], rect: Rect) -> None:
        """Feed events to ovui before `tick()`: mouse events in window-local coordinates,
        keyboard events as ImGui keys, and composed text (delivered as str) as text input."""
        for event in events:
            if isinstance(event, str):
                ui._ui._inject_text_input(event)
                continue
            if event.type == ovstream.InputEventType.KEYBOARD:
                key = to_imgui_key(event.keyboard.key_code)
                if key:
                    ui._ui._inject_key_event(key, event.keyboard.key_state == ovstream.KeyState.DOWN)
                continue
            if event.type != ovstream.InputEventType.MOUSE:
                continue
            m = event.mouse
            x, y = rect.to_local(m.x, m.y)
            ui._ui._inject_mouse_move(x, y)
            if m.type == ovstream.MouseEventType.BUTTON and m.data in _BUTTONS:
                ui._ui._inject_mouse_button(_BUTTONS[m.data], m.button_state == ovstream.KeyState.DOWN)
            elif m.type == ovstream.MouseEventType.WHEEL:
                ui._ui._inject_mouse_scroll(int(m.scroll_x), int(m.scroll_y))
    # [/snippet:inject-input]

    def release_pointer(self) -> None:
        """Pointer left the chrome: move it out and release the left button so drags end."""
        ui._ui._inject_mouse_move(-1, -1)
        ui._ui._inject_mouse_button(0, False)

    # -- frame -----------------------------------------------------------
    def tick(self) -> None:
        standalone._tick_one_frame()

    # [snippet:export-panel]
    def export(self, stream: wp.Stream) -> wp.array:
        """Queue the copy of the rendered chrome into `self.image` on `stream`.

        `wait_ready` queues the Vulkan-to-CUDA semaphore wait on the null stream;
        `stream` is a blocking stream, so the copy is ordered after it. The caller
        composites on the same stream and then calls `consumed()`.
        """
        if not headless_frame.wait_ready(10_000_000):
            raise RuntimeError("ovui wait_ready failed")
        if not headless_frame.copy_to_linear(self.image.ptr, self.width * 4, stream.cuda_stream):
            headless_frame.signal_consumed()
            raise RuntimeError("ovui copy_to_linear failed")
        self._exported = True
        return self.image

    def consumed(self) -> None:
        """Signal ovui that the export has been read; queued on the null stream, so it
        follows the composite work on any blocking stream."""
        if self._exported:
            headless_frame.signal_consumed()
            self._exported = False
    # [/snippet:export-panel]

    def close(self) -> None:
        self.consumed()
        headless_frame.shutdown()
        self.image = None
        standalone.shutdown()
