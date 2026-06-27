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
# ]
# ///

"""Display the frames from an ovstream NATIVE (StreamSDK) server in a window.

Connects to a running native server over the network, receives the encoded
video stream, decodes it client-side via StreamSDK's NvStreamingMedia into host
BGRA8 frames, and renders them with OpenCV's `cv2.imshow`. Unlike the SHM/CUDASHM clients this
needs neither the same host nor the same GPU as the producer -- only an NVIDIA
GPU on this (the consumer) machine for the decode.

Mouse and keyboard events from the OpenCV window are forwarded to the server
over the input stream, so a producer that registered an input callback (e.g.
`basic_stream native` or `ovrtx_stream native`) sees the same events it would
from any other client.

Run a native server first, e.g.:
    python ../basic_stream/main.py native

Requires: pip install opencv-python numpy. Needs an NVIDIA GPU + driver.

Usage:
    python main.py                       # connect to 127.0.0.1:49100
    python main.py 10.0.0.5              # connect to a remote server
    python main.py 10.0.0.5 --signal-port 50000

Press 'q' or close the window to exit.
"""

import argparse
import sys
import time

try:
    import cv2
except ImportError:
    sys.stderr.write("This example requires OpenCV. Install with:  pip install opencv-python\n")
    sys.exit(1)

import ovstream
from ovstream import Client, ClientType


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("server_ip", nargs="?", default="127.0.0.1",
                        help="native server address (default 127.0.0.1)")
    parser.add_argument("--signal-port", type=int, default=0,
                        help="signaling port (0 = default 49100)")
    parser.add_argument("--stream-port", type=int, default=0,
                        help="media/streaming port (0 = default 47999)")
    parser.add_argument("--retry-seconds", type=float, default=10.0,
                        help="how long to keep retrying the connection")
    args = parser.parse_args()

    # nvstConnectToServer fails synchronously if the server isn't reachable;
    # retry so the launch order is forgiving.
    deadline = time.monotonic() + args.retry_seconds
    client = None
    last_err = ""
    while time.monotonic() < deadline:
        try:
            client = Client(ClientType.NATIVE, server_ip=args.server_ip,
                            signal_port=args.signal_port, stream_port=args.stream_port)
            break
        except ovstream.OvstreamError as e:
            last_err = str(e)
            time.sleep(0.5)
    if client is None:
        sys.stderr.write(f"Failed to connect to {args.server_ip} after "
                         f"{args.retry_seconds:.1f}s: {last_err}\n")
        return 1

    print(f"Connected to {args.server_ip}. Press 'q' to exit.")
    window = f"ovstream native: {args.server_ip}"
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

            arr = frame.as_numpy()             # (H, pitch//4, 4) uint8 BGRA
            visible = arr[:, :frame.width, :]  # (H, W, 4) BGRA
            cv2.imshow(window, visible)

            raw_key = cv2.waitKey(1)
            if raw_key != -1:
                key = raw_key & 0xFF
                if key == ord("q") or key == 27:  # q or ESC
                    break
                _forward_key(client, key)
            if cv2.getWindowProperty(window, cv2.WND_PROP_VISIBLE) < 1:
                break

        if not client.is_alive():
            print("Connection closed.")
    finally:
        cv2.destroyAllWindows()
        client.close()

    return 0


def _make_mouse_callback(client, dims):
    """cv2 mouse callback forwarding events to the server. `dims` carries the
    latest frame's width/height so MOVE events report the server's coordinate
    space. Send failures (e.g. the connection just dropped) are swallowed."""
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

    return on_mouse


def _forward_key(client, key_code: int) -> None:
    """Forward a single cv2.waitKey code as a key-DOWN event (cv2 doesn't
    surface key release)."""
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
