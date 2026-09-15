# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary
#
# NVIDIA CORPORATION, its affiliates and licensors retain all intellectual
# property and proprietary rights in and to this material, related
# documentation and any modifications thereto. Any use, reproduction,
# disclosure or distribution of this material and related documentation
# without an express license agreement from NVIDIA CORPORATION or
# its affiliates is strictly prohibited.

"""Cadence (a thin wrapper over ovstream_utils.Loop) and rolling statistics."""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict

import ovstream_utils

from .contract import render_hz_for


class Cadence:
    """Paces the application loop at the delivered rate and tells it when a
    render step is owed.

    The loop ticks at `output_hz`. `fixed_step_hz` is the base render rate;
    `Tick.fixed_step_count` says whether a render step is owed this tick.
    Reconfiguring resets the Loop's timing statistics, so it happens only on a
    sustained change in frames-per-step, decided by the caller.
    """

    def __init__(self, output_hz: int, frames_per_step: int = 1):
        self.output_hz = output_hz
        self.frames_per_step = frames_per_step
        self._loop = ovstream_utils.Loop(self._config())

    def _config(self) -> ovstream_utils.LoopConfig:
        return ovstream_utils.LoopConfig(
            fps_target=self.output_hz,
            fixed_step_hz=render_hz_for(self.output_hz, self.frames_per_step),
            fixed_step_max=1,
        )

    @property
    def render_hz(self) -> int:
        return render_hz_for(self.output_hz, self.frames_per_step)

    def reconfigure_for(self, frames_per_step: int) -> bool:
        if frames_per_step == self.frames_per_step:
            return False
        self.frames_per_step = frames_per_step
        self._loop.reconfigure(self._config())
        return True

    def tick(self) -> ovstream_utils.Tick:
        return self._loop.tick()

    def close(self) -> None:
        self._loop.close()


@dataclass
class Stats:
    """Rolling one-second counters and timings for the stats readout."""

    window_s: float = 1.0
    _events: Dict[str, Deque[float]] = field(default_factory=dict)
    _timings: Dict[str, Deque[float]] = field(default_factory=dict)
    counters: Dict[str, int] = field(default_factory=dict)

    def count(self, name: str, n: int = 1) -> None:
        now = time.monotonic()
        q = self._events.setdefault(name, deque())
        for _ in range(n):
            q.append(now)
        self._trim(q, now)
        self.counters[name] = self.counters.get(name, 0) + n

    def timing(self, name: str, ms: float) -> None:
        q = self._timings.setdefault(name, deque(maxlen=240))
        q.append(ms)

    def rate(self, name: str) -> float:
        q = self._events.get(name)
        if not q:
            return 0.0
        self._trim(q, time.monotonic())
        return len(q) / self.window_s

    def mean_ms(self, name: str) -> float:
        q = self._timings.get(name)
        return sum(q) / len(q) if q else 0.0

    def _trim(self, q: Deque[float], now: float) -> None:
        cutoff = now - self.window_s
        while q and q[0] < cutoff:
            q.popleft()

    def line(self) -> str:
        parts = [
            f"steps/s {self.rate('step'):5.1f}",
            f"frames/s {self.rate('frame'):5.1f}",
            f"presents/s {self.rate('present'):5.1f}",
            f"repeats {self.counters.get('repeat', 0)}",
            f"dropped {self.counters.get('drop', 0)}",
            f"missed {self.counters.get('missed_step', 0)}",
            f"step(wall) {self.mean_ms('step_ms'):5.1f}ms",
            f"render->ready(gpu) {self.mean_ms('gpu_ms'):5.1f}ms",
            f"pipeline(cpu) {self.mean_ms('pipeline_ms'):4.1f}ms",
            f"publish(cpu) {self.mean_ms('stream_ms'):4.1f}ms",
        ]
        if "ui_ms" in self._timings:
            parts.append(f"ui(cpu) {self.mean_ms('ui_ms'):4.1f}ms")
        return "  ".join(parts)
