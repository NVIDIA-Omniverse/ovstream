# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary
#
# NVIDIA CORPORATION, its affiliates and licensors retain all intellectual
# property and proprietary rights in and to this material, related
# documentation and any modifications thereto. Any use, reproduction,
# disclosure or distribution of this material and related documentation
# without an express license agreement from NVIDIA CORPORATION or
# its affiliates is strictly prohibited.

"""GPU frame pipeline: one CUDA stream, a BGRA8 stream ring, the swizzle and the chrome composite.

Ownership rules
    Slots are allocated once at the maximum resolution; a frame occupies the
    top-left `height x width` region of its slot and is handed to ovstream as a
    sub-array with the slot pitch, so a resize never reallocates. A slot is
    acquired for one frame, filled on the pipeline stream, and handed to
    ovstream with the slot's CUDA event as the sync hint. The slot stays owned
    until `release()` is called for it, which the application does once every
    server's `stream_video` has returned (on the publish thread when one is
    used); a frame for which no slot is free is skipped, never written over a
    slot in flight. In fixed-size mode ovstream stages the frame before
    `stream_video` returns. In resizable mode the transport reads the buffer
    without staging and reports no completion, so there the ring depth is what
    keeps a released slot from being rewritten while the encoder may still be
    reading it.

    `zero_copy` (experimental) skips the copy: the mapped renderer output is
    converted and composited in place and streamed directly, with the mapping
    held until the transport has consumed the frame. Measured to save about
    0.1 ms per frame; the copy path stays the default for its ownership clarity.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional, Tuple

import ovrtx
import ovstream
import warp as wp

from . import marker as marker_codec
from .contract import NoFreeSlot
from .ui.layout import Rect


@wp.kernel
def _swap_rb(buf: wp.array3d(dtype=wp.uint8)):
    x, y = wp.tid()
    r = buf[y, x, 0]
    b = buf[y, x, 2]
    buf[y, x, 0] = b
    buf[y, x, 2] = r


@wp.kernel
def _blit(dst: wp.array3d(dtype=wp.uint8), src: wp.array3d(dtype=wp.uint8), x0: int, y0: int,
          width: int, height: int):
    """Copy `src` into `dst` at (x0, y0), clipped to `width` x `height`; channels untouched."""
    x, y = wp.tid()
    if x0 + x >= width or y0 + y >= height:
        return
    dst[y0 + y, x0 + x, 0] = src[y, x, 0]
    dst[y0 + y, x0 + x, 1] = src[y, x, 1]
    dst[y0 + y, x0 + x, 2] = src[y, x, 2]
    dst[y0 + y, x0 + x, 3] = src[y, x, 3]


@dataclass
class StagedFrame:
    slot: int
    video_frame: ovstream.VideoFrame
    pipeline_ms: float      # host time spent issuing the pipeline work
    mapping: object = None  # zero-copy mode: the held ovrtx mapping, released by FramePipeline.release()

    def gpu_ms(self, pipeline: "FramePipeline") -> Optional[float]:
        """GPU time from the pipeline's first command to the slot event, once the slot event has fired.

        Includes waiting for the renderer to finish the mapped output, so it is
        the render-to-ready time the transport's host wait absorbs.
        """
        return pipeline.gpu_elapsed_ms(self.slot)


class FramePipeline:
    """Copies an ovrtx RGBA8 render var into a BGRA8 slot ready for ovstream."""

    RING = 4

    def __init__(self, max_width: int, max_height: int, cuda_device: int = 0, zero_copy: bool = False):
        wp.init()
        self.zero_copy = zero_copy
        self.device = wp.get_device(f"cuda:{cuda_device}")
        self.stream = wp.Stream(self.device)
        self.max_size = (max_width, max_height)
        # [snippet:frame-ring]
        # Plain device allocations, not Warp's memory pool: a resizable WebRTC
        # stream registers the slot with the encoder, which rejects pool memory.
        with wp.ScopedDevice(self.device), wp.ScopedMempool(self.device, False):
            self.ring = [wp.zeros((max_height, max_width, 4), dtype=wp.uint8) for _ in range(self.RING)]
            self.events = [wp.Event(self.device, enable_timing=True) for _ in range(self.RING)]
            self.begin_events = [wp.Event(self.device, enable_timing=True) for _ in range(self.RING)]
        # [/snippet:frame-ring]
        self._next = 0
        self._busy = [False] * self.RING
        self._draw_marker = marker_codec.kernel()

    @property
    def cuda_context(self) -> int:
        return int(self.device.context)

    # [snippet:stage-frame]
    def _acquire_slot(self) -> int:
        for _ in range(self.RING):
            slot = self._next
            self._next = (self._next + 1) % self.RING
            if not self._busy[slot]:
                self._busy[slot] = True
                return slot
        raise NoFreeSlot("all frame slots are still being published")

    def stage(self, render_var: ovrtx.RenderVarOutput, timestamp_ns: int = 0,
              chrome: Optional[Tuple[wp.array, Rect]] = None,
              marker: Optional[marker_codec.Marker] = None, converted: bool = False) -> StagedFrame:
        """Build one frame in a free slot and record the slot event.

        Without `chrome` the rendered scene is the frame: mapped, copied into the slot and
        swizzled in place. With `chrome=(image, viewport)` the exported ovui image is the
        frame: it is copied first, the scene is blitted into `viewport` (clipped), and the
        whole frame is swizzled once. The optional marker is drawn last. Raises NoFreeSlot
        when every slot is in flight."""
        t0 = time.perf_counter()
        slot = self._acquire_slot()
        self.stream.record_event(self.begin_events[slot])
        mapping = render_var.map(device=ovrtx.Device.CUDA, sync_stream=self.stream.cuda_stream)
        try:
            src = wp.from_dlpack(mapping)
            height, width = int(src.shape[0]), int(src.shape[1])
            if width > self.max_size[0] or height > self.max_size[1]:
                raise RuntimeError(f"render output {width}x{height} exceeds the stream maximum {self.max_size}")
            if chrome is not None:
                image, viewport = chrome
                height, width = int(image.shape[0]), int(image.shape[1])
                dst = self.ring[slot][:height, :width]
                wp.copy(dst, image, stream=self.stream)
                # The scene fills the viewport region; a frame rendered at another size
                # (queued across a resize) is clipped to it.
                sw = min(int(src.shape[1]), viewport.width, width - viewport.x)
                sh = min(int(src.shape[0]), viewport.height, height - viewport.y)
                if sw > 0 and sh > 0:
                    wp.launch(_blit, dim=(sw, sh), inputs=[dst, src, viewport.x, viewport.y, width, height],
                              stream=self.stream)
                mapping.unmap(stream=self.stream.cuda_stream)
                mapping = None
                wp.launch(_swap_rb, dim=(width, height), inputs=[dst], stream=self.stream)
            elif self.zero_copy:
                # Experimental: work in place on the renderer's buffer and stream from it. The
                # mapping is held until the transport has consumed the frame (release()). The
                # swizzle must run exactly once per frame, so a re-staged frame passes
                # `converted=True`.
                dst = src
                if not converted:
                    wp.launch(_swap_rb, dim=(width, height), inputs=[dst], stream=self.stream)
            else:
                dst = self.ring[slot][:height, :width]     # sub-array view; pitch is the slot pitch
                wp.copy(dst, src, stream=self.stream)
                mapping.unmap(stream=self.stream.cuda_stream)
                mapping = None
                wp.launch(_swap_rb, dim=(width, height), inputs=[dst], stream=self.stream)
        except Exception:
            self._busy[slot] = False
            if mapping is not None:
                mapping.unmap(stream=self.stream.cuda_stream)
            raise
        if marker is not None:
            mx, my = marker_codec.origin(width, height)
            w0, w1, w2 = marker_codec.encode(marker)
            wp.launch(self._draw_marker, dim=(marker_codec.WIDTH, marker_codec.HEIGHT),
                      inputs=[dst, mx, my, wp.uint32(w0), wp.uint32(w1), wp.uint32(w2)], stream=self.stream)
        self.stream.record_event(self.events[slot])
        video_frame = ovstream.VideoFrame.from_cuda_array(
            dst,
            start_timestamp_ns=timestamp_ns,
            sync=ovstream.CudaSync(stream=self.stream.cuda_stream, wait_event=self.events[slot].cuda_event),
        )
        return StagedFrame(slot=slot, video_frame=video_frame, pipeline_ms=(time.perf_counter() - t0) * 1000.0,
                           mapping=mapping)
    # [/snippet:stage-frame]

    def release(self, staged: StagedFrame) -> None:
        """Give the slot back once every transport has consumed the frame (after
        `stream_video` returned); in zero-copy mode this also unmaps the renderer output.
        Called from the publish thread when one is used."""
        if staged.mapping is not None:
            staged.mapping.unmap(stream=self.stream.cuda_stream)
            staged.mapping = None
        self._busy[staged.slot] = False

    def gpu_elapsed_ms(self, slot: int) -> Optional[float]:
        """Elapsed GPU time between a slot's begin and done events; None if the done event has not fired."""
        if not self.events[slot].is_complete:
            return None
        return float(wp.get_event_elapsed_time(self.begin_events[slot], self.events[slot], synchronize=False))

    def close(self) -> None:
        wp.synchronize_stream(self.stream)
        self.ring = []
        self.events = []
        self.begin_events = []
