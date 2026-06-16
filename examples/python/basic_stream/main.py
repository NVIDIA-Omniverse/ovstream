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
# dependencies = ["ovstream"]
# ///

"""Stream a CUDA frame via WebRTC, RTSP, native, SHM, or any combination.

Uses ctypes to call cudaMalloc/cudaMemset directly (no WARP or CuPy needed).

Usage:
    python main.py                       (no args: WebRTC on default signal port)
    python main.py webrtc                (WebRTC on default signal port)
    python main.py webrtc:50000          (WebRTC on signal port 50000)
    python main.py rtsp                  (RTSP on default port)
    python main.py rtsp:9000             (RTSP on port 9000)
    python main.py shm                   (SHM, stream name auto: ovstream-<pid>)
    python main.py shm:my-stream         (SHM with explicit stream name)
    python main.py webrtc rtsp shm:demo  (three transports simultaneously)

For network specs the value after the colon is a port; for `shm` it is
the stream name an `ovstream.ShmClient` (or the bundled
`examples/python/local_stream/main_viewer.py`) attaches to.

View RTSP with:  ffplay rtsp://localhost:8554/stream
View SHM with:   python examples/python/local_stream/main_viewer.py <stream-name>
"""

import ctypes
import os
import sys

from pathlib import Path

import ovstream
import ovstream._bindings as _b
import ovstream_utils

# Minimal CUDA runtime bindings. The bundled CUDA runtime is shipped
# next to the ovstream library with a soversion-suffixed name
# (cudart64_<major>.dll on Windows, libcudart.so.<major> on Linux).
# Bare `libcudart.so` is the dev-tree symlink and is NOT present in a
# pip-installed wheel; glob the staged soversioned file instead. Sort
# numerically on the major so libcudart.so.12 compares greater than
# libcudart.so.9 (lexicographic sort would pick the wrong file when
# CUDA reaches 10+).
# [snippet:bundled-cudart]
_sdk_dir = Path(_b._find_library()).parent
if sys.platform == "win32":
    def _cudart_major(p: Path) -> int:
        try:
            return int(p.stem.split("_")[-1])
        except ValueError:
            return -1
    _candidates = sorted(_sdk_dir.glob("cudart64_*.dll"), key=_cudart_major)
    if not _candidates:
        raise FileNotFoundError(
            f"No cudart64_*.dll found next to ovstream.dll in {_sdk_dir}"
        )
    _cudart = ctypes.CDLL(str(_candidates[-1]))
else:
    def _cudart_major(p: Path) -> int:
        # `libcudart.so.12` -> name="libcudart.so.12", split(".")[-1]="12".
        try:
            return int(p.name.rsplit(".", 1)[-1])
        except ValueError:
            return -1
    _candidates = sorted(_sdk_dir.glob("libcudart.so.*"), key=_cudart_major)
    if not _candidates:
        # Fall back to the bare symlink (dev tree); raise a clear error
        # if neither is staged.
        bare = _sdk_dir / "libcudart.so"
        if not bare.exists():
            raise FileNotFoundError(
                f"No libcudart.so* found next to libovstream.so in {_sdk_dir}"
            )
        _candidates = [bare]
    _cudart = ctypes.CDLL(str(_candidates[-1]))
_cudart.cudaMalloc.argtypes = [ctypes.POINTER(ctypes.c_void_p), ctypes.c_size_t]
_cudart.cudaMalloc.restype = ctypes.c_int
_cudart.cudaMemset.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_size_t]
_cudart.cudaMemset.restype = ctypes.c_int
_cudart.cudaFree.argtypes = [ctypes.c_void_p]
_cudart.cudaFree.restype = ctypes.c_int
# [/snippet:bundled-cudart]

PROTOCOL_MAP = {
    "rtsp": ovstream.ServerType.RTSP,
    "webrtc": ovstream.ServerType.WEBRTC,
    "native": ovstream.ServerType.NATIVE,
    "shm": ovstream.ServerType.SHM,
    "cudashm": ovstream.ServerType.CUDASHM,
}


def parse_server_spec(spec: str):
    """Parse a `protocol[:detail]` spec into (ServerType, detail, label).

    `detail` is the port (int) for network protocols, the stream name
    (str) for SHM and CUDASHM, or 0 / "" if unspecified. The caller
    dispatches on ServerType to decide which config field to populate.
    """
    parts = spec.split(":", 1)
    protocol = parts[0].lower()

    if protocol not in PROTOCOL_MAP:
        print(f"Unknown protocol: {protocol}")
        print("  Protocols: webrtc, rtsp, native, shm, cudashm")
        sys.exit(1)

    server_type = PROTOCOL_MAP[protocol]

    if server_type in (ovstream.ServerType.SHM, ovstream.ServerType.CUDASHM):
        stream_name = parts[1] if len(parts) > 1 else ""
        label = f"{protocol.upper()}:{stream_name or '<auto>'}"
        return server_type, stream_name, label

    port = int(parts[1]) if len(parts) > 1 else 0
    default_port = 8554 if protocol == "rtsp" else 49100
    label = f"{protocol.upper()}:{port or default_port}"
    return server_type, port, label


def parse_ice_servers_env():
    """Parse the optional OVSTREAM_ICE_SERVERS env var into a list of
    WebRTCIceServer entries. Format::

        OVSTREAM_ICE_SERVERS="stun:host:port|turn:host:port,user,pass"

    Entries are separated by ``|``; within an entry, commas split into
    ``urls,username,credential`` (credential is the password / token).
    Empty or missing env var produces an empty list (no ICE servers).
    """
    raw = os.environ.get("OVSTREAM_ICE_SERVERS", "")
    if not raw.strip():
        return []
    servers = []
    for entry in raw.split("|"):
        parts = entry.split(",", 2)
        servers.append(ovstream.WebRTCIceServer(
            urls=parts[0],
            username=parts[1] if len(parts) > 1 else "",
            credential=parts[2] if len(parts) > 2 else "",
        ))
    return servers


def make_message_handler(srv):
    """Echo incoming client messages back to the client.

    Defined as a named function rather than a lambda so exceptions raised
    by `send_message` surface on stderr instead of being silently swallowed
    by the callback invocation layer.
    """
    def on_message(msg):
        print(f"Client says: {msg}")
        try:
            srv.send_message(msg)
        except ovstream.OvstreamError as e:
            print(f"Failed to echo message: {e}", file=sys.stderr)
    return on_message


def main():
    specs = [parse_server_spec(a) for a in sys.argv[1:]] if len(sys.argv) > 1 else [
        (ovstream.ServerType.WEBRTC, 0, "WebRTC:49100"),
    ]

    W, H = 1920, 1080

    # [snippet:initialize-sdk]
    ovstream.initialize(
        log_fn=lambda level, ch, msg, ts: print(f"[{level.name}][{ch}] {msg}"),
        log_min_severity=ovstream.LogLevel.ERROR,
    )
    # [/snippet:initialize-sdk]

    ice_servers = parse_ice_servers_env()
    if ice_servers:
        print(f"Configured {len(ice_servers)} ICE server(s) from OVSTREAM_ICE_SERVERS")

    # Everything below init must tear down on the way out even when
    # cudaMalloc / start / render-loop raises, or we leak the OVSTREAM
    # refcount and the next test / retry inherits a half-initialized
    # library.
    cuda_ptr = ctypes.c_void_p()
    servers = []
    frame_count = 0
    try:
        pitch = W * 4
        frame_size = pitch * H
        if _cudart.cudaMalloc(ctypes.byref(cuda_ptr), frame_size) != 0:
            raise RuntimeError("cudaMalloc failed")

        # [snippet:create-server]
        for server_type, detail, label in specs:
            s = ovstream.Server(server_type)
            s.on_connection = lambda c, lbl=label: print(f"[{lbl}] Client {'connected' if c else 'disconnected'}")

            # Reverse-channel callbacks only fire on transports that have
            # one. WebRTC, native, SHM, and CUDASHM do; RTSP does not.
            # Enumerated explicitly so a future backend without a
            # reverse channel is not silently opted in.
            if server_type in (ovstream.ServerType.WEBRTC,
                               ovstream.ServerType.NATIVE,
                               ovstream.ServerType.SHM,
                               ovstream.ServerType.CUDASHM):
                s.on_message = make_message_handler(s)
                # Match the C basic_stream's onInput shape: print
                # KEYBOARD events with their key state, and mouse
                # *button* events (skipping move / wheel chatter).
                def _on_input(event):
                    if event.type == ovstream.InputEventType.KEYBOARD:
                        kb = event.keyboard
                        state = ("down" if kb.key_state == ovstream.KeyState.DOWN
                                 else "up")
                        print(f"Keyboard: key_code={kb.key_code} {state}")
                    elif (event.type == ovstream.InputEventType.MOUSE
                          and event.mouse.type == ovstream.MouseEventType.BUTTON):
                        mouse = event.mouse
                        state = ("down" if mouse.button_state == ovstream.KeyState.DOWN
                                 else "up")
                        print(f"Mouse button: {mouse.data} {state}")
                s.on_input = _on_input

            cfg = ovstream.ServerConfig(width=W, height=H)
            if server_type == ovstream.ServerType.RTSP:
                if detail:
                    cfg.stream_port = detail
            elif server_type == ovstream.ServerType.SHM:
                if detail:
                    cfg.shm_stream_name = detail
            elif server_type == ovstream.ServerType.CUDASHM:
                if detail:
                    cfg.cudashm_stream_name = detail
            else:  # WebRTC / Native
                if detail:
                    cfg.webrtc_signal_port = detail
                # ICE servers are WebRTC/native-only; the setter rejects
                # other backends with NOT_SUPPORTED, so we only set them
                # for the StreamSDK-backed transports.
                if ice_servers:
                    s.set_webrtc_ice_servers(ice_servers)

            s.start(cfg)
        # [/snippet:create-server]

            if server_type == ovstream.ServerType.RTSP:
                print(f"[{label}] rtsp://localhost:{detail or 8554}/stream")
            elif server_type == ovstream.ServerType.SHM:
                print(f"[{label}] attach with: python examples/python/local_stream/main_viewer.py "
                      f"{detail or '<see ovstream log for auto name>'}")
            elif server_type == ovstream.ServerType.CUDASHM:
                print(f"[{label}] attach with: python examples/python/local_stream/main_cudashm_viewer.py "
                      f"{detail or '<see ovstream log for auto name>'}")
            else:
                print(f"[{label}] signal port {detail or 49100}")

            servers.append(s)

        print("Press Ctrl+C to stop.")

        # [snippet:stream-loop]
        frame = ovstream.VideoFrame(buffer=cuda_ptr.value, width=W, height=H, pitch_bytes=pitch)

        # ovstream_utils.Loop is an optional frame pacer bundled with
        # ovstream. loop.tick() blocks as needed to hit fps_target and
        # returns a Tick with the measured per-frame dt and a frame
        # index. Bundled with ovstream but not required to use it.
        try:
            with ovstream_utils.Loop(ovstream_utils.LoopConfig(fps_target=60)) as loop:
                while True:
                    t = loop.tick()

                    fill = (t.frame_index * 2) % 256
                    _cudart.cudaMemset(cuda_ptr, fill, frame_size)

                    for s in servers:
                        try:
                            s.stream_video(frame)
                        except ovstream.OvstreamError:
                            pass

                    print(f"\rFPS: {t.stats.fps_current}", end="", flush=True)
                    frame_count = t.frame_index + 1
        except KeyboardInterrupt:
            pass
        # [/snippet:stream-loop]

        print(f"\nDone. Streamed {frame_count} frames.")
    finally:
        # [snippet:cleanup]
        for s in servers:
            s.stop()
            s.close()
        if cuda_ptr.value:
            _cudart.cudaFree(cuda_ptr)
        ovstream.shutdown()
        # [/snippet:cleanup]


if __name__ == "__main__":
    main()
