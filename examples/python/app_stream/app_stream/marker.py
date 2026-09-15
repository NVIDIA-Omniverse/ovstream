# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary
#
# NVIDIA CORPORATION, its affiliates and licensors retain all intellectual
# property and proprietary rights in and to this material, related
# documentation and any modifications thereto. Any use, reproduction,
# disclosure or distribution of this material and related documentation
# without an express license agreement from NVIDIA CORPORATION or
# its affiliates is strictly prohibited.

"""Pixel marker: frame identity a client can read back from the decoded video.

The WebRTC path forwards pixels only, so the identity of a delivered frame is
carried in the frame itself: three rows of 32 black-or-white cells in the
bottom-left corner, one bit per cell, large enough to survive H.264 at the
delivered resolution. A client samples the cell centres, rebuilds the words
and checks the checksum before trusting a read.

    word 0   frame id (32 bits, increments per presented frame)
    word 1   scene ordinal (24 bits) | class (2 bits) | 6 spare bits
    word 2   pointer x (12 bits) | pointer y (12 bits) | checksum (8 bits)

`class` is 0 simulated, 1 interpolated, 2 repeat. The pointer position is the
last one the render submit for `ordinal` consumed; a client that moves the
pointer to a unique position and waits for a frame whose marker echoes it
measures input-to-visible latency against the render that saw the input, not
against the next frame that happened to arrive.

The pure functions here are unit-tested; the Warp kernel draws the marker.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

CELL = 8            # cell size in pixels
COLS = 32           # cells per row (one 32-bit word)
ROWS = 3
MARGIN = 8          # distance from the bottom-left corner
WIDTH = CELL * COLS
HEIGHT = CELL * ROWS

SIMULATED, INTERPOLATED, REPEAT = 0, 1, 2


@dataclass(frozen=True)
class Marker:
    frame_id: int
    ordinal: int
    frame_class: int
    pointer_x: int
    pointer_y: int


def _checksum(w0: int, w1: int, w2_high: int) -> int:
    """8-bit XOR of every payload byte, seeded so an all-black block does not verify."""
    c = 0xA5
    for word in (w0, w1, w2_high):
        for shift in (0, 8, 16, 24):
            c ^= (word >> shift) & 0xFF
    return c


def encode(m: Marker) -> Tuple[int, int, int]:
    """Three 32-bit words for `m`."""
    w0 = m.frame_id & 0xFFFFFFFF
    w1 = ((m.ordinal & 0xFFFFFF) << 8) | ((m.frame_class & 0x3) << 6)
    w2_high = ((m.pointer_x & 0xFFF) << 20) | ((m.pointer_y & 0xFFF) << 8)
    return w0, w1, w2_high | _checksum(w0, w1, w2_high)


def decode(words: Tuple[int, int, int]) -> Optional[Marker]:
    """The marker the words carry, or None when the checksum does not verify."""
    w0, w1, w2 = (w & 0xFFFFFFFF for w in words)
    if (w2 & 0xFF) != _checksum(w0, w1, w2 & 0xFFFFFF00):
        return None
    return Marker(frame_id=w0, ordinal=w1 >> 8, frame_class=(w1 >> 6) & 0x3,
                  pointer_x=(w2 >> 20) & 0xFFF, pointer_y=(w2 >> 8) & 0xFFF)


def origin(frame_width: int, frame_height: int) -> Tuple[int, int]:
    """Top-left pixel of the marker block for a frame of this size."""
    return MARGIN, frame_height - MARGIN - HEIGHT


def read_words(pixels, frame_width: int, frame_height: int, threshold: int = 128) -> Tuple[int, int, int]:
    """Rebuild the three words from an (H, W, C) uint8 image; the first channel is sampled
    at each cell centre (the cells are grey, so any channel order works). Works on a
    numpy array or nested lists."""
    x0, y0 = origin(frame_width, frame_height)
    words = []
    for row in range(ROWS):
        word = 0
        y = y0 + row * CELL + CELL // 2
        for col in range(COLS):
            x = x0 + col * CELL + CELL // 2
            if int(pixels[y][x][0]) >= threshold:
                word |= 1 << (COLS - 1 - col)
        words.append(word)
    return words[0], words[1], words[2]


def read(pixels, frame_width: int, frame_height: int) -> Optional[Marker]:
    return decode(read_words(pixels, frame_width, frame_height))


def kernel():
    """The Warp kernel that draws a marker; imported lazily so the pure functions stay GPU-free."""
    import warp as wp

    @wp.kernel
    def draw_marker(dst: wp.array3d(dtype=wp.uint8), x0: int, y0: int,
                    w0: wp.uint32, w1: wp.uint32, w2: wp.uint32):
        # One thread per pixel of the marker block; the most significant bit is the leftmost cell.
        px, py = wp.tid()
        col = px / CELL
        row = py / CELL
        word = w0
        if row == 1:
            word = w1
        elif row == 2:
            word = w2
        bit = (word >> wp.uint32(COLS - 1 - col)) & wp.uint32(1)
        value = wp.uint8(0)
        if bit == wp.uint32(1):
            value = wp.uint8(255)
        dst[y0 + py, x0 + px, 0] = value
        dst[y0 + py, x0 + px, 1] = value
        dst[y0 + py, x0 + px, 2] = value
        dst[y0 + py, x0 + px, 3] = wp.uint8(255)

    return draw_marker
