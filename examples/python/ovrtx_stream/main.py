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
# requires-python = ">=3.10,<3.14"
# dependencies = [
#     "ovstream",
#     "ovrtx",
#     "warp-lang",
# ]
# ///

"""Stream the stock ovrtx minimal-example scene via OVSTREAM.

ovrtx renders the Robot-OVRTX scene directly into CUDA memory; a short
Warp kernel swizzles RGBA8 -> BGRA8 in a long-lived stream buffer; and
OVSTREAM wraps that buffer as a VideoFrame each iteration.

By default the camera orbits the robot slowly (Y-axis rotation at about
a full turn every 20 s). Any transport with a reverse input channel
(WebRTC, native, SHM) lets a connected client take control:

    left-click drag  -> rotate the camera around the robot
    mouse wheel      -> zoom in / out
    any key press    -> return to auto-orbit

RTSP has no input channel, so RTSP clients just see the auto-orbit.

Requires:
    pip install ovstream ovrtx warp-lang

Usage:
    python main.py                      (no args: WebRTC on default signal port)
    python main.py webrtc               (WebRTC with camera control)
    python main.py rtsp                 (RTSP-only, orbit view)
    python main.py shm:robot            (SHM with stream name 'robot')
    python main.py rtsp:9000 webrtc shm (three transports simultaneously)

View RTSP with:  ffplay rtsp://localhost:8554/stream
View SHM with:   python examples/python/local_stream/main_viewer.py <stream-name>

Note: The first run of this example takes noticeably longer than
subsequent ones because ovrtx compiles and caches its shaders on first
use. This is an ovrtx startup cost, not a streaming-pipeline cost.
"""

import math
import sys

import numpy as np

try:
    import ovrtx
except ImportError:
    print("This example requires ovrtx: pip install ovrtx", file=sys.stderr)
    sys.exit(1)

try:
    import warp as wp
except ImportError:
    print("This example requires NVIDIA Warp: pip install warp-lang", file=sys.stderr)
    sys.exit(1)

import ovstream
import ovstream_utils

wp.init()


# Stock scene from the ovrtx Python minimal example.
# https://github.com/NVIDIA-Omniverse/ovrtx/blob/main/examples/python/minimal/main.py
USD_URL = "https://omniverse-content-production.s3.us-west-2.amazonaws.com/Samples/Robot-OVRTX/robot-ovrtx.usda"
RENDER_PRODUCT = "/Render/Camera"
CAMERA_PRIM = "/World/Camera"

# Scene is Z-up. The robot stands at roughly (0, 0, 0..1.5); aim the
# camera at a point inside its torso. Values derived from the initial
# camera pose shipped in the USD (translate=(1.44, -4.15, 0.76),
# target approximated at (0, 0, 0.8) so the camera sits slightly below
# the aim-point and looks up a touch).
TARGET = np.array([0.0, 0.0, 0.8], dtype=np.float64)
INITIAL_EYE = np.array([1.44, -4.15, 0.76], dtype=np.float64)

_offset = INITIAL_EYE - TARGET
HORIZ_DIST     = float(np.hypot(_offset[0], _offset[1]))
HEIGHT_OFFSET  = float(_offset[2])
INITIAL_THETA  = float(np.arctan2(_offset[1], _offset[0]))
ORBIT_OMEGA    = 2.0 * math.pi / 20.0   # full rotation every 20 seconds
PITCH_LIMIT    = math.radians(75.0)      # clamp so we never go over the pole
MIN_RADIUS     = 0.5                     # don't let the zoom collapse
GROUND_CLEARANCE = 0.1                   # keep eye.z at least this far above z=0
MOUSE_YAW_GAIN   = 0.005                 # radians per pixel of mouse drag
MOUSE_PITCH_GAIN = 0.005
MOUSE_WHEEL_GAIN = 0.25                  # metres per wheel tick


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


# ---------------------------------------------------------------------------
# Camera pose state + input handling
# ---------------------------------------------------------------------------
# Kept as module-level mutable state so the input callback (invoked on the
# StreamSDK callback thread) and the render loop can share it without
# threading the values through every function.

cam_state = {
    "orbit_enabled":   True,
    "yaw_offset":      0.0,   # added to auto-orbit theta when disabled
    "pitch":           0.0,
    "zoom_delta":      0.0,   # added to HORIZ_DIST
    "last_mouse_x":    None,
    "last_mouse_y":    None,
    "left_button":     False,
}


def reset_user_camera():
    cam_state["orbit_enabled"] = True
    cam_state["yaw_offset"]    = 0.0
    cam_state["pitch"]         = 0.0
    cam_state["zoom_delta"]    = 0.0


def make_input_handler():
    def on_input(event):
        if event.type == ovstream.InputEventType.MOUSE:
            mouse = event.mouse
            if mouse.type == ovstream.MouseEventType.BUTTON:
                if mouse.data == ovstream.MouseButton.LEFT:
                    cam_state["left_button"] = (
                        mouse.button_state == ovstream.KeyState.DOWN)
                    # Reset the last-position anchor on press so the
                    # very first MOVE after a button-down doesn't emit a
                    # huge delta based on wherever the cursor was last.
                    if cam_state["left_button"]:
                        cam_state["last_mouse_x"] = mouse.x
                        cam_state["last_mouse_y"] = mouse.y
            elif mouse.type == ovstream.MouseEventType.MOVE:
                if cam_state["left_button"]:
                    if cam_state["last_mouse_x"] is not None:
                        dx = mouse.x - cam_state["last_mouse_x"]
                        dy = mouse.y - cam_state["last_mouse_y"]
                        # Yaw: drag left -> camera orbits left. Pitch:
                        # drag UP = tilt camera down toward the floor
                        # (turntable convention; inverts the screen-y
                        # delta). Ground-plane clamping happens in
                        # compute_camera_matrix because the floor
                        # constraint depends on the current radius.
                        cam_state["yaw_offset"] -= dx * MOUSE_YAW_GAIN
                        cam_state["pitch"]      += dy * MOUSE_PITCH_GAIN
                        cam_state["pitch"] = max(-PITCH_LIMIT,
                                                 min(PITCH_LIMIT,
                                                     cam_state["pitch"]))
                        cam_state["orbit_enabled"] = False
                    cam_state["last_mouse_x"] = mouse.x
                    cam_state["last_mouse_y"] = mouse.y
            elif mouse.type == ovstream.MouseEventType.WHEEL:
                # mouse.scroll_y is vertical scroll in notches (1.0 = one
                # full notch; positive = away from user).
                cam_state["zoom_delta"] -= mouse.scroll_y * MOUSE_WHEEL_GAIN
                cam_state["orbit_enabled"] = False
        elif event.type == ovstream.InputEventType.KEYBOARD:
            if event.keyboard.key_state == ovstream.KeyState.DOWN:
                # Any key press returns to auto-orbit.
                reset_user_camera()
    return on_input


def compute_camera_matrix(t: float) -> np.ndarray:
    """Build the camera-to-world transform as a (4, 4) row-major float64 matrix.

    Z-up, right-handed; camera looks down its local -Z toward TARGET.
    """
    if cam_state["orbit_enabled"]:
        theta = INITIAL_THETA + t * ORBIT_OMEGA
        phi   = 0.0
        radius = HORIZ_DIST
    else:
        theta  = INITIAL_THETA + cam_state["yaw_offset"]
        phi    = cam_state["pitch"]
        radius = max(MIN_RADIUS, HORIZ_DIST + cam_state["zoom_delta"])

    # Ground-plane clamp: keep eye.z >= GROUND_CLEARANCE so the camera
    # never dips below the floor (the scene uses z=0 as the ground and
    # letting eye.z go negative makes the robot disappear behind the
    # groundplane). Since eye.z = TARGET.z + radius*sin(phi) +
    # HEIGHT_OFFSET, the minimum allowed phi depends on the current
    # radius -- zooming in tightens it, zooming out relaxes it.
    min_sin_phi = (GROUND_CLEARANCE - TARGET[2] - HEIGHT_OFFSET) / radius
    if min_sin_phi > -1.0:
        phi = max(phi, math.asin(min_sin_phi))

    cos_phi = math.cos(phi)
    eye = TARGET + np.array([
        radius * cos_phi * math.cos(theta),
        radius * cos_phi * math.sin(theta),
        radius * math.sin(phi) + HEIGHT_OFFSET,
    ], dtype=np.float64)

    forward = TARGET - eye
    forward /= np.linalg.norm(forward)
    world_up = np.array([0.0, 0.0, 1.0], dtype=np.float64)
    right = np.cross(forward, world_up)
    right /= np.linalg.norm(right)
    up = np.cross(right, forward)

    # USD Gf.Matrix4d is row-major; points transform as P_world = P_local * M.
    # The first three rows are the basis vectors; the fourth row is the
    # translation. Camera looks down local -Z, so the Z basis is -forward.
    M = np.eye(4, dtype=np.float64)
    M[0, :3] = right
    M[1, :3] = up
    M[2, :3] = -forward
    M[3, :3] = eye
    return M


# RGBA8 -> BGRA8 swap. ovrtx emits LdrColor as RGBA8; OVSTREAM wants BGRA8.
@wp.kernel
def swap_rb(buf: wp.array3d(dtype=wp.uint8)):
    x, y = wp.tid()
    r = buf[y, x, 0]
    b = buf[y, x, 2]
    buf[y, x, 0] = b
    buf[y, x, 2] = r


def main():
    specs = [parse_server_spec(a) for a in sys.argv[1:]] if len(sys.argv) > 1 else [
        (ovstream.ServerType.WEBRTC, 0, "WebRTC:49100"),
    ]

    print("Creating ovrtx renderer (first run compiles and caches shaders)...", file=sys.stderr)
    renderer = ovrtx.Renderer()
    print(f"Loading USD scene: {USD_URL}", file=sys.stderr)
    renderer.open_usd(USD_URL)

    # Bind the camera's xform attribute so we can animate it per-frame
    # without editing the USD (same pattern as the ovrtx planet-system
    # example, but for a single prim). Map to CPU each frame -- a 64-byte
    # transform has no benefit from staying on the GPU.
    camera_binding = renderer.bind_attribute(
        prim_paths=[CAMERA_PRIM],
        attribute_name="omni:xform",
        dtype="float64",
        shape=(4, 4),
        prim_mode=ovrtx.PrimMode.MUST_EXIST,
    )

    target_frame_time = 1.0 / 60.0

    # Warm-up step to discover the camera's authored resolution so the
    # server's declared extents match what ovrtx actually produces.
    # (If the camera is authored at 1920x1080, we configure the server
    # for exactly that; starting at a mismatched resolution would make
    # every stream_video reject the frame.)
    with camera_binding.map(device=ovrtx.Device.CPU) as m:
        wp.from_dlpack(m.tensor).numpy().reshape(1, 4, 4)[0] = compute_camera_matrix(0.0)
    products = renderer.step(render_products={RENDER_PRODUCT},
                             delta_time=target_frame_time)
    first_frame = next(iter(next(iter(products.values())).frames))
    with first_frame.render_vars["LdrColor"].map(device=ovrtx.Device.CUDA) as warmup:
        tensor = wp.from_dlpack(warmup.tensor)
        if tensor.ndim != 3 or tensor.shape[2] != 4 or tensor.dtype != wp.uint8:
            raise RuntimeError(
                f"LdrColor has unexpected shape/dtype {tensor.shape} / {tensor.dtype}; "
                f"expected (H, W, 4) uint8. The example assumes RGBA8 LDR output."
            )
        height, width = int(tensor.shape[0]), int(tensor.shape[1])
    print(f"Render output: {width}x{height} RGBA8", file=sys.stderr)

    # Long-lived stream buffer. ovstream stages each frame into server-
    # owned memory before stream_video returns, so this buffer is free
    # to mutate on the next iteration immediately; reusing one buffer
    # across frames just saves a per-frame allocation.
    stream_buf = wp.zeros((height, width, 4), dtype=wp.uint8, device="cuda:0")

    # CUDA stream + event for the producer-side sync hint passed to
    # ovstream. The event is recorded on Warp's stream after each
    # swap_rb kernel; ovstream waits on it instead of forcing a
    # host-side wp.synchronize() between every frame.
    draw_stream = wp.get_stream("cuda:0")
    draw_event  = wp.Event(device="cuda:0")

    # ovrtx renders the frames in Warp's CUDA context on cuda:0. StreamSDK
    # needs that exact context to read them: a device ordinal alone is not
    # enough and fails the encode on a multi-GPU host. Pass it to ovstream
    # via cuda_context (paired with cuda_device below).
    stream_cuda_context = int(wp.get_device("cuda:0").context)

    ovstream.initialize()
    try:
        servers = []
        for server_type, detail, label in specs:
            s = ovstream.Server(server_type)
            s.on_connection = lambda c, lbl=label: print(
                f"[{lbl}] Client {'connected' if c else 'disconnected'}")
            # Input works on every transport with a reverse channel
            # (WebRTC, native, SHM, CUDASHM). RTSP has no input path.
            # Enumerated explicitly so a future backend without a
            # reverse channel is not silently opted in.
            if server_type in (ovstream.ServerType.WEBRTC,
                               ovstream.ServerType.NATIVE,
                               ovstream.ServerType.SHM,
                               ovstream.ServerType.CUDASHM):
                s.on_input = make_input_handler()

            # Pin the encoder to cuda:0 (where the frames live) and hand it
            # the producer's CUDA context. The default would be the display
            # GPU on a multi-GPU host; cuda_device without cuda_context fails
            # the encode for a producer that uses its own CUDA context.
            cfg = ovstream.ServerConfig(width=width, height=height,
                                        cuda_device=0,
                                        cuda_context=stream_cuda_context)
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

            s.start(cfg)

            if server_type == ovstream.ServerType.RTSP:
                print(f"[{label}] rtsp://localhost:{detail or 8554}/stream")
            elif server_type == ovstream.ServerType.SHM:
                print(f"[{label}] attach with: python examples/python/local_stream/main_viewer.py "
                      f"{detail or '<see ovstream log for auto name>'}  "
                      "(left-drag orbit, wheel zoom, any key -> resume orbit)")
            elif server_type == ovstream.ServerType.CUDASHM:
                print(f"[{label}] attach with: python examples/python/local_stream/main_cudashm_viewer.py "
                      f"{detail or '<see ovstream log for auto name>'}  "
                      "(left-drag orbit, wheel zoom, any key -> resume orbit)")
            else:
                print(f"[{label}] signal port {detail or 49100}  "
                      "(left-drag orbit, wheel zoom, any key -> resume orbit)")

            servers.append(s)

        print("Press Ctrl+C to stop.")
        frame_count = 0
        loop_start_ns = None

        # ovstream_utils.Loop paces the loop to fps_target and reports
        # the measured per-frame interval via tick.delta_time_seconds,
        # which we pass to renderer.step so animation advances at the
        # actual wall-clock rate rather than a hardcoded 1/fps. Bundled
        # with ovstream but not required to use it.
        try:
            with ovstream_utils.Loop(ovstream_utils.LoopConfig(fps_target=60)) as loop:
                while True:
                    tick = loop.tick()
                    if loop_start_ns is None:
                        loop_start_ns = tick.start_time_ns
                    elapsed_seconds = (tick.start_time_ns - loop_start_ns) / 1e9

                    # Update the camera pose before stepping the renderer.
                    with camera_binding.map(device=ovrtx.Device.CPU) as m:
                        wp.from_dlpack(m.tensor).numpy().reshape(1, 4, 4)[0] = (
                            compute_camera_matrix(elapsed_seconds))

                    # First tick reports delta_time_seconds == 0.0 (no
                    # previous frame to measure against); fall back to
                    # the nominal target so the warm-up frame doesn't
                    # render with dt=0.
                    delta_time = tick.delta_time_seconds or target_frame_time
                    products = renderer.step(
                        render_products={RENDER_PRODUCT},
                        delta_time=delta_time,
                    )

                    # [snippet:compose-ovrtx]
                    # Copy ovrtx's transient LdrColor tensor into the
                    # long-lived stream buffer, then swizzle in place
                    # (ovrtx emits RGBA8 today; ovstream wants BGRA8).
                    for _name, product in products.items():
                        for frame in product.frames:
                            with frame.render_vars["LdrColor"].map(
                                    device=ovrtx.Device.CUDA) as mapping:
                                source = wp.from_dlpack(mapping.tensor)
                                wp.copy(stream_buf, source)
                    wp.launch(swap_rb, dim=(width, height),
                              inputs=[stream_buf], device="cuda:0")
                    draw_stream.record_event(draw_event)

                    # Hand the stream buffer to ovstream. The
                    # `sync` hint lets ovstream chain off Warp's
                    # CUDA stream without a host-side
                    # wp.synchronize() between every frame.
                    video_frame = ovstream.VideoFrame.from_cuda_array(
                        stream_buf,
                        sync=ovstream.CudaSync(
                            stream=draw_stream.cuda_stream,
                            wait_event=draw_event.cuda_event,
                        ),
                    )
                    for s in servers:
                        try:
                            s.stream_video(video_frame)
                        except ovstream.OvstreamError:
                            pass  # e.g. no client connected yet; drop silently
                    # [/snippet:compose-ovrtx]

                    print(f"\rFPS: {tick.stats.fps_current}", end="", flush=True)
                    frame_count = tick.frame_index + 1
        except KeyboardInterrupt:
            pass

        for s in servers:
            try:
                s.stop()
            except ovstream.OvstreamError:
                pass
            s.close()

        camera_binding.unbind()
        print(f"\nDone. Streamed {frame_count} frames.")
    finally:
        ovstream.shutdown()


if __name__ == "__main__":
    main()
