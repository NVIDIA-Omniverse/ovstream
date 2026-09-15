# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary
#
# NVIDIA CORPORATION, its affiliates and licensors retain all intellectual
# property and proprietary rights in and to this material, related
# documentation and any modifications thereto. Any use, reproduction,
# disclosure or distribution of this material and related documentation
# without an express license agreement from NVIDIA CORPORATION or
# its affiliates is strictly prohibited.

"""Composition root: wires the libraries together and runs the per-tick frame path."""

from __future__ import annotations

import json
import sys
import time

import ovstream

from . import capabilities
from .config import AppConfig, parse_args
from .contract import FgDetector, FrameContractError, NoFreeSlot, PresentQueue, ResizeDebounce, clamp_resolution
from .marker import INTERPOLATED, REPEAT, SIMULATED, Marker

SETTLE_STEPS = 10   # steps allowed for a freshly populated RenderProduct to appear
WARMUP_STEPS = 40   # renderer warm-up (texture streaming, path-tracing convergence)
STATE_PERIOD_S = 0.25   # app_stream:state broadcast interval
CURSOR_VIEWPORT, CURSOR_PANEL, CURSOR_DRAG = "Crosshair", "Arrow", "Hand"


def run(argv=None) -> int:
    cfg = parse_args(argv)
    report = capabilities.probe(cfg)
    print(capabilities.format_report(report), file=sys.stderr)
    if cfg.report_json:
        capabilities.write_json(report, cfg.report_json)
    if cfg.report_only:
        return 0
    return App(cfg, report).run()


class App:
    def __init__(self, cfg: AppConfig, report: dict = None):
        # Imports here so `--report` works without the GPU libraries loading.
        from .camera import OrbitCamera
        from .frames import FramePipeline
        from .input_bridge import InputBridge
        from .pacing import Cadence, Stats
        from .render import RenderDriver
        from .scene import SceneController
        from .streaming import StreamLayer

        self.cfg = cfg
        self.gpu_name = ((report or {}).get("gpu") or {}).get("name", "")
        self.stats = Stats()
        self.bridge = InputBridge()
        self.camera = OrbitCamera()
        self.fg = FgDetector()
        self.fg_wanted = cfg.fg_requested      # follows the panel / app-message toggle
        self.queue = PresentQueue()

        self.layout = None
        self.regions = None
        if cfg.ui:
            from .ui.layout import Workbench
            self.layout = Workbench()
            self.regions = self.layout.rects(cfg.size[0], cfg.size[1])
        render_size = self.regions.viewport.size if self.regions else cfg.size

        print("Creating renderer (first run compiles and caches shaders)...", file=sys.stderr)
        self.scene = SceneController(cfg.scene, cfg.camera, render_size, cfg.fg_requested, cfg.fg_interpolated)
        self.render = RenderDriver(self.scene.stage, cfg.cuda_device, log_file=cfg.renderer_log)
        self.scene.populate()
        self.pipeline = FramePipeline(cfg.size[0], cfg.size[1], cfg.cuda_device, zero_copy=cfg.zero_copy)
        if cfg.zero_copy and cfg.publish_thread:
            print("--zero-copy holds the mapping only until stream_video returns; --publish-thread is ignored.",
                  file=sys.stderr)
            cfg.publish_thread = False
        self.cadence = Cadence(cfg.output_hz)
        self.stream = StreamLayer(cfg, self.bridge, self.stats, self.pipeline.cuda_context)
        self._last_frame = None
        self._last_item = None
        self._last_converted = False
        self._frame_id = 0
        self._pointer = (0, 0)                 # last pointer position seen in the input
        self._pointer_by_ordinal = {}          # ordinal -> pointer position the submit consumed
        self.size = cfg.size
        self.resize = ResizeDebounce()
        self.bridge.resize_handler = self._on_resize_request
        self.bridge.set_handler = self._on_set_command
        self._last_state = 0.0
        self.ui = None
        self._panel_focus = False
        self._pointer_owner = None             # 'ui' or 'viewport' while a button is held
        self._pointer_on_ui = False
        self._buttons_down = set()
        self._fg_grace = 0                     # steps after an FG toggle during which the count may lag
        self._last_step_ms = 0.0
        if cfg.ui:
            from .ui.host import UiHost
            self.ui = UiHost(cfg.size[0], cfg.size[1], cfg.cuda_device, self._build_chrome)

    def _build_chrome(self, width: int, height: int):
        from .ui.workbench import Actions, WorkbenchUi
        self.regions = self.layout.rects(width, height)
        actions = Actions(fg_toggle=self._request_fg, orbit_toggle=self._set_orbit, camera_reset=self.camera.reset)
        return WorkbenchUi(width, height, self.regions, actions, self._view())

    def _set_orbit(self, enabled: bool) -> None:
        if enabled:
            self.camera.reset()
        else:
            self.camera.orbit_enabled = False

    def _view(self):
        from .ui.workbench import View
        s = self.stats
        stats = {k: s.rate(k) for k in ("step", "frame", "present")}
        stats.update({k: s.mean_ms(k) for k in ("step_ms", "gpu_ms", "stream_ms", "ui_ms")})
        stats.update({k: s.counters.get(k, 0) for k in ("missed_step", "repeat", "drop")})
        stats["step_ms_last"] = self._last_step_ms
        scene = self.cfg.scene.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
        return View(scene=scene, camera=self.cfg.camera, viewport=self.regions.viewport.size, frame=self.size,
                    output_hz=self.cfg.output_hz, render_hz=self.cadence.render_hz,
                    fg_requested=self.fg_wanted, fg_active=self.fg.active,
                    frames_per_step=self.fg.frames_per_step if self.fg.active else 1,
                    orbit=self.camera.orbit_enabled, dragging=self.camera.dragging,
                    servers=[(sp.label, self.stream.connected.get(sp.label, False)) for sp in self.cfg.servers],
                    clients=self.stream.clients, resizable=self.cfg.resizable, gpu=self.gpu_name, stats=stats)

    def _request_fg(self, enabled: bool) -> None:
        """Panel callback: rewrite the RenderProduct's frame-generation attributes."""
        self.fg_wanted = enabled
        self.scene.set_frame_generation(enabled, self.cfg.fg_interpolated)
        self.fg.reset()
        self._fg_grace = self.fg.window + 1

    # -- control channel (main thread, via the bridge's command queue) --------
    def _on_resize_request(self, width: int, height: int) -> None:
        self.resize.request(width, height, time.monotonic())

    def _on_set_command(self, payload) -> None:
        """`app_stream:set` from a browser client."""
        if not isinstance(payload, dict):
            return
        if "fg" in payload:
            self._request_fg(bool(payload["fg"]))
        if payload.get("camera_reset"):
            self.camera.reset()
        if "size" in payload and self.cfg.resizable:
            size = payload["size"]
            try:
                w, h = int(size[0]), int(size[1])
            except (TypeError, ValueError, IndexError, KeyError):
                return
            self.resize.request(w, h, time.monotonic())

    # [snippet:apply-resize]
    def _apply_resize(self, width: int, height: int) -> None:
        size = clamp_resolution(width, height, self.cfg.size)
        if size == self.size:
            return
        self.size = size
        if self.ui is not None:
            self.ui.resize(size[0], size[1], self._build_chrome)    # also recomputes self.regions
            self.scene.set_resolution(*self.regions.viewport.size)
        else:
            self.scene.set_resolution(*size)      # the next step renders at the new size
        self.stats.count("resize")
        print(f"\nresized to {size[0]}x{size[1]}", file=sys.stderr)
    # [/snippet:apply-resize]

    def _state(self) -> dict:
        s = self.stats
        return {"fg_active": self.fg.active, "frames_per_step": self.fg.frames_per_step if self.fg.active else 1,
                "output_hz": self.cfg.output_hz, "render_hz": self.cadence.render_hz, "size": list(self.size),
                "rates": {k: round(s.rate(k), 1) for k in ("step", "frame", "present")},
                "missed_steps": s.counters.get("missed_step", 0), "dropped": s.counters.get("drop", 0)}

    # ------------------------------------------------------------------
    def _settle_and_warm_up(self) -> None:
        dt = 1.0 / self.cadence.render_hz
        for i in range(SETTLE_STEPS + WARMUP_STEPS):
            ordinal = self.scene.next_ordinal()
            self.scene.write_camera(self.camera.matrix(0.0), ordinal)
            result = self.render.step(dt, ordinal)
            if result.present:
                self.fg.observe(len(result.frames))
                if i >= SETTLE_STEPS:
                    continue
            elif i >= SETTLE_STEPS:
                raise RuntimeError("the streaming RenderProduct never became available")
        self._apply_fg_detection(initial=True)

    # [snippet:fg-detection]
    def _apply_fg_detection(self, initial: bool = False) -> None:
        fps = self.fg.frames_per_step if self.fg.active else 1
        changed = self.cadence.reconfigure_for(fps)
        if initial or changed:
            if self.fg.active:
                print(f"Frame generation active: {fps} frames per step; rendering at "
                      f"{self.cadence.render_hz} Hz for {self.cfg.output_hz} Hz delivered.", file=sys.stderr)
            elif self.fg_wanted:
                print("Frame generation requested but not active. Checklist: RealTimePathTracing render mode, "
                      "only LdrColor on the RenderProduct, both dimensions >= 126, Ada or newer GPU, supported "
                      "driver; see the capability report for the observed display state.", file=sys.stderr)
    # [/snippet:fg-detection]

    # ------------------------------------------------------------------
    def run(self) -> int:
        try:
            self._settle_and_warm_up()
            self.stream.start(self.cfg.size[0], self.cfg.size[1])
            self.stream.set_cursor(CURSOR_VIEWPORT)
            if self.cfg.publish_thread:
                self.stream.start_publish_thread()
            print("Streaming. Press Ctrl+C to stop.", file=sys.stderr)
            self._loop()
        except KeyboardInterrupt:
            pass
        finally:
            self.close()
        return 0

    def _summary(self) -> dict:
        s = self.stats
        return {
            "fg_active": self.fg.active,
            "frames_per_step": self.fg.frames_per_step if self.fg.active else 1,
            "output_hz": self.cfg.output_hz,
            "render_hz": self.cadence.render_hz,
            "rates_last_second": {k: s.rate(k) for k in ("step", "frame", "present", "submit")},
            "counters": dict(s.counters),
            "mean_ms": {k: s.mean_ms(k) for k in ("step_ms", "gpu_ms", "pipeline_ms", "stream_ms", "ui_ms")},
            "run": {"servers": [sp.label for sp in self.cfg.servers], "size": list(self.cfg.size),
                    "fg": self.cfg.fg, "fg_interpolated": self.cfg.fg_interpolated,
                    "publish_thread": self.cfg.publish_thread, "ui": self.cfg.ui, "marker": self.cfg.marker,
                    "zero_copy": self.cfg.zero_copy},
        }

    def _loop(self) -> None:
        t_start = None
        last_report = time.monotonic()
        while True:
            tick = self.cadence.tick()
            if t_start is None:
                t_start = tick.start_time_ns
            elapsed = (tick.start_time_ns - t_start) / 1e9
            if self.cfg.duration_s and elapsed >= self.cfg.duration_s:
                return

            # [snippet:tick-order]
            # 1. Commands and input first, so this tick's input reaches the camera
            #    before the render submit below. Pointer events over the panel go to
            #    ovui, everything else to the camera; the ovui tick runs the widget
            #    callbacks now, so a panel toggle also lands before the submit.
            self.bridge.run_commands()
            due = self.resize.due(time.monotonic())
            if due is not None:
                self._apply_resize(*due)
            self._dispatch_input(self.bridge.drain_events())
            if self.ui is not None:
                t_ui = time.perf_counter()
                self.ui.view.update(self._view())
                self.ui.tick()
                self.stats.timing("ui_ms", (time.perf_counter() - t_ui) * 1000.0)

            # 2. Collect the in-flight step if it finished (two non-blocking poll stages).
            try:
                result = self.render.poll()
            except FrameContractError as e:
                print(f"frame contract violated: {e}", file=sys.stderr)
                raise
            if result is not None:
                self.stats.count("step")
                self.stats.timing("step_ms", result.step_ms)
                self._last_step_ms = result.step_ms
                if result.present:
                    self._check_frame_count(len(result.frames))
                    self.fg.observe(len(result.frames))
                    self.stats.count("frame", len(result.frames))
                    # Keep the products object alive with its frames until presented.
                    self.queue.push_step([(result, f, c.interpolated) for f, c in zip(result.frames, result.classes)])
                    self._apply_fg_detection()

            # 3. Submit the next step when one is owed and none is in flight. The camera is
            #    published first so the submitted ordinal carries this tick's input. A step
            #    owed while the previous one is still running is a missed step, not a stall.
            if tick.fixed_step_count:
                if self.render.in_flight:
                    self.stats.count("missed_step")
                else:
                    ordinal = self.scene.next_ordinal()
                    self.scene.write_camera(self.camera.matrix(elapsed), ordinal)
                    self.render.submit(1.0 / self.cadence.render_hz, ordinal)
                    if self.cfg.marker:
                        self._pointer_by_ordinal[ordinal] = self._pointer

            # 4. Present one frame per tick. On underflow the last frame is re-presented,
            #    re-staged into a fresh slot so a slot still being published is never rewritten.
            item = self.queue.pop()
            repeat = item is None
            if repeat:
                if self._last_item is not None:
                    self.stats.count("repeat")
                item = self._last_item
            else:
                self.stats.count("present")
                self._last_item = item
                self._last_converted = False
            staged = None
            if item is not None:
                result, frame, interpolated = item
                chrome = None
                if self.ui is not None:
                    chrome = (self.ui.export(self.pipeline.stream), self.regions.viewport)
                marker = None
                if self.cfg.marker:
                    self._frame_id += 1
                    px, py = self._pointer_by_ordinal.get(result.ordinal, self._pointer)
                    marker = Marker(self._frame_id, result.ordinal,
                                    REPEAT if repeat else INTERPOLATED if interpolated else SIMULATED, px, py)
                    self._pointer_by_ordinal = {o: p for o, p in self._pointer_by_ordinal.items()
                                                if o >= result.ordinal}
                try:
                    staged = self.pipeline.stage(self.render.color_var(frame), timestamp_ns=tick.start_time_ns,
                                                 chrome=chrome, marker=marker, converted=self._last_converted)
                except NoFreeSlot:
                    # Every slot is still being published (a stalled transport); skip this tick.
                    self.stats.count("skip:busy")
                    if self.ui is not None:
                        self.ui.consumed()
                    staged = None
            if staged is not None:
                self._last_converted = True
                if self.ui is not None:
                    self.ui.consumed()
                self.stats.timing("pipeline_ms", staged.pipeline_ms)
                # The slot goes back to the pipeline once every server has returned from
                # stream_video (on the publish thread when one is used).
                self.stream.publish(staged.video_frame, done=lambda s=staged: self.pipeline.release(s))
                # In direct mode the transport has waited on the slot event by now, so the GPU
                # time from map to ready is known: renderer finish + copy + swizzle.
                gpu_ms = staged.gpu_ms(self.pipeline)
                if gpu_ms is not None:
                    self.stats.timing("gpu_ms", gpu_ms)
            self.stats.counters["drop"] = self.queue.dropped_frames
            # [/snippet:tick-order]

            now = time.monotonic()
            if now - self._last_state >= STATE_PERIOD_S:
                self._last_state = now
                self.stream.send_state(self._state())
            if now - last_report >= 1.0:
                last_report = now
                print("\r" + self.stats.line() + "    ", end="", file=sys.stderr, flush=True)

    def _check_frame_count(self, count: int) -> None:
        """The frame contract (render.py, contract.py) already guarantees one rendered frame
        per step; this checks the generated count against the request. A toggle takes a few
        steps to land, during which the count may lag the request.
        """
        if self._fg_grace:
            self._fg_grace -= 1
            return
        # The renderer clamps the interpolated count to what the GPU supports (one on Ada),
        # so fewer frames than requested is a valid outcome; more than requested is not.
        expected = self.cfg.fg_interpolated + 1
        if self.cfg.fg_interpolated and count > expected:
            raise FrameContractError(f"{count} frames per step but at most {expected} were requested; the camera "
                                     "fires more than once per render step")

    # [snippet:input-routing]
    def _dispatch_input(self, events) -> None:
        """Pointer events go to the surface they are over (chrome or viewport), and a pressed
        button pins the gesture to that surface until it is released, so a drag that ends over
        the chrome still delivers its release to the camera. Keyboard and text follow the last
        click (chrome focus) and otherwise drive the camera."""
        for event in events:
            if not isinstance(event, str) and event.type == ovstream.InputEventType.MOUSE:
                self._pointer = (int(event.mouse.x), int(event.mouse.y))
        if self.ui is None:
            for event in events:
                if not isinstance(event, str):
                    self.camera.handle(event)
            self.stream.set_cursor(CURSOR_DRAG if self.camera.dragging else CURSOR_VIEWPORT)
            return
        to_ui = []
        for event in events:
            if isinstance(event, str) or event.type == ovstream.InputEventType.KEYBOARD:
                if self._panel_focus:
                    to_ui.append(event)
                elif not isinstance(event, str):
                    self.camera.handle(event)
                continue
            if event.type != ovstream.InputEventType.MOUSE:
                continue
            m = event.mouse
            over_ui = self.regions.over_ui(m.x, m.y)
            if m.type == ovstream.MouseEventType.BUTTON:
                if m.button_state == ovstream.KeyState.DOWN:
                    if not self._buttons_down:
                        self._pointer_owner = "ui" if over_ui else "viewport"
                        self._panel_focus = over_ui
                    self._buttons_down.add(int(m.data))
                else:
                    self._buttons_down.discard(int(m.data))
            owner = self._pointer_owner or ("ui" if over_ui else "viewport")
            if owner == "ui":
                self._pointer_on_ui = True
                to_ui.append(event)
            else:
                if self._pointer_on_ui:
                    # Deliver the chrome's earlier events first so the leave lands after them.
                    if to_ui:
                        self.ui.inject(to_ui, self.regions.frame)
                        to_ui = []
                    self._pointer_on_ui = False
                    self.ui.release_pointer()
                self.camera.handle(event)
            if m.type == ovstream.MouseEventType.BUTTON and not self._buttons_down:
                self._pointer_owner = None
                self._pointer_on_ui = over_ui
        if to_ui:
            self.ui.inject(to_ui, self.regions.frame)
        self.stream.set_cursor(CURSOR_PANEL if self._pointer_on_ui else
                               CURSOR_DRAG if self.camera.dragging else CURSOR_VIEWPORT)
    # [/snippet:input-routing]

    def close(self) -> None:
        print("", file=sys.stderr)
        # Servers first: no callback and no publish-thread accounting after this point.
        try:
            self.stream.close()
        except Exception as e:
            print(f"shutdown: {type(e).__name__}: {e}", file=sys.stderr)
        if self.cfg.stats_json:
            try:
                with open(self.cfg.stats_json, "w", encoding="utf-8") as f:
                    json.dump(self._summary(), f, indent=1)
            except Exception as e:
                print(f"stats: {type(e).__name__}: {e}", file=sys.stderr)
        # Release every reference to renderer outputs before the renderer goes away.
        self.queue = PresentQueue()
        self._last_frame = None
        self._last_item = None
        # [snippet:shutdown-order]
        steps = [self.pipeline.close]
        if self.ui is not None:
            steps.append(self.ui.close)
        steps += [self.render.close, self.scene.close, self.cadence.close]
        for step in steps:
            try:
                step()
            except Exception as e:  # keep tearing down
                print(f"shutdown: {type(e).__name__}: {e}", file=sys.stderr)
        # [/snippet:shutdown-order]
