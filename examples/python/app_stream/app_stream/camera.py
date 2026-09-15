# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary
#
# NVIDIA CORPORATION, its affiliates and licensors retain all intellectual
# property and proprietary rights in and to this material, related
# documentation and any modifications thereto. Any use, reproduction,
# disclosure or distribution of this material and related documentation
# without an express license agreement from NVIDIA CORPORATION or
# its affiliates is strictly prohibited.

"""Orbit camera: auto-orbit by default, taken over by mouse drag and wheel."""

from __future__ import annotations

import math

import numpy as np

import ovstream


class OrbitCamera:
    """Turntable camera around a fixed target in a Z-up, right-handed scene.

    `handle(event)` is called on the main thread with input events drained
    from the streaming worker thread. `matrix(t)` returns the camera-to-world
    transform for simulation time `t` as a (4, 4) row-major float64 matrix,
    the layout of a USD `omni:xform`.
    """

    ORBIT_OMEGA = 2.0 * math.pi / 20.0   # one turn every 20 s
    PITCH_LIMIT = math.radians(75.0)
    MIN_RADIUS = 0.5
    GROUND_CLEARANCE = 0.1
    YAW_GAIN = 0.005                     # radians per pixel
    PITCH_GAIN = 0.005
    WHEEL_GAIN = 0.25                    # metres per notch

    def __init__(self, target=(0.0, 0.0, 0.8), initial_eye=(1.44, -4.15, 0.76)):
        self.target = np.array(target, dtype=np.float64)
        offset = np.array(initial_eye, dtype=np.float64) - self.target
        self.horiz_dist = float(np.hypot(offset[0], offset[1]))
        self.height_offset = float(offset[2])
        self.initial_theta = float(np.arctan2(offset[1], offset[0]))
        self.reset()
        self._last = None
        self._dragging = False

    @property
    def dragging(self) -> bool:
        return self._dragging

    def reset(self) -> None:
        self.orbit_enabled = True
        self.yaw_offset = 0.0
        self.pitch = 0.0
        self.zoom_delta = 0.0

    def handle(self, event: ovstream.InputEvent) -> None:
        if event.type == ovstream.InputEventType.MOUSE:
            m = event.mouse
            if m.type == ovstream.MouseEventType.BUTTON and m.data == ovstream.MouseButton.LEFT:
                self._dragging = m.button_state == ovstream.KeyState.DOWN
                self._last = (m.x, m.y) if self._dragging else None
            elif m.type == ovstream.MouseEventType.MOVE and self._dragging:
                if self._last is not None:
                    dx, dy = m.x - self._last[0], m.y - self._last[1]
                    self.yaw_offset -= dx * self.YAW_GAIN
                    self.pitch = max(-self.PITCH_LIMIT, min(self.PITCH_LIMIT, self.pitch + dy * self.PITCH_GAIN))
                    self.orbit_enabled = False
                self._last = (m.x, m.y)
            elif m.type == ovstream.MouseEventType.WHEEL:
                self.zoom_delta -= m.scroll_y * self.WHEEL_GAIN
                self.orbit_enabled = False
        elif event.type == ovstream.InputEventType.KEYBOARD:
            if event.keyboard.key_state == ovstream.KeyState.DOWN:
                self.reset()

    def matrix(self, t: float) -> np.ndarray:
        if self.orbit_enabled:
            theta, phi, radius = self.initial_theta + t * self.ORBIT_OMEGA, 0.0, self.horiz_dist
        else:
            theta = self.initial_theta + self.yaw_offset
            phi = self.pitch
            radius = max(self.MIN_RADIUS, self.horiz_dist + self.zoom_delta)

        # Keep the eye above the ground plane; the limit depends on the radius.
        min_sin_phi = (self.GROUND_CLEARANCE - self.target[2] - self.height_offset) / radius
        if min_sin_phi > -1.0:
            phi = max(phi, math.asin(min_sin_phi))

        cos_phi = math.cos(phi)
        eye = self.target + np.array([
            radius * cos_phi * math.cos(theta),
            radius * cos_phi * math.sin(theta),
            radius * math.sin(phi) + self.height_offset,
        ])
        forward = self.target - eye
        forward /= np.linalg.norm(forward)
        right = np.cross(forward, np.array([0.0, 0.0, 1.0]))
        right /= np.linalg.norm(right)
        up = np.cross(right, forward)

        m = np.eye(4, dtype=np.float64)
        m[0, :3] = right
        m[1, :3] = up
        m[2, :3] = -forward
        m[3, :3] = eye
        return m
