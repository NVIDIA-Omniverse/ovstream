# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary
#
# NVIDIA CORPORATION, its affiliates and licensors retain all intellectual
# property and proprietary rights in and to this material, related
# documentation and any modifications thereto. Any use, reproduction,
# disclosure or distribution of this material and related documentation
# without an express license agreement from NVIDIA CORPORATION or
# its affiliates is strictly prohibited.

"""Startup capability report: what is installed, which GPU, what the run will try."""

from __future__ import annotations

import importlib
import json
import os
import platform
import subprocess
import sys
from typing import Any, Dict

from .config import AppConfig

LIBRARIES = ("ovstream", "ovrtx", "ovstage", "ovui", "warp-lang", "numpy")
ENV_KEYS = ("CUDA_DEVICE_MAX_CONNECTIONS", "OMNIUI_HEADLESS", "OMNIUI_BACKEND", "CUDA_PATH", "DISPLAY")


def _version(dist: str) -> str:
    try:
        return importlib.metadata.version(dist)
    except Exception:
        return "not installed"


def _gpu() -> Dict[str, Any]:
    info: Dict[str, Any] = {}
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,driver_version,display_active,compute_cap", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=10)
        rows = [r.strip() for r in out.stdout.splitlines() if r.strip()]
        if rows:
            name, driver, display, cc = [c.strip() for c in rows[0].split(",")]
            info.update(name=name, driver=driver, display_active=display, compute_capability=cc)
    except Exception as e:  # nvidia-smi missing or unusable
        info["nvidia_smi"] = f"unavailable ({type(e).__name__})"
    return info


def probe(cfg: AppConfig) -> Dict[str, Any]:
    import importlib.metadata  # noqa: F401  (keeps the metadata module loaded for _version)

    report: Dict[str, Any] = {
        "python": sys.version.split()[0],
        "platform": f"{platform.system()} {platform.release()} {platform.machine()}",
        "libraries": {d: _version(d) for d in LIBRARIES},
        "gpu": _gpu(),
        "env": {k: os.environ.get(k) for k in ENV_KEYS if os.environ.get(k) is not None},
        "run": {
            "scene": cfg.scene,
            "servers": [s.label for s in cfg.servers],
            "output_hz": cfg.output_hz,
            "frame_generation": cfg.fg,
            "fg_interpolated_requested": cfg.fg_interpolated,
            "size": list(cfg.size),
            "ui": cfg.ui,
        },
        "notes": [],
    }
    cc = report["gpu"].get("compute_capability")
    try:
        major, minor = (int(part) for part in str(cc).split(".")[:2])
    except (TypeError, ValueError):
        major = minor = None
    if major is not None and (major < 8 or (major == 8 and minor < 9)):
        report["notes"].append("frame generation needs an Ada (compute 8.9) or newer GPU")
    if report["gpu"].get("display_active", "").lower() == "disabled":
        report["notes"].append("nvidia-smi reports no active display on this GPU; frame generation is "
                               "verified by frame count at runtime, not assumed")
    if report["libraries"]["ovui"] == "not installed" and cfg.ui:
        report["notes"].append("ovui is not installed; run with --no-ui or install the ui profile")
    return report


def format_report(report: Dict[str, Any]) -> str:
    lines = ["app_stream capability report",
             f"  python {report['python']} on {report['platform']}"]
    lines += [f"  {name:10s} {ver}" for name, ver in report["libraries"].items()]
    gpu = report["gpu"]
    if gpu.get("name"):
        lines.append(f"  gpu        {gpu['name']} driver {gpu['driver']} cc {gpu['compute_capability']} "
                     f"display_active {gpu['display_active']}")
    else:
        lines.append(f"  gpu        {gpu.get('nvidia_smi', 'unknown')}")
    for k, v in report["env"].items():
        lines.append(f"  env        {k}={v}")
    run = report["run"]
    lines.append(f"  run        {run['size'][0]}x{run['size'][1]} @ {run['output_hz']} Hz, "
                 f"FG {run['frame_generation']} (requested count {run['fg_interpolated_requested']}), "
                 f"ui {run['ui']}, servers {', '.join(run['servers'])}")
    for n in report["notes"]:
        lines.append(f"  note       {n}")
    return "\n".join(lines)


def write_json(report: Dict[str, Any], path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=1)
