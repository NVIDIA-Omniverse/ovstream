# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary
#
# NVIDIA CORPORATION, its affiliates and licensors retain all intellectual
# property and proprietary rights in and to this material, related
# documentation and any modifications thereto. Any use, reproduction,
# disclosure or distribution of this material and related documentation
# without an express license agreement from NVIDIA CORPORATION or
# its affiliates is strictly prohibited.

"""The application chrome: top bar, dock sections, status bar. Built once with ovui at the
frame size; `update()` refreshes the readouts every tick. The viewport region is left
blank here and receives the rendered scene when the frame is composited."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Deque, Dict, List, Optional, Tuple

import omni.ui as ui

from .layout import Regions

# ABGR colours.
BG = 0xFF1B1B1B
BG_BAR = 0xFF232323
BG_SECTION = 0xFF2A2A2A
BORDER = 0xFF3A3A3A
TEXT = 0xFFE6E6E6
TEXT_DIM = 0xFF9A9A9A
ACCENT = 0xFF00B976        # NVIDIA green in ABGR
WARN = 0xFF3AA0FF
OFF = 0xFF555555

HISTORY = 120              # samples kept for the frame-time plot


@dataclass
class Actions:
    fg_toggle: Callable[[bool], None]
    orbit_toggle: Callable[[bool], None]
    camera_reset: Callable[[], None]


@dataclass
class View:
    """Everything the chrome displays; the application fills one of these per tick."""

    scene: str = ""
    camera: str = ""
    viewport: Tuple[int, int] = (0, 0)
    frame: Tuple[int, int] = (0, 0)
    output_hz: int = 0
    render_hz: int = 0
    render_mode: str = "RealTimePathTracing"
    fg_requested: bool = False
    fg_active: bool = False
    frames_per_step: int = 1
    orbit: bool = True
    dragging: bool = False
    servers: List[Tuple[str, bool]] = field(default_factory=list)   # (label, connected)
    clients: int = 0
    resizable: bool = False
    gpu: str = ""
    stats: Dict[str, float] = field(default_factory=dict)


def _style(color=TEXT, size=13, **extra):
    s = {"color": color, "font_size": size}
    s.update(extra)
    return s


class WorkbenchUi:
    """ovui widgets for the chrome. Coordinates are frame coordinates: the window fills the frame."""

    def __init__(self, width: int, height: int, regions: Regions, actions: Actions, view: View):
        self.regions = regions
        self._actions = actions
        self._labels: Dict[str, ui.Label] = {}
        self._dots: Dict[str, ui.Rectangle] = {}
        self._history: Deque[float] = deque([0.0] * HISTORY, maxlen=HISTORY)
        self._plot: Optional[ui.Plot] = None
        self._fg_model = ui.SimpleBoolModel(view.fg_requested)
        self._fg_model.add_value_changed_fn(lambda m: actions.fg_toggle(m.as_bool))
        self._orbit_model = ui.SimpleBoolModel(view.orbit)
        self._orbit_model.add_value_changed_fn(lambda m: actions.orbit_toggle(m.as_bool))
        flags = (ui.WINDOW_FLAGS_NO_TITLE_BAR | ui.WINDOW_FLAGS_NO_RESIZE | ui.WINDOW_FLAGS_NO_MOVE |
                 ui.WINDOW_FLAGS_NO_SCROLLBAR | ui.WINDOW_FLAGS_NO_COLLAPSE | ui.WINDOW_FLAGS_NO_SAVED_SETTINGS)
        self.window = ui.Window("app_stream", width=width, height=height, flags=flags, padding_x=0, padding_y=0)
        self.window.position_x = 0
        self.window.position_y = 0
        with self.window.frame:
            with ui.ZStack():
                ui.Rectangle(style={"background_color": BG})
                with ui.VStack():
                    self._build_top_bar(regions.top_bar.height)
                    with ui.HStack():
                        self._build_viewport_placeholder()
                        if regions.dock.width:
                            self._build_dock(regions.dock.width, view)
                    if regions.status_bar.height:
                        self._build_status_bar(regions.status_bar.height)
        self.update(view)

    # -- construction --------------------------------------------------------------
    def _label(self, key: str, text: str = "-", color=TEXT, size=13, **kw) -> ui.Label:
        label = ui.Label(text, style=_style(color, size), **kw)
        self._labels[key] = label
        return label

    def _row(self, key: str, title: str) -> None:
        with ui.HStack(height=18):
            ui.Label(title, style=_style(TEXT_DIM, 12), width=ui.Fraction(1.1), height=18,
                     alignment=ui.Alignment.LEFT_CENTER)
            self._label(key, size=12, alignment=ui.Alignment.RIGHT_CENTER, height=18)

    def _dot(self, key: str, size: int = 8) -> None:
        """A small round status indicator, vertically centred in an 18 px row."""
        with ui.VStack(width=size, height=18):
            ui.Spacer(height=(18 - size) // 2)
            self._dots[key] = ui.Rectangle(width=size, height=size,
                                           style={"background_color": OFF, "border_radius": size // 2})
            ui.Spacer()

    def _section(self, title: str):
        return ui.CollapsableFrame(title, height=0, collapsed=False,
                                   style={"CollapsableFrame": {"background_color": BG_SECTION, "border_radius": 4,
                                                               "padding": 6},
                                          "CollapsableFrame.Header": {"color": TEXT, "font_size": 13}})

    def _build_top_bar(self, height: int) -> None:
        if not height:
            return
        with ui.ZStack(height=height):
            ui.Rectangle(style={"background_color": BG_BAR, "border_color": BORDER, "border_width": 1})
            with ui.HStack(spacing=10):
                ui.Rectangle(width=6, style={"background_color": ACCENT})
                ui.Spacer(width=6)
                ui.Label("app_stream", style=_style(TEXT, 18), width=0)
                ui.Label("ovrtx  +  ovstage  +  ovui  +  ovstream", style=_style(TEXT_DIM, 12), width=0)
                ui.Spacer()
                with ui.HStack(width=0, spacing=6):
                    ui.CheckBox(model=self._fg_model, width=18)
                    ui.Label("frame generation", style=_style(TEXT), width=0)
                ui.Spacer(width=18)
                with ui.HStack(width=0, spacing=6):
                    ui.CheckBox(model=self._orbit_model, width=18)
                    ui.Label("auto orbit", style=_style(TEXT), width=0)
                ui.Spacer(width=18)
                with ui.VStack(width=0):
                    ui.Spacer()
                    ui.Button("Reset camera", width=110, height=26, clicked_fn=self._actions.camera_reset,
                              style={"Button": {"background_color": BG_SECTION, "border_radius": 4},
                                     "Button.Label": _style(TEXT, 13)})
                    ui.Spacer()
                ui.Spacer(width=12)

    def _build_viewport_placeholder(self) -> None:
        # The scene is composited over this region after the UI is exported.
        with ui.ZStack():
            ui.Rectangle(style={"background_color": 0xFF000000})
            ui.InvisibleButton()

    def _build_dock(self, width: int, view: View) -> None:
        with ui.ZStack(width=width):
            ui.Rectangle(style={"background_color": BG, "border_color": BORDER, "border_width": 1})
            with ui.ScrollingFrame(horizontal_scrollbar_policy=ui.ScrollBarPolicy.SCROLLBAR_ALWAYS_OFF,
                                   style={"ScrollingFrame": {"background_color": BG}}):
                with ui.VStack(spacing=6, style={"VStack": {"margin": 8}}):
                    with self._section("Scene"):
                        with ui.VStack(spacing=2):
                            self._row("scene", "file")
                            self._row("camera", "camera")
                            self._row("viewport", "viewport")
                            self._row("frame", "stream")
                    with self._section("Rendering"):
                        with ui.VStack(spacing=2):
                            self._row("render_mode", "mode")
                            self._row("render_hz", "base rate")
                            self._row("fg_state", "frame generation")
                            self._row("frames_per_step", "frames per step")
                    with self._section("Camera"):
                        with ui.VStack(spacing=2):
                            self._row("camera_mode", "mode")
                            ui.Label("left-drag orbits, wheel zooms, any key resumes the orbit",
                                     style=_style(TEXT_DIM, 11), word_wrap=True)
                    with self._section("Streaming"):
                        with ui.VStack(spacing=4):
                            for label, _ in view.servers:
                                with ui.HStack(height=18, spacing=8):
                                    self._dot(label, 8)
                                    ui.Label(label, style=_style(TEXT, 12), height=18,
                                             alignment=ui.Alignment.LEFT_CENTER)
                            self._row("clients", "clients")
                            self._row("resizable", "client resize")
                    with self._section("Statistics"):
                        with ui.VStack(spacing=2):
                            self._row("rate_render", "render steps/s")
                            self._row("rate_frames", "frames/s")
                            self._row("rate_present", "presents/s")
                            self._row("step_ms", "step wall ms")
                            self._row("gpu_ms", "render-to-ready ms")
                            self._row("stream_ms", "stream_video ms")
                            self._row("ui_ms", "ui tick ms")
                            self._row("missed", "missed / repeats / drops")
                            ui.Spacer(height=4)
                            ui.Label("step wall time, last 120 steps", style=_style(TEXT_DIM, 11), height=14)
                            try:
                                self._plot = ui.Plot(ui.Type.LINE, 0.0, 40.0, *self._history, height=56,
                                                     style={"color": ACCENT, "background_color": BG_BAR})
                            except Exception:   # a build without Plot keeps the table
                                self._plot = None

    def _build_status_bar(self, height: int) -> None:
        with ui.ZStack(height=height):
            ui.Rectangle(style={"background_color": BG_BAR, "border_color": BORDER, "border_width": 1})
            with ui.HStack(spacing=16):
                ui.Spacer(width=290)     # the frame-identity marker is drawn in this corner
                with ui.HStack(width=0, spacing=6):
                    with ui.VStack(width=8):
                        ui.Spacer()
                        self._dot("status", 8)
                        ui.Spacer()
                    self._label("status", "starting", TEXT, 12, width=0, alignment=ui.Alignment.LEFT_CENTER)
                ui.Spacer()
                self._label("status_fg", "", TEXT_DIM, 12, width=0)
                ui.Spacer()
                self._label("status_rate", "", TEXT_DIM, 12, width=0)
                ui.Spacer(width=16)
                self._label("status_gpu", "", TEXT_DIM, 12, width=0)
                ui.Spacer(width=14)

    # -- per tick ------------------------------------------------------------------
    def _set(self, key: str, text: str) -> None:
        label = self._labels.get(key)
        if label is not None:
            label.text = text

    def update(self, v: View) -> None:
        s = v.stats
        self._set("scene", v.scene)
        self._set("camera", v.camera)
        self._set("viewport", f"{v.viewport[0]} x {v.viewport[1]}")
        self._set("frame", f"{v.frame[0]} x {v.frame[1]} @ {v.output_hz} Hz")
        self._set("render_mode", v.render_mode)
        self._set("render_hz", f"{v.render_hz} Hz")
        self._set("fg_state", ("active" if v.fg_active else "requested, not engaged") if v.fg_requested else "off")
        self._set("frames_per_step", str(v.frames_per_step))
        self._set("camera_mode", "dragging" if v.dragging else "auto orbit" if v.orbit else "manual")
        self._set("clients", str(v.clients))
        self._set("resizable", "on" if v.resizable else "off")
        self._set("rate_render", f"{s.get('step', 0.0):.0f}")
        self._set("rate_frames", f"{s.get('frame', 0.0):.0f}")
        self._set("rate_present", f"{s.get('present', 0.0):.0f}")
        self._set("step_ms", f"{s.get('step_ms', 0.0):.1f}")
        self._set("gpu_ms", f"{s.get('gpu_ms', 0.0):.1f}")
        self._set("stream_ms", f"{s.get('stream_ms', 0.0):.2f}")
        self._set("ui_ms", f"{s.get('ui_ms', 0.0):.2f}")
        self._set("missed", f"{s.get('missed_step', 0):.0f} / {s.get('repeat', 0):.0f} / {s.get('drop', 0):.0f}")
        for label, connected in v.servers:
            dot = self._dots.get(label)
            if dot is not None:
                dot.set_style({"background_color": ACCENT if connected else OFF, "border_radius": 4})
        streaming = v.clients > 0
        self._set("status", f"streaming to {v.clients} client{'s' if v.clients != 1 else ''}" if streaming
                  else "waiting for a client")
        status_dot = self._dots.get("status")
        if status_dot is not None:
            status_dot.set_style({"background_color": ACCENT if streaming else WARN, "border_radius": 4})
        self._set("status_fg", f"frame generation: {v.frames_per_step} frames per step" if v.fg_active
                  else "frame generation off" if not v.fg_requested else "frame generation not engaged")
        self._set("status_rate", f"{s.get('present', 0.0):.0f} / {v.output_hz} Hz")
        self._set("status_gpu", v.gpu)
        if self._orbit_model.as_bool != v.orbit:
            self._orbit_model.set_value(v.orbit)
        if "step_ms_last" in s:
            self._history.append(float(s["step_ms_last"]))
            if self._plot is not None:
                self._plot.set_data(*self._history)
