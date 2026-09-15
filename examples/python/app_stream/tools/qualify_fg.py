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

"""Frame-generation qualification: run app_stream over a profile matrix and record the evidence.

Each profile runs `main.py --no-ui` for a fixed duration on a local SHM stream
(no client needed), then the statistics the app wrote on exit are collected
together with the capability report into one JSON document and a Markdown
summary. A profile that requests frame generation "passes" only when the app
observed more than one frame per step; on a configuration where generation
cannot engage the row says so instead of being skipped silently.

    python tools/qualify_fg.py --out qualification.json --duration 20
    python tools/qualify_fg.py --require-fg     # exit 1 if a requesting profile did not engage
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
MAIN = HERE.parent / "main.py"

PROFILES = [
    # name,            fg,     output_hz, notes
    ("fg-off-60",      "off",  60,  "60 Hz base render, 60 Hz delivered"),
    ("fg-on-60",       "auto", 60,  "30 Hz base render + 1 interpolated frame, 60 Hz delivered (primary profile)"),
    ("fg-off-120",     "off",  120, "120 Hz base render, 120 Hz delivered"),
    ("fg-on-120",      "auto", 120, "60 Hz base render + 1 interpolated frame, 120 Hz delivered (high-rate profile)"),
]


GPU_FIELDS = ("clocks.sm", "temperature.gpu", "power.draw", "clocks_throttle_reasons.sw_power_cap",
              "clocks_throttle_reasons.hw_thermal_slowdown", "clocks_throttle_reasons.sw_thermal_slowdown")


class GpuSampler:
    """Samples nvidia-smi while a profile runs. A power- or thermally-capped GPU slows down
    within a sustained run; the clock range and throttle reasons seen make that visible."""

    def __init__(self, period_s: float = 2.0):
        self.period_s = period_s
        self.samples: list = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _query(self) -> dict:
        try:
            out = subprocess.run(["nvidia-smi", f"--query-gpu={','.join(GPU_FIELDS)}", "--format=csv,noheader"],
                                 capture_output=True, text=True, timeout=10).stdout.strip().splitlines()
        except Exception as e:
            return {"error": type(e).__name__}
        return dict(zip(GPU_FIELDS, [c.strip() for c in out[0].split(",")])) if out else {}

    def _run(self) -> None:
        while not self._stop.is_set():
            self.samples.append(self._query())
            self._stop.wait(self.period_s)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self._thread.join()

    def summary(self) -> dict:
        clocks = []
        reasons = set()
        for smp in self.samples:
            try:
                clocks.append(int(smp["clocks.sm"].split()[0]))
            except (KeyError, ValueError):
                continue
            if "Active" in smp.get("clocks_throttle_reasons.sw_power_cap", ""):
                reasons.add("power cap")
            if "Active" in smp.get("clocks_throttle_reasons.hw_thermal_slowdown", "") +                            smp.get("clocks_throttle_reasons.sw_thermal_slowdown", ""):
                reasons.add("thermal")
        return {"samples": len(self.samples), "sm_mhz_min": min(clocks) if clocks else None,
                "sm_mhz_max": max(clocks) if clocks else None, "throttle": sorted(reasons) or ["none"]}


def supports_frame_generation(gpu_info: dict) -> bool:
    """True when the GPU's compute capability is Ada (8.9) or newer; unknown counts as supported
    so a missing nvidia-smi never hides a real non-engagement."""
    try:
        major, minor = (int(v) for v in str(gpu_info.get("compute_capability", "")).split(".")[:2])
    except (TypeError, ValueError):
        return True
    return (major, minor) >= (8, 9)


def run_profile(python: str, name: str, fg: str, hz: int, duration: float, size: str, workdir: Path) -> dict:
    stats = workdir / f"{name}.stats.json"
    report = workdir / f"{name}.report.json"
    renderer_log = workdir / f"{name}.ovrtx.log"
    cmd = [python, str(MAIN), "--no-ui", "--fg", fg, "--output-hz", str(hz), "--size", size,
           "--duration", str(duration), "--stats-json", str(stats), "--report-json", str(report),
           "--renderer-log", str(renderer_log), f"shm:qualify_{name}"]
    t0 = time.time()
    with GpuSampler() as gpu:
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=duration + 600.0)
        except subprocess.TimeoutExpired as e:
            proc = subprocess.CompletedProcess(cmd, 124, e.stdout or "", (e.stderr or "") + "\nprofile timed out")
    stderr_lines = proc.stderr.strip().splitlines()
    if renderer_log.exists():
        stderr_lines += renderer_log.read_text(encoding="utf-8", errors="replace").splitlines()
    row = {"profile": name, "fg": fg, "output_hz": hz, "wall_s": round(time.time() - t0, 1),
           "exit_code": proc.returncode, "stderr_tail": stderr_lines[-3:],
           # Renderer log lines about generation, so a run where it did not engage says why.
           "fg_log": [ln for ln in stderr_lines if re.search(r"dlss|frame gen|ngx|interpolat", ln, re.I)][-20:],
           "gpu": gpu.summary()}
    if stats.exists():
        row["stats"] = json.loads(stats.read_text(encoding="utf-8"))
    if report.exists():
        row["report"] = json.loads(report.read_text(encoding="utf-8"))
    s = row.get("stats", {})
    counters = s.get("counters", {})
    # Run averages over the whole duration; the app's own rates are a trailing one-second window.
    row["avg"] = {k: counters.get(k, 0) / duration for k in ("step", "frame", "present")}
    row["fg_active"] = bool(s.get("fg_active"))
    row["frames_per_step"] = s.get("frames_per_step")
    # A profile counts only when the application ran to completion and left statistics;
    # statistics written while an exception unwinds do not make a pass.
    row["ok"] = proc.returncode == 0 and bool(s) and s.get("counters", {}).get("present", 0) > 0
    if not row["ok"]:
        row["verdict"] = f"failed (exit {proc.returncode})"
    else:
        row["verdict"] = ("engaged" if row["fg_active"] else "not engaged") if fg != "off" else "n/a"
    return row


def summarize(rows: list) -> str:
    lines = ["| profile | requested | frames/step | steps/s avg (last s) | presents/s avg (last s) | missed | drops "
             "| step wall ms | SM MHz min-max | throttle | verdict |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        s = r.get("stats", {})
        rates = s.get("rates_last_second", {})
        ms = s.get("mean_ms", {})
        c = s.get("counters", {})
        gpu = r.get("gpu", {})
        lines.append("| {} | {} | {} | {:.0f} ({:.0f}) | {:.0f} ({:.0f}) | {} | {} | {:.1f} | {}-{} | {} | {} |".format(
            r["profile"], r["fg"], r.get("frames_per_step", "?"),
            r["avg"]["step"], rates.get("step", 0), r["avg"]["present"], rates.get("present", 0),
            c.get("missed_step", 0), c.get("drop", 0), ms.get("step_ms", 0),
            gpu.get("sm_mhz_min", "?"), gpu.get("sm_mhz_max", "?"), ", ".join(gpu.get("throttle", ["?"])), r["verdict"]))
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", default="qualification.json")
    ap.add_argument("--duration", type=float, default=20.0)
    ap.add_argument("--size", default="1920x1080")
    ap.add_argument("--python", default=sys.executable)
    ap.add_argument("--profiles", default=",".join(p[0] for p in PROFILES))
    ap.add_argument("--require-fg", action="store_true",
                    help="exit non-zero if a profile that requests frame generation did not engage")
    args = ap.parse_args()
    if args.duration <= 0:
        ap.error("--duration must be > 0")

    wanted = set(args.profiles.split(","))
    rows = []
    with tempfile.TemporaryDirectory(prefix="app_stream_qualify_") as tmp:
        for name, fg, hz, note in PROFILES:
            if name not in wanted:
                continue
            print(f"running {name} ({note}) for {args.duration:.0f}s ...", file=sys.stderr)
            row = run_profile(args.python, name, fg, hz, args.duration, args.size, Path(tmp))
            row["note"] = note
            rows.append(row)
            print(f"  -> {row['verdict']}, frames/step {row.get('frames_per_step')}, exit {row['exit_code']}", file=sys.stderr)
            if row["verdict"] != "engaged" and fg != "off":
                # Put the evidence in the job log, not only in the JSON artifact.
                for line in row.get("fg_log") or row.get("stderr_tail") or []:
                    print(f"     | {line}", file=sys.stderr)

    # Name the GPU in the log itself: a runner pool can hand out different machines
    # under one runner id, and generation needs an Ada or newer GPU.
    gpu_info = (rows[0].get("report") or {}).get("gpu", {}) if rows else {}
    if gpu_info:
        print(f"GPU: {gpu_info.get('name', '?')} driver {gpu_info.get('driver', '?')} compute "
              f"{gpu_info.get('compute_capability', '?')} display_active {gpu_info.get('display_active', '?')}",
              file=sys.stderr)
    doc = {"generated": time.strftime("%Y-%m-%dT%H:%M:%S"), "duration_s": args.duration, "size": args.size,
           "capability_report": rows[0].get("report") if rows else None, "profiles": rows}
    Path(args.out).write_text(json.dumps(doc, indent=1), encoding="utf-8")
    md = summarize(rows)
    Path(args.out).with_suffix(".md").write_text(md + "\n", encoding="utf-8")
    print(md)
    broken = [r for r in rows if not r.get("ok")]
    if broken:
        print(f"profiles did not complete: {', '.join(r['profile'] for r in broken)}", file=sys.stderr)
        return 1
    failed = [r for r in rows if r["fg"] != "off" and not r["fg_active"]]
    if args.require_fg and failed:
        if not supports_frame_generation(gpu_info):
            # Not a qualified configuration: the evidence is recorded, the requirement is not
            # applicable. A runner pool can hand out an Ampere card under the same tags.
            print(f"frame generation needs an Ada (compute 8.9) or newer GPU; {gpu_info.get('name', 'this GPU')} "
                  f"(compute {gpu_info.get('compute_capability', '?')}) cannot run it, so --require-fg does not apply "
                  "here. Results recorded as 'not engaged'.", file=sys.stderr)
            return 0
        print(f"frame generation did not engage for: {', '.join(r['profile'] for r in failed)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
