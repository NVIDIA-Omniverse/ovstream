# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary
#
# NVIDIA CORPORATION, its affiliates and licensors retain all intellectual
# property and proprietary rights in and to this material, related
# documentation and any modifications thereto. Any use, reproduction,
# disclosure or distribution of this material and related documentation
# without an express license agreement from NVIDIA CORPORATION or
# its affiliates is strictly prohibited.

"""ovstream servers: creation, callbacks, and publishing with classified failures."""

from __future__ import annotations

import sys
import threading
import time
from typing import Callable, List, Optional, Tuple

import ovstream
from ovstream_utils.messaging import AppMessenger

from .config import AppConfig, ServerSpec
from .input_bridge import InputBridge
from .pacing import Stats

INPUT_CAPABLE = {"webrtc", "native", "shm", "cudashm"}
MESSAGE_CAPABLE = {"webrtc", "native"}     # control channel: text, resize requests, cursor, app messages


SET_EVENT = "app_stream:set"       # inbound: {"fg": bool, "camera_reset": true, "size": [w, h]}
STATE_EVENT = "app_stream:state"   # outbound, a few times a second


class StreamLayer:
    """One ovstream Server per requested transport, all fed the same frames."""

    def __init__(self, cfg: AppConfig, bridge: InputBridge, stats: Stats, cuda_context: int):
        self.cfg = cfg
        self.bridge = bridge
        self.stats = stats
        self.cuda_context = cuda_context
        self.servers: List[ovstream.Server] = []
        self.labels: List[str] = []
        self.clients = 0
        self.connected: dict = {}          # label -> a client is attached
        self._thread: Optional[threading.Thread] = None
        self._mailbox: Optional[Tuple[ovstream.VideoFrame, Callable[[], None]]] = None
        self._cv = threading.Condition()
        self._stop = False
        self.messengers: List[AppMessenger] = []
        self._cursor = "Arrow"
        self._reported: set = set()
        ovstream.initialize()

    # [snippet:register-control-channel]
    def start(self, width: int, height: int) -> None:
        for spec in self.cfg.servers:
            server = ovstream.Server(getattr(ovstream.ServerType, spec.protocol.upper()))
            # Callbacks are registered before start() so no early event is lost.
            server.on_connection = self._on_connection(spec.label)
            if spec.protocol in INPUT_CAPABLE:
                server.on_input = self.bridge.on_input
            if spec.protocol in MESSAGE_CAPABLE:
                server.on_unicode = self.bridge.on_unicode
                if self.cfg.resizable:
                    server.on_resize_request = self.bridge.on_resize_request
                messenger = AppMessenger(server)
                messenger.on(SET_EVENT, self.bridge.on_set_command)
                self.messengers.append(messenger)
            server.start(self._server_config(spec, width, height))
            self.servers.append(server)
            self.labels.append(spec.label)
            print(f"[{spec.label}] {self._connect_hint(spec)}")
    # [/snippet:register-control-channel]

    def _server_config(self, spec: ServerSpec, width: int, height: int) -> ovstream.ServerConfig:
        cfg = ovstream.ServerConfig(width=width, height=height, target_fps=self.cfg.output_hz,
                                    cuda_device=self.cfg.cuda_device, cuda_context=self.cuda_context)
        if spec.protocol == "rtsp":
            if spec.port:
                cfg.stream_port = spec.port
        elif spec.protocol == "shm":
            if spec.stream_name:
                cfg.shm_stream_name = spec.stream_name
        elif spec.protocol == "cudashm":
            if spec.stream_name:
                cfg.cudashm_stream_name = spec.stream_name
        else:
            if spec.port:
                cfg.webrtc_signal_port = spec.port
            cfg.webrtc_resizable = self.cfg.resizable
        return cfg

    # -- control channel ------------------------------------------------
    # [snippet:cursor-and-state]
    def set_cursor(self, name: str) -> None:
        """Forward the cursor shape to WebRTC/native clients when it changes."""
        if name == self._cursor:
            return
        self._cursor = name
        for server, label in zip(self.servers, self.labels):
            if label.split(":")[0].lower() in MESSAGE_CAPABLE:
                try:
                    server.set_cursor(name)
                except ovstream.OvstreamError:
                    pass

    def send_state(self, state: dict) -> None:
        """Broadcast the application state to browser clients (`app_stream:state`)."""
        for messenger in self.messengers:
            try:
                messenger.send(STATE_EVENT, state)
            except ovstream.OvstreamError:
                pass
    # [/snippet:cursor-and-state]

    @staticmethod
    def _connect_hint(spec: ServerSpec) -> str:
        if spec.protocol == "rtsp":
            return f"rtsp://localhost:{spec.port or 8554}/stream"
        if spec.protocol in ("shm", "cudashm"):
            return f"attach an ovstream {spec.protocol.upper()} client to {spec.stream_name or '<see log for auto name>'}"
        return f"signal port {spec.port or 49100}; open examples/webrtc_client/index.html"

    def _on_connection(self, label: str):
        def cb(connected: bool) -> None:
            self.clients = max(self.clients + (1 if connected else -1), 0)
            self.connected[label] = connected
            print(f"[{label}] client {'connected' if connected else 'disconnected'} ({self.clients} total)",
                  file=sys.stderr)
        return cb

    def start_publish_thread(self) -> None:
        """Publish from a worker thread.

        The transports wait for the producer's GPU work inside stream_video (the SDK's
        CudaSync contract); on the worker that wait overlaps the next tick instead of
        stalling it. A one-frame mailbox keeps the newest frame; a frame superseded before
        it was sent is counted as `skip:superseded` and released at once. Every frame's
        `done` callback runs after all servers have returned, which is what hands its
        ring slot back to the producer.
        """
        self._thread = threading.Thread(target=self._publish_loop, name="app_stream-publish", daemon=True)
        self._thread.start()

    def _publish_loop(self) -> None:
        while True:
            with self._cv:
                while self._mailbox is None and not self._stop:
                    self._cv.wait()
                if self._stop:
                    return
                (frame, done), self._mailbox = self._mailbox, None
            self._publish_now(frame)
            done()

    def publish(self, frame: ovstream.VideoFrame, done: Callable[[], None]) -> None:
        """Submit one frame to every server, directly or through the publish thread.
        `done` runs once no server can still be reading the frame."""
        if self._thread is None:
            self._publish_now(frame)
            done()
            return
        superseded = None
        with self._cv:
            if self._mailbox is not None:
                self.stats.count("skip:superseded")
                superseded = self._mailbox[1]
            self._mailbox = (frame, done)
            self._cv.notify()
        if superseded is not None:
            superseded()

    def _publish_now(self, frame: ovstream.VideoFrame) -> None:
        """Failures are counted by class, never raised."""
        t0 = time.perf_counter()
        for server in self.servers:
            try:
                server.stream_video(frame)
                self.stats.count("submit")
            except ovstream.OvstreamError as e:
                # Expected while no client is attached or when the encoder applies backpressure;
                # each distinct message is printed once so a persistent failure is visible.
                self.stats.count(f"skip:{e.status.name.lower()}" if getattr(e, "status", None) else "skip")
                if str(e) not in self._reported:
                    self._reported.add(str(e))
                    print(f"\nstream_video: {e}", file=sys.stderr)
        self.stats.timing("stream_ms", (time.perf_counter() - t0) * 1000.0)

    def close(self) -> None:
        for messenger in self.messengers:
            messenger.close()
        self.messengers = []
        if self._thread is not None:
            with self._cv:
                self._stop = True
                self._cv.notify()
            # A server must not be destroyed while a stream_video call is in progress, so
            # wait for the worker however long its current call takes.
            while self._thread.is_alive():
                self._thread.join(timeout=5)
                if self._thread.is_alive():
                    print("waiting for the publish thread to finish its stream_video call...", file=sys.stderr)
            self._thread = None
            if self._mailbox is not None:
                self._mailbox[1]()
                self._mailbox = None
        for server in self.servers:
            server.on_input = None
            server.on_connection = None
        for server in self.servers:
            try:
                server.stop()
            except ovstream.OvstreamError:
                pass
            server.close()
        self.servers = []
        ovstream.shutdown()
