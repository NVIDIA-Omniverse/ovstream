# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary
#
# NVIDIA CORPORATION, its affiliates and licensors retain all intellectual
# property and proprietary rights in and to this material, related
# documentation and any modifications thereto. Any use, reproduction,
# disclosure or distribution of this material and related documentation
# without an express license agreement from NVIDIA CORPORATION or
# its affiliates is strictly prohibited.

"""Frame contract: pure logic with no GPU or library imports, so it is unit-testable.

Terms
    step      one renderer step covering the simulation window [t0, t1]
    firing    one sensor capture inside a step; a camera at the render rate
              fires exactly once per step
    simulated the frame a firing actually rendered
    interpolated
              a frame DLSS Frame Generation inferred between two simulated
              frames; returned before the simulated frame it leads to
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

# Minimum RenderProduct dimension for frame generation.
FG_MIN_DIMENSION = 126


class NoFreeSlot(RuntimeError):
    """Every frame-ring slot is still owned by a frame in flight."""


class FrameContractError(ValueError):
    """The renderer returned frames that violate the one-firing-per-step contract."""


@dataclass(frozen=True)
class FrameClass:
    index: int          # position in the returned frame list
    interpolated: bool  # False for the simulated frame
    time: float         # frame end time inside the step window


# [snippet:classify-frames]
def classify_frames(frame_times: Sequence[Tuple[float, float]],
                    step_start: float, step_end: float,
                    tolerance: float = 1e-6,
                    rendered: Optional[Sequence[bool]] = None) -> List[FrameClass]:
    """Classify the frames of one RenderProduct returned by one step.

    `frame_times` are (start_time, end_time) per frame, in returned order.
    With frame generation active a single firing returns N interpolated
    frames followed by the simulated frame, all strictly increasing in time
    and the last one ending at `step_end`.

    Timing alone cannot separate generated frames from a camera that fires
    several times per step (both are evenly spaced), so `rendered` carries the
    renderer's own word: True for a frame the renderer drew, False for one it
    generated (ovrtx reports a progression of 0 for generated frames). Exactly
    one rendered frame per step is the configuration this application
    requires; anything else raises rather than being reported as generation.
    """
    n = len(frame_times)
    if n == 0:
        return []
    if rendered is not None:
        if len(rendered) != n:
            raise FrameContractError("rendered flags do not match the frame list")
        drawn = sum(1 for r in rendered if r)
        if drawn != 1:
            raise FrameContractError(f"{drawn} rendered frames in one step: the camera fires {drawn} times per "
                                     "render step; schedule one firing per step")
        if rendered[-1] is not True:
            raise FrameContractError("the rendered frame is not the last of the step; generated frames should "
                                     "precede the simulated one")
    ends = [e for _, e in frame_times]
    for a, b in zip(ends, ends[1:]):
        if not b > a:
            raise FrameContractError(f"frame times not strictly increasing: {ends}")
    if abs(ends[-1] - step_end) > tolerance:
        raise FrameContractError(
            f"last frame ends at {ends[-1]:.6f}, expected the step end {step_end:.6f}; "
            "the RenderProduct fires more than once per step")
    if n > 1:
        # Interpolated frames divide the step window evenly; a second firing
        # would sit at a sensor period boundary instead. Check spacing.
        spacing = (step_end - step_start) / n
        for k, e in enumerate(ends):
            expected = step_start + spacing * (k + 1)
            if abs(e - expected) > max(tolerance, spacing * 0.05):
                raise FrameContractError(
                    f"frame {k} ends at {e:.6f}; interpolated frames should end at {expected:.6f}. "
                    "This looks like multiple firings per step, not frame generation")
    return [FrameClass(index=i, interpolated=(i < n - 1), time=ends[i]) for i in range(n)]
# [/snippet:classify-frames]


def frames_per_step(classes: Sequence[FrameClass]) -> int:
    return len(classes)


@dataclass
class FgDetector:
    """Decides whether frame generation is active from observed frame counts.

    Active once at least two frames per step have been seen in any of the
    last `window` steps. Requested-but-inactive is reported after `window`
    consecutive single-frame steps so the application can print its checklist.
    """

    window: int = 3
    _history: List[int] = None  # type: ignore[assignment]

    def __post_init__(self):
        self._history = []

    def observe(self, count: int) -> None:
        self._history.append(count)
        del self._history[:-self.window]

    def reset(self) -> None:
        """Forget history after a runtime toggle so detection restarts from the next steps."""
        self._history = []

    @property
    def active(self) -> bool:
        return any(c > 1 for c in self._history)

    @property
    def settled(self) -> bool:
        return len(self._history) >= self.window

    @property
    def frames_per_step(self) -> int:
        return max(self._history) if self._history else 1


def render_hz_for(output_hz: int, frames_per_step: int) -> int:
    """Base render rate that yields `output_hz` delivered frames."""
    if frames_per_step < 1:
        raise ValueError("frames_per_step must be >= 1")
    if output_hz < 1:
        raise ValueError("output_hz must be >= 1")
    if output_hz % frames_per_step:
        raise ValueError(f"output rate {output_hz} is not a multiple of {frames_per_step} frames per step")
    return output_hz // frames_per_step


def clamp_resolution(width: int, height: int, max_size: Tuple[int, int],
                     width_multiple: int = 8, min_dimension: int = FG_MIN_DIMENSION) -> Tuple[int, int]:
    """Clamp a requested size to the encoder and frame-generation limits.

    Width is rounded down to a multiple of `width_multiple`, height to an even
    value, both bounded below by `min_dimension` and above by `max_size`.
    """
    w = min(max(width, min_dimension), max_size[0])
    h = min(max(height, min_dimension), max_size[1])
    w -= w % width_multiple
    if w < min_dimension:
        w += width_multiple
    h -= h % 2
    if h < min_dimension:
        h += 2
    return min(w, max_size[0]), min(h, max_size[1])


class PresentQueue:
    """Frames waiting to be presented, one per output tick.

    Holds references (opaque to this module) in arrival order. `push_step`
    appends all frames of one step. `pop` returns the next frame or None on
    underflow, in which case the caller re-presents its last frame. When more
    than `max_steps` unfinished steps are queued the oldest is dropped and its
    frames counted.
    """

    def __init__(self, max_steps: int = 2):
        self._steps: List[List[object]] = []
        self.max_steps = max_steps
        self.dropped_frames = 0
        self.presented = 0
        self.repeats = 0

    def push_step(self, refs: Sequence[object]) -> None:
        if not refs:
            return
        # Fully presented steps leave empty lists behind; they are not backlog.
        self._steps = [s for s in self._steps if s]
        self._steps.append(list(refs))
        while len(self._steps) > self.max_steps:
            self.dropped_frames += len(self._steps.pop(0))

    def pop(self):
        while self._steps:
            step = self._steps[0]
            if step:
                self.presented += 1
                return step.pop(0)
            self._steps.pop(0)
        self.repeats += 1
        return None

    @property
    def depth(self) -> int:
        return sum(len(s) for s in self._steps)


class ResizeDebounce:
    """Coalesces client resize requests: the latest request is applied once no
    new one has arrived for `settle_s` seconds."""

    def __init__(self, settle_s: float = 0.15):
        self.settle_s = settle_s
        self._pending: Optional[Tuple[int, int]] = None
        self._since: float = 0.0

    def request(self, width: int, height: int, now: float) -> None:
        self._pending = (width, height)
        self._since = now

    def due(self, now: float) -> Optional[Tuple[int, int]]:
        """The size to apply, once; None while requests are still arriving or nothing is pending."""
        if self._pending is None or now - self._since < self.settle_s:
            return None
        size, self._pending = self._pending, None
        return size

