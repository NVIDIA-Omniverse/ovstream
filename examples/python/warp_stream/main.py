#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary
#
# NVIDIA CORPORATION, its affiliates and licensors retain all intellectual
# property and proprietary rights in and to this material, related
# documentation and any modifications thereto. Any use, reproduction,
# disclosure or distribution of this material and related documentation
# without an express license agreement from NVIDIA CORPORATION or
# its affiliates is strictly prohibited.

# /// script
# requires-python = ">=3.8"
# dependencies = [
#     "ovstream",
#     "warp-lang",
# ]
# ///

"""Render frames with NVIDIA WARP and stream via WebRTC, RTSP, SHM, or any combination.

Demonstrates two API features together:

  - ``VideoInput.TENSOR`` / DLPack input: the server is configured
    with ``video_input=VideoInput.TENSOR`` and the Warp array is
    handed in via ``VideoFrame.from_dlpack(buf, ...)``, which picks
    up the underlying CUDA pointer through the standard DLPack
    capsule protocol. Works for any DLPack-speaking producer (Warp,
    CuPy, PyTorch, ovrtx, ...).

  - ``CudaSync`` stream/event hint: instead of calling
    ``wp.synchronize()`` between the kernel and ``stream_video``,
    the example records an event on Warp's stream after each
    ``wp.launch`` and passes the stream+event handles via
    ``sync=CudaSync(...)``. The SHM backend chains its
    device-to-host memcpy on the event without a host block; RTSP /
    WebRTC host-block on the event before submitting (the caller
    still avoids the global ``cudaDeviceSynchronize``).

The raw-CUDA pointer path (``VideoInput.CUDA`` +
``VideoFrame.from_cuda_array``) is the alternative; see
basic_stream / ovrtx_stream for that.

Requires: pip install warp-lang

Usage:
    python main.py                       (no args: WebRTC on default signal port)
    python main.py webrtc                (WebRTC with callbacks)
    python main.py rtsp                  (RTSP on default port)
    python main.py shm:my-stream         (SHM with explicit stream name)
    python main.py webrtc rtsp:9000 shm  (three transports simultaneously)

View RTSP with:  ffplay rtsp://localhost:8554/stream
View SHM with:   python examples/python/local_stream/main_viewer.py <stream-name>
"""

import sys

try:
    import warp as wp
except ImportError:
    print("This example requires NVIDIA WARP: pip install warp-lang")
    sys.exit(1)

import ovstream
import ovstream_utils

wp.init()


def make_message_handler(srv):
    """Echo incoming client messages back to the client.

    Defined as a named function rather than a lambda so exceptions raised
    by `send_message` surface on stderr instead of being silently swallowed
    by the callback invocation layer (same pattern as basic_stream's main.py).
    """
    def on_message(msg):
        print(f"Client says: {msg}")
        try:
            srv.send_message(msg)
        except ovstream.OvstreamError as e:
            print(f"Failed to echo message: {e}", file=sys.stderr)
    return on_message

PROTOCOL_MAP = {
    "rtsp": ovstream.ServerType.RTSP,
    "webrtc": ovstream.ServerType.WEBRTC,
    "native": ovstream.ServerType.NATIVE,
    "shm": ovstream.ServerType.SHM,
}


def parse_server_spec(spec: str):
    """Parse a `protocol[:detail]` spec into (ServerType, detail, label).

    `detail` is the port (int) for network protocols, the stream name
    (str) for SHM, or 0 / "" if unspecified. The caller dispatches on
    ServerType to decide which config field to populate.
    """
    parts = spec.split(":", 1)
    protocol = parts[0].lower()

    if protocol not in PROTOCOL_MAP:
        print(f"Unknown protocol: {protocol}")
        print("  Protocols: webrtc, rtsp, native, shm")
        sys.exit(1)

    server_type = PROTOCOL_MAP[protocol]

    if server_type == ovstream.ServerType.SHM:
        stream_name = parts[1] if len(parts) > 1 else ""
        label = f"SHM:{stream_name or '<auto>'}"
        return server_type, stream_name, label

    port = int(parts[1]) if len(parts) > 1 else 0
    default_port = 8554 if protocol == "rtsp" else 49100
    label = f"{protocol.upper()}:{port or default_port}"
    return server_type, port, label


# [snippet:warp-kernel]
@wp.kernel
def draw_gradient(buf: wp.array3d(dtype=wp.uint8), frame: int):
    x, y, c = wp.tid()
    if c == 0:
        buf[y, x, c] = wp.uint8((x + frame) % 256)
    elif c == 1:
        buf[y, x, c] = wp.uint8((y + frame) % 256)
    elif c == 2:
        buf[y, x, c] = wp.uint8((x + y + frame) % 256)
    else:
        buf[y, x, c] = wp.uint8(255)
# [/snippet:warp-kernel]


def main():
    specs = [parse_server_spec(a) for a in sys.argv[1:]] if len(sys.argv) > 1 else [
        (ovstream.ServerType.WEBRTC, 0, "WebRTC:49100"),
    ]

    W, H = 1920, 1080
    frame_count = 0

    ovstream.initialize(
        log_fn=lambda level, ch, msg, ts: print(f"[{level.name}][{ch}] {msg}"),
        log_min_severity=ovstream.LogLevel.ERROR,
    )
    try:
        servers = []
        for server_type, detail, label in specs:
            s = ovstream.Server(server_type)
            s.on_connection = lambda c, lbl=label: print(f"[{lbl}] Client {'connected' if c else 'disconnected'}")

            if server_type in (ovstream.ServerType.WEBRTC,
                               ovstream.ServerType.NATIVE,
                               ovstream.ServerType.SHM):
                s.on_message = make_message_handler(s)
                s.on_input = lambda event: (
                    print(f"Input: {event.type.name} key={event.keyboard.key_code}")
                    if event.type == ovstream.InputEventType.KEYBOARD else None
                )

            cfg = ovstream.ServerConfig(
                width=W,
                height=H,
                video_input=ovstream.VideoInput.TENSOR,
            )
            if server_type == ovstream.ServerType.RTSP:
                if detail:
                    cfg.stream_port = detail
            elif server_type == ovstream.ServerType.SHM:
                if detail:
                    cfg.shm_stream_name = detail
            else:  # WebRTC / Native
                if detail:
                    cfg.webrtc_signal_port = detail

            s.start(cfg)

            if server_type == ovstream.ServerType.RTSP:
                print(f"[{label}] rtsp://localhost:{detail or 8554}/stream")
            elif server_type == ovstream.ServerType.SHM:
                print(f"[{label}] attach with: python examples/python/local_stream/main_viewer.py "
                      f"{detail or '<see ovstream log for auto name>'}")
            else:
                print(f"[{label}] signal port {detail or 49100}")

            servers.append(s)

        print("Press Ctrl+C to stop.")

        # [snippet:warp-buffer-alloc]
        buf = wp.zeros((H, W, 4), dtype=wp.uint8, device="cuda:0")
        # [/snippet:warp-buffer-alloc]

        # CUDA stream + event reused across frames: record the event
        # after each draw_gradient launch and hand it to ovstream so
        # the encoder ingest chains on it (no host-side wp.synchronize).
        draw_stream = wp.get_stream("cuda:0")
        draw_event  = wp.Event(device="cuda:0")

        # ovstream_utils.Loop is an optional frame pacer bundled with
        # ovstream. loop.tick() blocks as needed to hit fps_target and
        # returns a Tick with the measured per-frame dt and a frame
        # index. Bundled with ovstream but not required to use it.
        # [snippet:warp-stream-loop]
        try:
            with ovstream_utils.Loop(ovstream_utils.LoopConfig(fps_target=60)) as loop:
                while True:
                    t = loop.tick()

                    wp.launch(draw_gradient, dim=(W, H, 4), inputs=[buf, t.frame_index], device="cuda:0")
                    draw_stream.record_event(draw_event)

                    video_frame = ovstream.VideoFrame.from_dlpack(
                        buf,
                        sync=ovstream.CudaSync(
                            stream=draw_stream.cuda_stream,
                            wait_event=draw_event.cuda_event,
                        ),
                    )
                    for s in servers:
                        try:
                            s.stream_video(video_frame)
                        except ovstream.OvstreamError:
                            pass

                    print(f"\rFPS: {t.stats.fps_current}", end="", flush=True)
                    frame_count = t.frame_index + 1
        except KeyboardInterrupt:
            pass
        # [/snippet:warp-stream-loop]

        for s in servers:
            try:
                s.stop()
            except ovstream.OvstreamError:
                pass
            s.close()
    finally:
        # Always shutdown so the init/shutdown ref count stays balanced
        # even if an exception escapes the loop above.
        ovstream.shutdown()
        print(f"\nDone. Streamed {frame_count} frames.")


if __name__ == "__main__":
    main()
