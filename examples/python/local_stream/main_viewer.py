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

"""Display the frames from an ovstream SHM stream in a window.

Attaches to a running SHM producer by stream name, pulls BGRA8 frames
straight out of the shared region (zero-copy), and renders them with
OpenCV's `cv2.imshow`. OpenCV is already BGR-native, so BGRA frames
display without a color swap.

The viewer also forwards mouse and keyboard events from the OpenCV
window back to the producer over the SHM control channel. Producers
that registered an input callback (e.g. `starfield_stream shm:...` or
`ovrtx_stream shm:...`) see exactly the same events they'd see from
a WebRTC client, so SHM is an interactive transport in practice.

Run this alongside any SHM producer -- e.g. `local_stream` (C example)
or `main.py` from this directory started without `--reader`. Both sides
must agree on the stream name (default: `local_stream`).

Requires: pip install opencv-python numpy

Usage:
    python main_viewer.py                # default stream name 'local_stream'
    python main_viewer.py demo-stream    # explicit stream name

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


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("stream_name", nargs="?", default="local_stream",
                        help="SHM stream identifier (must match the producer)")
    parser.add_argument("--retry-seconds", type=float, default=10.0,
                        help="how long to wait for the producer if it isn't running yet")
    args = parser.parse_args()

    # Wait briefly for the producer to come up. This makes the
    # producer/viewer launch order forgiving -- you can start either
    # one first.
    deadline = time.monotonic() + args.retry_seconds
    client = None
    last_err = ""
    while time.monotonic() < deadline:
        try:
            client = ovstream.ShmClient(args.stream_name)
            break
        except ovstream.OvstreamError as e:
            last_err = str(e)
            time.sleep(0.2)
    if client is None:
        sys.stderr.write(f"Failed to attach to '{args.stream_name}' after "
                         f"{args.retry_seconds:.1f}s: {last_err}\n")
        return 1

    print(f"Attached to '{args.stream_name}'. Press 'q' to exit.")
    window = f"ovstream: {args.stream_name}"
    # cv2.WINDOW_NORMAL lets the user resize; the BGRA frame is uploaded
    # at its native dimensions every frame.
    cv2.namedWindow(window, cv2.WINDOW_NORMAL)

    # Latest frame's dimensions, kept in a dict so the mouse callback
    # (which can fire on a separate Qt/GTK event thread inside OpenCV)
    # always uses the producer's current resolution. data / data2 on
    # MOVE events feed the producer's normalization x/data, y/data2,
    # so they must match the frame the cursor was over.
    dims = {"width": 0, "height": 0}

    cv2.setMouseCallback(window, _make_mouse_callback(client, dims))

    try:
        while client.is_producer_alive():
            frame = client.wait_frame(timeout_ms=500)
            if frame is None:
                # Timeout; check window state and loop again.
                if cv2.getWindowProperty(window, cv2.WND_PROP_VISIBLE) < 1:
                    break
                continue

            dims["width"] = frame.width
            dims["height"] = frame.height

            # Zero-copy view into shared memory. The pitch may include
            # padding past `width` to align rows; trim to the actual
            # pixel rectangle before display.
            arr = frame.as_numpy()                       # (H, pitch//4, 4) uint8 BGRA
            visible = arr[:, :frame.width, :]            # (H, W, 4) BGRA
            cv2.imshow(window, visible)

            # waitKey is required for cv2.imshow to actually pump
            # window events; 1 ms is enough to keep a 60 FPS feed
            # responsive. Return is -1 when no key was pressed; anything
            # else is a Unicode-ish code point we forward as a key DOWN.
            raw_key = cv2.waitKey(1)
            if raw_key != -1:
                key = raw_key & 0xFF
                if key == ord("q") or key == 27:  # q or ESC
                    break
                _forward_key(client, key)
            if cv2.getWindowProperty(window, cv2.WND_PROP_VISIBLE) < 1:
                break

        if not client.is_producer_alive():
            print("Producer stopped.")
    finally:
        cv2.destroyAllWindows()
        client.close()

    return 0


def _make_mouse_callback(client, dims):
    """Build the cv2 mouse callback that forwards events to the producer.

    `dims` is a shared mutable dict carrying the latest frame's
    width/height so MOVE events report the producer's coordinate space.
    Send failures (e.g. producer just stopped) are swallowed -- a dead
    SHM connection is detected in the main loop via is_producer_alive.
    """
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
            # OpenCV packs the signed wheel delta into the upper 16 bits
            # of `flags`. Windows convention is 120 units per detent;
            # ovstream / StreamSDK report `scroll_y` in detents.
            # This packing is Windows-only; on Linux/X11 OpenCV's GUI
            # backend doesn't deliver MOUSEWHEEL events, so this branch
            # silently no-ops there.
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
    """Forward a single cv2.waitKey code as a key-DOWN event.

    cv2.waitKey doesn't surface key release, only press, so we never
    emit KeyState.UP. That's enough for the existing producer-side
    "any key resets" handlers; producers that need press/release pairs
    will want a different input source than an OpenCV viewer.
    """
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
