# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary
#
# NVIDIA CORPORATION, its affiliates and licensors retain all intellectual
# property and proprietary rights in and to this material, related
# documentation and any modifications thereto. Any use, reproduction,
# disclosure or distribution of this material and related documentation
# without an express license agreement from NVIDIA CORPORATION or
# its affiliates is strictly prohibited.

"""Where the application chrome and the viewport sit in the streamed frame; pure logic, unit-tested."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple


@dataclass(frozen=True)
class Rect:
    x: int
    y: int
    width: int
    height: int

    def contains(self, px: float, py: float) -> bool:
        return self.x <= px < self.x + self.width and self.y <= py < self.y + self.height

    def to_local(self, px: float, py: float) -> Tuple[int, int]:
        return int(px) - self.x, int(py) - self.y

    @property
    def size(self) -> Tuple[int, int]:
        return self.width, self.height


@dataclass(frozen=True)
class Regions:
    """The frame split into chrome and viewport. Everything outside `viewport` is UI."""

    frame: Rect
    top_bar: Rect
    dock: Rect
    status_bar: Rect
    viewport: Rect

    def over_ui(self, px: float, py: float) -> bool:
        return self.frame.contains(px, py) and not self.viewport.contains(px, py)


@dataclass(frozen=True)
class Workbench:
    """A top bar, a dock on the right and a status bar at the bottom; the viewport takes the rest.

    The viewport keeps an even width that is a multiple of 8 and an even height so it can
    be the renderer's resolution directly, and it never drops below the frame-generation
    minimum; on a frame too small for the chrome the dock gives way first.
    """

    top_bar: int = 40
    dock: int = 340
    status_bar: int = 28
    min_viewport: int = 128

    def rects(self, frame_width: int, frame_height: int) -> Regions:
        dock = self.dock if frame_width - self.dock >= self.min_viewport else 0
        top = self.top_bar if frame_height - self.top_bar - self.status_bar >= self.min_viewport else 0
        status = self.status_bar if top else 0
        vw = frame_width - dock
        vw -= vw % 8
        vh = frame_height - top - status
        vh -= vh % 2
        frame = Rect(0, 0, frame_width, frame_height)
        return Regions(
            frame=frame,
            top_bar=Rect(0, 0, frame_width, top),
            dock=Rect(frame_width - dock, top, dock, frame_height - top - status),
            status_bar=Rect(0, frame_height - status, frame_width, status),
            viewport=Rect(0, top, vw, vh),
        )
