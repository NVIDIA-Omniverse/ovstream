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

"""Stream a CUDA frame over the SHM transport, with a colocated reader.

The SHM ("shared memory") backend writes raw BGRA8 frames into a named
shared region for same-machine consumers. This example demonstrates the
full end-to-end loop in one process: a producer thread fills a CUDA
buffer with a moving fill pattern and pushes frames; a reader thread
attaches as an ovstream.Client, pulls frames, and prints throughput.

The reader could just as well live in a separate process / Electron
N-API addon -- the only thing it needs to know is the stream name the
producer used. Run two consumer processes simultaneously to verify
multi-reader semantics.

Usage:
    python main.py                     (auto-named stream)
    python main.py my-stream           (explicit stream name)
    python main.py my-stream --reader  (reader-only, attaches
                                        to a producer that's
                                        already running)

View frames with:  python main_viewer.py my-stream
"""

import argparse
import ctypes
import sys
import threading
import time
from pathlib import Path

import ovstream
import ovstream._bindings as _b
import ovstream_utils

_sdk_dir = Path(_b._find_library()).parent
if sys.platform == "win32":
    def _cudart_major(p):
        try:
            return int(p.stem.split("_")[-1])
        except ValueError:
            return -1
    _candidates = sorted(_sdk_dir.glob("cudart64_*.dll"), key=_cudart_major)
    if not _candidates:
        raise FileNotFoundError(f"No cudart64_*.dll found in {_sdk_dir}")
    _cudart = ctypes.CDLL(str(_candidates[-1]))
else:
    def _cudart_major(p):
        try:
            return int(p.name.rsplit(".", 1)[-1])
        except ValueError:
            return -1
    _candidates = sorted(_sdk_dir.glob("libcudart.so.*"), key=_cudart_major)
    if not _candidates:
        bare = _sdk_dir / "libcudart.so"
        if not bare.exists():
            raise FileNotFoundError(f"No libcudart.so* found in {_sdk_dir}")
        _candidates = [bare]
    _cudart = ctypes.CDLL(str(_candidates[-1]))
_cudart.cudaMalloc.argtypes = [ctypes.POINTER(ctypes.c_void_p), ctypes.c_size_t]
_cudart.cudaMalloc.restype = ctypes.c_int
_cudart.cudaMemset.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_size_t]
_cudart.cudaMemset.restype = ctypes.c_int
_cudart.cudaFree.argtypes = [ctypes.c_void_p]
_cudart.cudaFree.restype = ctypes.c_int


# [snippet:shm-consumer]
def reader_thread(stream_name, stop_event):
    """Attach as a SHM Client and report observed frames + per-second rate."""
    # Wait briefly for the producer to come up.
    deadline = time.monotonic() + 5.0
    client = None
    while time.monotonic() < deadline and not stop_event.is_set():
        try:
            client = ovstream.Client(ovstream.ClientType.SHM,
                                     stream_name=stream_name)
            break
        except ovstream.OvstreamError:
            time.sleep(0.1)

    if client is None:
        print(f"[reader] failed to attach to '{stream_name}'", file=sys.stderr)
        return

    print(f"[reader] attached to '{stream_name}'")
    try:
        observed = 0
        last_report = time.monotonic()
        while not stop_event.is_set() and client.is_alive():
            frame = client.wait_frame(timeout_ms=500)
            if frame is None:
                continue
            observed += 1
            now = time.monotonic()
            if now - last_report >= 1.0:
                print(f"[reader] received {observed} frames "
                      f"(latest seq={frame.sequence}, "
                      f"{frame.width}x{frame.height})")
                observed = 0
                last_report = now
    finally:
        client.close()
        print("[reader] detached")
# [/snippet:shm-consumer]


def run_producer(stream_name, width, height, run_reader):
    ovstream.initialize(
        log_fn=lambda level, ch, msg, ts: print(f"[{level.name}][{ch}] {msg}"),
        log_min_severity=ovstream.LogLevel.WARNING,
    )

    cuda_ptr = ctypes.c_void_p()
    server = None
    stop_event = threading.Event()
    reader = None
    try:
        pitch = width * 4
        frame_size = pitch * height
        if _cudart.cudaMalloc(ctypes.byref(cuda_ptr), frame_size) != 0:
            raise RuntimeError("cudaMalloc failed")

        server = ovstream.Server(ovstream.ServerType.SHM)
        server.on_connection = lambda c: print(
            f"[producer] reader {'attached' if c else 'detached'}"
        )
        cfg = ovstream.ServerConfig(width=width, height=height,
                                    shm_stream_name=stream_name)
        server.start(cfg)
        print(f"[producer] streaming on '{stream_name}'")

        if run_reader:
            reader = threading.Thread(
                target=reader_thread, args=(stream_name, stop_event),
                daemon=True)
            reader.start()

        frame = ovstream.VideoFrame(buffer=cuda_ptr.value,
                                    width=width, height=height,
                                    pitch_bytes=pitch)

        print("Press Ctrl+C to stop.")
        try:
            with ovstream_utils.Loop(ovstream_utils.LoopConfig(fps_target=60)) as loop:
                while True:
                    t = loop.tick()
                    fill = (t.frame_index * 2) % 256
                    _cudart.cudaMemset(cuda_ptr, fill, frame_size)
                    server.stream_video(frame)
                    print(f"\rFPS: {t.stats.fps_current}", end="", flush=True)
        except KeyboardInterrupt:
            pass

    finally:
        stop_event.set()
        if reader is not None:
            reader.join(timeout=2.0)
        if server is not None:
            server.stop()
            server.close()
        if cuda_ptr.value:
            _cudart.cudaFree(cuda_ptr)
        ovstream.shutdown()


def run_reader_only(stream_name):
    # No ovstream.initialize() needed -- a SHM Client is a pure consumer
    # that doesn't depend on the server-side init refcount.
    stop_event = threading.Event()
    try:
        reader_thread(stream_name, stop_event)
    except KeyboardInterrupt:
        pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stream_name", nargs="?", default="local_stream",
                        help="SHM stream identifier (default: local_stream)")
    parser.add_argument("--reader", action="store_true",
                        help="Reader-only: attach to an already-running producer")
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=720)
    args = parser.parse_args()

    if args.reader:
        run_reader_only(args.stream_name)
    else:
        run_producer(args.stream_name, args.width, args.height, run_reader=True)


if __name__ == "__main__":
    main()
