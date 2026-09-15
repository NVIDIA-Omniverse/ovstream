# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary
#
# NVIDIA CORPORATION, its affiliates and licensors retain all intellectual
# property and proprietary rights in and to this material, related
# documentation and any modifications thereto. Any use, reproduction,
# disclosure or distribution of this material and related documentation
# without an express license agreement from NVIDIA CORPORATION or
# its affiliates is strictly prohibited.

"""ovrtx renderer driver: synchronous and asynchronous stepping, frame classification."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import List, Optional

import ovrtx

from .contract import FrameClass, classify_frames
from .scene import LDR_VAR, RENDER_PRODUCT


@dataclass
class StepResult:
    ordinal: int
    step_ms: float                       # submit-to-complete wall time
    products: object                     # RenderProductSetOutputs; keeps the frames alive
    frames: List[object] = field(default_factory=list)   # FrameOutput, returned order
    classes: List[FrameClass] = field(default_factory=list)

    @property
    def present(self) -> bool:
        return bool(self.frames)


class RenderDriver:
    """Owns the ovrtx Renderer attached to the application's ovstage stage.

    `step()` is the synchronous path used for warm-up. The main loop uses
    `submit()` and `poll()`: at most one step is in flight, and both poll
    stages (`Operation.wait(0)` and `PendingFetch.fetch(0)`) are non-blocking,
    so a slow step never stalls the loop; the loop keeps presenting.
    """

    def __init__(self, stage, cuda_device: int = 0, log_file: Optional[str] = None):
        cfg = ovrtx.RendererConfig()
        cfg.active_cuda_gpus = str(cuda_device)   # comma-separated device ordinals
        if log_file:
            cfg.log_file_path = log_file
            cfg.log_level = "info"
        self.renderer = ovrtx.Renderer(cfg)
        self.renderer.attach_ovstage(stage)
        self.sim_time = 0.0
        self._op = None          # in-flight Operation
        self._pending = None     # PendingFetch once the operation completed
        self._inflight = None    # (ordinal, start, end, submit_time)

    # -- synchronous -----------------------------------------------------
    def step(self, delta_time: float, ordinal: int) -> StepResult:
        t0 = time.perf_counter()
        products = self.renderer.step(render_products={RENDER_PRODUCT}, delta_time=delta_time, ordinal=ordinal)
        start, end = self._advance(delta_time)
        return self._result(products, ordinal, start, end, (time.perf_counter() - t0) * 1000.0)

    # -- asynchronous ----------------------------------------------------
    @property
    def in_flight(self) -> bool:
        return self._inflight is not None

    # [snippet:async-step]
    def submit(self, delta_time: float, ordinal: int) -> None:
        if self._inflight is not None:
            raise RuntimeError("a step is already in flight")
        self._op = self.renderer.step_async(render_products={RENDER_PRODUCT}, delta_time=delta_time, ordinal=ordinal)
        start, end = self._advance(delta_time)
        self._inflight = (ordinal, start, end, time.perf_counter())

    def poll(self) -> Optional[StepResult]:
        """Return the completed step, or None if none is in flight or it is not ready yet."""
        if self._inflight is None:
            return None
        if self._pending is None:
            self._pending = self._op.wait(timeout_ns=0)
            if self._pending is None:
                return None
        products = self._pending.fetch(timeout_ns=0)
        if products is None:
            return None
        ordinal, start, end, submitted = self._inflight
        self._op = self._pending = self._inflight = None
        return self._result(products, ordinal, start, end, (time.perf_counter() - submitted) * 1000.0)
    # [/snippet:async-step]

    def finish(self) -> None:
        """Complete any in-flight step (blocking); used before teardown."""
        if self._inflight is None:
            return
        pending = self._pending or self._op.wait()
        if pending is not None:
            pending.fetch()
        self._op = self._pending = self._inflight = None

    # -- helpers -----------------------------------------------------------
    def _advance(self, delta_time: float):
        start = self.sim_time
        self.sim_time += delta_time
        return start, self.sim_time

    def _result(self, products, ordinal, start, end, step_ms) -> StepResult:
        result = StepResult(ordinal=ordinal, step_ms=step_ms, products=products)
        if RENDER_PRODUCT in list(products.keys()):
            result.frames = list(products[RENDER_PRODUCT].frames)
            times = [(f.start_time, f.end_time) for f in result.frames]
            # progression counts renders into the output; a generated frame reports 0.
            rendered = [f.progression > 0 for f in result.frames]
            result.classes = classify_frames(times, start, end, tolerance=max(1e-6, (end - start) * 0.02),
                                             rendered=rendered)
        return result

    def reset(self) -> None:
        self.finish()
        self.renderer.reset()
        self.sim_time = 0.0

    @staticmethod
    def color_var(frame) -> ovrtx.RenderVarOutput:
        return frame.render_vars[LDR_VAR]

    def close(self) -> None:
        self.finish()
        self.renderer.detach_ovstage()
        self.renderer.destroy()
