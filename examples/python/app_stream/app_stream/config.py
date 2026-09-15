# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary
#
# NVIDIA CORPORATION, its affiliates and licensors retain all intellectual
# property and proprietary rights in and to this material, related
# documentation and any modifications thereto. Any use, reproduction,
# disclosure or distribution of this material and related documentation
# without an express license agreement from NVIDIA CORPORATION or
# its affiliates is strictly prohibited.

"""Command-line options -> AppConfig. Pure Python, no library imports."""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

DEFAULT_SCENE = (
    "https://omniverse-content-production.s3.us-west-2.amazonaws.com/Samples/Robot-OVRTX/robot-ovrtx.usda"
)
DEFAULT_CAMERA = "/World/Camera"

# Transport names accepted on the command line, in `<protocol>[:port-or-name]`
# form, matching the other ovstream examples.
PROTOCOLS = ("webrtc", "native", "rtsp", "shm", "cudashm")
DEFAULT_PORTS = {"webrtc": 49100, "native": 49100, "rtsp": 8554}


@dataclass
class ServerSpec:
    protocol: str            # one of PROTOCOLS
    port: int = 0            # network protocols; 0 = backend default
    stream_name: str = ""    # shm / cudashm; "" = auto

    @property
    def label(self) -> str:
        if self.protocol in ("shm", "cudashm"):
            return f"{self.protocol.upper()}:{self.stream_name or '<auto>'}"
        return f"{self.protocol.upper()}:{self.port or DEFAULT_PORTS[self.protocol]}"


def parse_server_spec(spec: str) -> ServerSpec:
    protocol, _, detail = spec.partition(":")
    protocol = protocol.lower()
    if protocol not in PROTOCOLS:
        raise argparse.ArgumentTypeError(f"unknown protocol {protocol!r}; expected one of {', '.join(PROTOCOLS)}")
    if protocol in ("shm", "cudashm"):
        return ServerSpec(protocol, stream_name=detail)
    return ServerSpec(protocol, port=int(detail) if detail else 0)


@dataclass
class AppConfig:
    scene: str = DEFAULT_SCENE
    camera: str = DEFAULT_CAMERA
    servers: List[ServerSpec] = field(default_factory=lambda: [ServerSpec("webrtc")])
    output_hz: int = 60                    # delivered frame rate
    fg: str = "auto"                       # auto | on | off
    fg_interpolated: int = 0               # requested interpolated frames per simulated frame; 0 = renderer picks
    size: Tuple[int, int] = (1920, 1080)   # stream / render resolution; the maximum when resizable
    resizable: bool = False                # honour client resize requests (WebRTC/native per-frame dimensions)
    marker: bool = False                   # draw the frame-identity pixel marker (see marker.py)
    zero_copy: bool = False                # experimental: stream from the mapped ovrtx output, no device copy
    ui: bool = True                        # ovui control panel composited into the stream
    report_only: bool = False
    report_json: Optional[str] = None
    duration_s: float = 0.0                # 0 = run until interrupted
    cuda_device: int = 0
    publish_thread: bool = False           # publish from a worker thread so transport waits never stall the loop
    stats_json: Optional[str] = None       # write final statistics here on exit
    renderer_log: Optional[str] = None     # write the renderer's own log (info level) to this file

    @property
    def fg_requested(self) -> bool:
        return self.fg in ("auto", "on")


def parse_args(argv=None) -> AppConfig:
    ap = argparse.ArgumentParser(
        prog="app_stream",
        description="Reference implementation: ovrtx + ovstage + ovui + ovstream, with DLSS Frame Generation.",
    )
    ap.add_argument("servers", nargs="*", type=parse_server_spec,
                    help="transports as <protocol>[:port-or-name]; default webrtc")
    ap.add_argument("--scene", default=DEFAULT_SCENE, help="USD scene path or URL")
    ap.add_argument("--camera", default=DEFAULT_CAMERA, help="camera prim path in the scene")
    ap.add_argument("--output-hz", type=int, default=60, help="delivered frame rate (default 60)")
    ap.add_argument("--fg", choices=("auto", "on", "off"), default="auto",
                    help="DLSS Frame Generation: auto (request, fall back), on, off")
    ap.add_argument("--fg-interpolated", type=int, default=0,
                    help="interpolated frames per simulated frame to request; 0 lets the renderer choose")
    ap.add_argument("--size", default="1920x1080", help="render/stream resolution WxH")
    ap.add_argument("--resizable", action="store_true",
                    help="honour browser resize requests; --size becomes the maximum (WebRTC/native)")
    ap.add_argument("--no-stream", action="store_true",
                    help="run the render (and UI) loop without any server; for renderer-only measurements")
    ap.add_argument("--zero-copy", action="store_true",
                    help="experimental: convert and composite in place on the mapped ovrtx output and stream "
                         "from it, holding the mapping until stream_video returns (no device copy)")
    ap.add_argument("--marker", action="store_true",
                    help="draw the frame-identity pixel marker in the bottom-left corner (for measurement)")
    ap.add_argument("--no-ui", action="store_true", help="no ovui control panel; ovui is not imported")
    ap.add_argument("--report", action="store_true", help="print the capability report and exit")
    ap.add_argument("--report-json", default=None, help="also write the capability report to this file")
    ap.add_argument("--duration", type=float, default=0.0, help="stop after this many seconds (0 = run until Ctrl+C)")
    ap.add_argument("--cuda-device", type=int, default=0, help="GPU ordinal for rendering and encoding")
    ap.add_argument("--publish-thread", action="store_true",
                    help="call stream_video from a worker thread; the transport wait for the GPU then overlaps the next tick")
    ap.add_argument("--stats-json", default=None, help="write final statistics to this file on exit")
    ap.add_argument("--renderer-log", default=None,
                    help="write the renderer's log at info level to this file (why frame generation did or did not engage)")
    ns = ap.parse_args(argv)
    if ns.zero_copy and not ns.no_ui:
        ap.error("--zero-copy works in place on the renderer's output and cannot carry the UI; use it with --no-ui")
    if ns.zero_copy and ns.resizable:
        ap.error("--zero-copy cannot be combined with --resizable: a resizable stream may read the buffer "
                 "after stream_video returns, and the mapping is released at that point")

    w, _, h = ns.size.lower().partition("x")
    cfg = AppConfig(
        scene=ns.scene,
        camera=ns.camera,
        servers=[] if ns.no_stream else (ns.servers or [ServerSpec("webrtc")]),
        output_hz=ns.output_hz,
        fg=ns.fg,
        fg_interpolated=ns.fg_interpolated,
        size=(int(w), int(h)),
        resizable=ns.resizable,
        marker=ns.marker,
        zero_copy=ns.zero_copy,
        ui=not ns.no_ui,
        report_only=ns.report,
        report_json=ns.report_json,
        duration_s=ns.duration,
        cuda_device=ns.cuda_device,
        publish_thread=ns.publish_thread,
        stats_json=ns.stats_json,
        renderer_log=ns.renderer_log,
    )
    return cfg
