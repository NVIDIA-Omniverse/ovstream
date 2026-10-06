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
#     "ovstream>=0.5.5",
#     "ovrtx>=0.5,<0.7",
#     "ovstage>=0.2",
#     "numpy",
# ]
# ///

"""Stream the stock ovrtx minimal-example scene via OVSTREAM.

ovstage owns the scene, ovrtx renders it, and OVSTREAM streams it.
stream_scene.usda wraps the stock robot scene and adds a RenderProduct
whose colour output is swizzled to BGRA8 by an ovrtx Sensor Processing
Graph node, so the mapped CUDA buffer is handed to OVSTREAM as-is: no
copy, no kernel, no Warp.

By default the camera orbits the robot slowly (Y-axis rotation at about
a full turn every 20 s). Any transport with a reverse input channel
(WebRTC, native, SHM) lets a connected client take control:

    left-click drag  -> rotate the camera around the robot
    mouse wheel      -> zoom in / out
    any key press    -> return to auto-orbit

RTSP has no input channel, so RTSP clients just see the auto-orbit.

Requires:
    pip install ovstream ovrtx ovstage numpy

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
from pathlib import Path

import numpy as np

try:
    import ovrtx
    import ovstage
except ImportError:
    print("This example requires ovrtx and ovstage: pip install ovrtx ovstage", file=sys.stderr)
    sys.exit(1)

import ovstream
import ovstream_utils


# stream_scene.usda sublayers the stock ovrtx robot scene and adds the
# RenderProduct /Render/Stream. That product renders the scene camera and
# publishes two outputs: LdrColor, the renderer's RGBA8 colour output, and
# BgraColor, the same image with red and blue exchanged by the SPG Swizzle
# node, which is the BGRA8 layout OVSTREAM expects.
SCENE_PATH = str(Path(__file__).with_name("stream_scene.usda"))
CAMERA_PRIM = "/World/Camera"
RENDER_PRODUCT = "/Render/Stream"
BGRA_VAR = RENDER_PRODUCT + "/BgraColor"

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


class CameraWriter:
    """Publishes the camera transform to ovstage once per frame.

    One query is created up front and reused; each write is published under
    a fresh ordinal, and the renderer is then stepped at that ordinal.
    """

    def __init__(self, stage, prim_path: str):
        self._stage = stage
        self._paths = ovstage.PathDictionary(stage)
        self._path_list = self._paths.create_path_list_from_strings([prim_path])
        self._query = stage.query_from_path_list(self._path_list)
        self._attribute = self._paths.intern_token("omni:xform")
        self._dtype = ovstage.numpy_to_dldatatype(np.dtype(np.float64), lanes=16)

    def write(self, matrix: np.ndarray, ordinal: int) -> None:
        tensor = ovstage.make_dltensor(matrix.reshape(1, 4, 4), dtype=self._dtype, shape=[1], ndim=1)
        self._stage.write_attribute(self._query, self._attribute, ordinal=ordinal,
                                    tensors=tensor, is_array=False).wait()
        self._stage.advance_write_floor(ordinal, ovstage.Scope.ALL).wait()

    def release(self) -> None:
        self._query.release().wait()
        self._paths.destroy_path_list(self._path_list)
        self._paths.destroy()


def main():
    specs = [parse_server_spec(a) for a in sys.argv[1:]] if len(sys.argv) > 1 else [
        (ovstream.ServerType.WEBRTC, 0, "WebRTC:49100"),
    ]

    # Everything created below is released in the finally, whether or not the scene
    # load and warm-up succeed.
    ovstream.initialize()
    renderer = stage = camera = None
    try:
        # The stage is created before the renderer and the renderer is then
        # attached to it; the libraries must be initialized in this order.
        stage = ovstage.Stage("ovstream.example.ovrtx_stream")
        print("Creating ovrtx renderer (first run compiles and caches shaders)...", file=sys.stderr)
        renderer = ovrtx.Renderer()
        renderer.attach_ovstage(stage)

        print(f"Loading USD scene: {SCENE_PATH}", file=sys.stderr)
        ordinal = 1
        ovstage.population.open_usd(stage, SCENE_PATH, ordinal=ordinal)
        stage.advance_write_floor(ordinal, ovstage.Scope.ALL).wait()

        camera = CameraWriter(stage, CAMERA_PRIM)
        target_frame_time = 1.0 / 60.0

        # Warm-up steps: the RenderProduct is picked up by the renderer one
        # step after the stage is populated, so step until it appears and use
        # it to discover the output resolution. The server's declared
        # extents must match what ovrtx produces; a mismatch would make every
        # stream_video reject the frame.
        ordinal += 1
        camera.write(compute_camera_matrix(0.0), ordinal)
        for _ in range(10):
            products = renderer.step(render_products={RENDER_PRODUCT}, delta_time=target_frame_time, ordinal=ordinal)
            if RENDER_PRODUCT in list(products.keys()):
                break
        else:
            raise RuntimeError(f"{RENDER_PRODUCT} did not become available")
        mapping = products[RENDER_PRODUCT].frames[0].render_vars[BGRA_VAR].map(device=ovrtx.Device.CUDA)
        shape = tuple(mapping.shape)
        mapping.unmap()
        del mapping, products
        if len(shape) != 3 or shape[2] != 4:
            raise RuntimeError(f"BgraColor has unexpected shape {shape}; expected (H, W, 4) uint8")
        height, width = int(shape[0]), int(shape[1])
        print(f"Render output: {width}x{height} BGRA8", file=sys.stderr)

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

            # The RenderProduct in stream_scene.usda renders on CUDA-visible
            # GPU 0 (deviceIds = [0]) and its mapped render var is readable
            # from that device's primary context, so cuda_device=0 is enough
            # here. A producer that renders in its own context must also
            # pass cuda_context (see warp_stream).
            cfg = ovstream.ServerConfig(width=width, height=height, cuda_device=0)
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

                    # Publish this frame's camera pose, then render at that ordinal.
                    ordinal += 1
                    camera.write(compute_camera_matrix(elapsed_seconds), ordinal)

                    # First tick reports delta_time_seconds == 0.0 (no
                    # previous frame to measure against); fall back to
                    # the nominal target so the warm-up frame doesn't
                    # render with dt=0.
                    delta_time = tick.delta_time_seconds or target_frame_time
                    products = renderer.step(
                        render_products={RENDER_PRODUCT},
                        delta_time=delta_time,
                        ordinal=ordinal,
                    )

                    # [snippet:compose-ovrtx]
                    # Map the BGRA8 output to CUDA and hand the mapped
                    # buffer straight to ovstream. The mapping's
                    # producer-done event is passed as the sync hint so
                    # ovstream waits for the render on the GPU rather
                    # than the host; ovstream stages the frame before
                    # stream_video returns, so the mapping can be
                    # released immediately afterwards.
                    for frame in products[RENDER_PRODUCT].frames:
                        mapping = frame.render_vars[BGRA_VAR].map(device=ovrtx.Device.CUDA)
                        video_frame = ovstream.VideoFrame(
                            buffer=int(mapping.data),
                            width=width,
                            height=height,
                            pitch_bytes=width * 4,
                            sync=ovstream.CudaSync(wait_event=int(mapping.wait_event or 0)),
                        )
                        for s in servers:
                            s.stream_video(video_frame)
                        mapping.unmap()
                        del mapping
                    # [/snippet:compose-ovrtx]
                    del products

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

        print(f"\nDone. Streamed {frame_count} frames.")
    finally:
        if camera is not None:
            camera.release()
        if renderer is not None and stage is not None:
            renderer.detach_ovstage()
        if stage is not None:
            stage.destroy()
        if renderer is not None:
            renderer.destroy()
        ovstream.shutdown()


if __name__ == "__main__":
    main()
