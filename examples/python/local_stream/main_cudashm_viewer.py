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
#     "opencv-python",
#     "numpy",
# ]
# ///

"""Display the frames from an ovstream CUDASHM stream in a window.

Attaches to a running CUDASHM producer by stream name, imports the
producer's CUDA IPC ring buffer handles, and on each new frame copies
the slot's GPU pixels back to host (`cudaMemcpy2D` D2H) and renders
them with OpenCV's `cv2.imshow`. The D2H copy is purely for display
-- a real consumer (simulation kernel, GPU post-processing) would
launch its own CUDA kernels directly against the device pointer
`frame.device_ptr` and never touch host memory.

The viewer also forwards mouse and keyboard events from the OpenCV
window back to the producer over the CUDASHM control channel. Producers
that registered an input callback (e.g. `ovrtx_stream cudashm:...`)
see exactly the same events they'd see from a WebRTC client.

Run this alongside any CUDASHM producer -- e.g. `basic_stream
cudashm:my-stream` from sdk/examples/python/basic_stream/ or any other
pluggable example. Both sides must agree on the stream name (default:
`local_stream`).

IMPORTANT: This viewer cannot run in the same process as the producer.
CUDA forbids `cudaIpcOpenMemHandle` in the process that called
`cudaIpcGetMemHandle`, so launch the producer in one terminal and the
viewer in another.

Requires: pip install opencv-python numpy

Usage:
    python main_cudashm_viewer.py                # default 'local_stream'
    python main_cudashm_viewer.py demo-stream    # explicit stream name

Press 'q' or close the window to exit.
"""

import argparse
import ctypes
import sys
import time
from pathlib import Path

try:
    import cv2
except ImportError:
    sys.stderr.write("This example requires OpenCV. Install with:  pip install opencv-python\n")
    sys.exit(1)

try:
    import numpy as np
except ImportError:
    sys.stderr.write("This example requires numpy. Install with:  pip install numpy\n")
    sys.exit(1)

import ovstream
import ovstream._bindings as _b


# Load the CUDA runtime that ships next to ovstream so the viewer's
# D2H cudaMemcpy lands on the same driver instance the
# ovstream_client library imported the IPC handles into. Mirrors the
# pattern in local_stream/main.py.
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

# cudaMemcpy2D for the per-frame D2H copy. cudaMemcpyDeviceToHost = 2.
_cudaMemcpyDeviceToHost = 2
_cudart.cudaMemcpy2D.argtypes = [
    ctypes.c_void_p,   # dst
    ctypes.c_size_t,   # dpitch
    ctypes.c_void_p,   # src
    ctypes.c_size_t,   # spitch
    ctypes.c_size_t,   # width (bytes per row)
    ctypes.c_size_t,   # height (rows)
    ctypes.c_int,      # cudaMemcpyKind
]
_cudart.cudaMemcpy2D.restype = ctypes.c_int
_cudart.cudaGetErrorString.argtypes = [ctypes.c_int]
_cudart.cudaGetErrorString.restype = ctypes.c_char_p
_cudart.cudaSetDevice.argtypes = [ctypes.c_int]
_cudart.cudaSetDevice.restype = ctypes.c_int


def _copy_slot_to_host(frame: ovstream.Frame) -> "np.ndarray":
    """D2H-copy one cudashm slot into a numpy BGRA buffer.

    Returns a fresh (height, width, 4) uint8 array trimmed to the
    producer's actual pixel rectangle (pitch padding stripped).
    """
    pitch = frame.pitch_bytes
    row_bytes = frame.width * 4
    host = np.empty((frame.height, pitch // 4, 4), dtype=np.uint8)
    err = _cudart.cudaMemcpy2D(
        host.ctypes.data,
        pitch,
        ctypes.c_void_p(frame.device_ptr),
        pitch,
        row_bytes,
        frame.height,
        _cudaMemcpyDeviceToHost,
    )
    if err != 0:
        msg = _cudart.cudaGetErrorString(err)
        raise RuntimeError(
            f"cudaMemcpy2D D2H failed: {msg.decode('utf-8', 'replace') if msg else err}"
        )
    return host[:, :frame.width, :]


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("stream_name", nargs="?", default="local_stream",
                        help="CUDASHM stream identifier (must match the producer)")
    parser.add_argument("--retry-seconds", type=float, default=10.0,
                        help="how long to wait for the producer if it isn't running yet")
    parser.add_argument("--device", type=int, default=None,
                        help="CUDA device to attach on; must match the producer's "
                             "cuda_device unless the two GPUs support peer access. "
                             "Defaults to the current device (0). The producer's "
                             "device is named in the attach error on a mismatch.")
    args = parser.parse_args()

    # CUDA IPC imports the producer's buffers into this process's active
    # device, so select the producer's GPU before attaching when it's
    # not the default and the GPUs can't peer-access each other.
    if args.device is not None:
        err = _cudart.cudaSetDevice(args.device)
        if err != 0:
            msg = _cudart.cudaGetErrorString(err)
            sys.stderr.write(f"cudaSetDevice({args.device}) failed: "
                             f"{msg.decode() if msg else err}\n")
            return 1

    # Wait briefly for the producer to come up.
    deadline = time.monotonic() + args.retry_seconds
    client = None
    last_err = ""
    while time.monotonic() < deadline:
        try:
            client = ovstream.Client(ovstream.ClientType.CUDASHM,
                                     stream_name=args.stream_name)
            break
        except ovstream.OvstreamError as e:
            last_err = str(e)
            time.sleep(0.2)
    if client is None:
        sys.stderr.write(f"Failed to attach to '{args.stream_name}' after "
                         f"{args.retry_seconds:.1f}s: {last_err}\n")
        return 1

    print(f"Attached to '{args.stream_name}' (producer GPU: device "
          f"{client.get_producer_device()}). Press 'q' to exit.")
    window = f"ovstream-cudashm: {args.stream_name}"
    cv2.namedWindow(window, cv2.WINDOW_NORMAL)

    dims = {"width": 0, "height": 0}
    cv2.setMouseCallback(window, _make_mouse_callback(client, dims))

    try:
        while client.is_alive():
            frame = client.wait_frame(timeout_ms=500)
            if frame is None:
                if cv2.getWindowProperty(window, cv2.WND_PROP_VISIBLE) < 1:
                    break
                continue

            dims["width"] = frame.width
            dims["height"] = frame.height

            visible = _copy_slot_to_host(frame)        # (H, W, 4) BGRA
            cv2.imshow(window, visible)

            raw_key = cv2.waitKey(1)
            if raw_key != -1:
                key = raw_key & 0xFF
                if key == ord("q") or key == 27:
                    break
                _forward_key(client, key)
            if cv2.getWindowProperty(window, cv2.WND_PROP_VISIBLE) < 1:
                break

        if not client.is_alive():
            print("Producer stopped.")
    finally:
        cv2.destroyAllWindows()
        client.close()

    return 0


def _make_mouse_callback(client, dims):
    """Same shape as main_viewer.py's mouse callback; forwards events over the
    CUDASHM control channel."""
    button_map = {
        cv2.EVENT_LBUTTONDOWN: (ovstream.MouseButton.LEFT,   ovstream.KeyState.DOWN),
        cv2.EVENT_LBUTTONUP:   (ovstream.MouseButton.LEFT,   ovstream.KeyState.UP),
        cv2.EVENT_MBUTTONDOWN: (ovstream.MouseButton.MIDDLE, ovstream.KeyState.DOWN),
        cv2.EVENT_MBUTTONUP:   (ovstream.MouseButton.MIDDLE, ovstream.KeyState.UP),
        cv2.EVENT_RBUTTONDOWN: (ovstream.MouseButton.RIGHT,  ovstream.KeyState.DOWN),
        cv2.EVENT_RBUTTONUP:   (ovstream.MouseButton.RIGHT,  ovstream.KeyState.UP),
    }

    def _send(evt):
        try:
            client.send_input_event(evt)
        except ovstream.OvstreamError:
            pass

    def on_mouse(event, x, y, flags, _param):
        w = dims["width"]
        h = dims["height"]
        if w == 0 or h == 0:
            return

        if event == cv2.EVENT_MOUSEMOVE:
            _send(ovstream.InputEvent(
                type=ovstream.InputEventType.MOUSE,
                mouse=ovstream.MouseEvent(
                    type=ovstream.MouseEventType.MOVE,
                    modifiers=0, x=x, y=y, data=w, data2=h,
                    button_state=ovstream.KeyState.UP,
                ),
            ))
        elif event in button_map:
            button, state = button_map[event]
            _send(ovstream.InputEvent(
                type=ovstream.InputEventType.MOUSE,
                mouse=ovstream.MouseEvent(
                    type=ovstream.MouseEventType.BUTTON,
                    modifiers=0, x=x, y=y,
                    data=int(button), data2=0,
                    button_state=state,
                ),
            ))
        elif event == cv2.EVENT_MOUSEWHEEL:
            delta = (flags >> 16) & 0xFFFF
            if delta & 0x8000:
                delta -= 0x10000
            _send(ovstream.InputEvent(
                type=ovstream.InputEventType.MOUSE,
                mouse=ovstream.MouseEvent(
                    type=ovstream.MouseEventType.WHEEL,
                    modifiers=0, x=x, y=y, data=0, data2=0,
                    button_state=ovstream.KeyState.UP,
                    scroll_y=delta / 120.0,
                ),
            ))

    return on_mouse


def _forward_key(client, key_code: int) -> None:
    try:
        client.send_input_event(ovstream.InputEvent(
            type=ovstream.InputEventType.KEYBOARD,
            keyboard=ovstream.KeyboardEvent(
                key_code=key_code, scan_code=0, modifiers=0,
                key_state=ovstream.KeyState.DOWN,
            ),
        ))
    except ovstream.OvstreamError:
        pass


if __name__ == "__main__":
    sys.exit(main())
