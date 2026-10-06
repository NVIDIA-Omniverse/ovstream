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

"""Four-way performance comparison on one scene, camera path, resolution and GPU.

    renderer          ovrtx alone, no UI, no server
    renderer+ui       ovrtx with the composited ovui panel, no server
    stream-fg-off     the full application streaming over WebRTC, frame generation off
    stream-fg-on      the full application streaming over WebRTC, frame generation on
    stream-30hz       streaming with generation off at the 30 Hz base rate generation uses

Every variant runs `main.py` for the same duration and records the application's
statistics (rates, per-stage timings, misses, drops) together with the GPU clock
range sampled during the run. The streaming variants also connect a real Chrome
through the Playwright client, which reads the pixel marker from the decoded video
(unique frames per class, delivery interval) and measures input-to-visible latency
by pointer echo, and reports the receiver's decoded frame rate and bitrate.

    python tools/compare.py --duration 30 --out comparison.json
    python tools/compare.py --client-python C:/path/with/playwright/python.exe

Results are written as JSON plus a Markdown table. Cold start (shader compile) is
excluded: measurement begins once the application reports it is streaming. Variants
are separated by an idle cooldown because a power- or thermally-capped GPU (laptops)
otherwise carries the previous variant's throttling into the next; the SM clock
range each variant actually saw is part of the table, so a throttled run is visible
rather than silently averaged.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
MAIN = HERE.parent / "main.py"
CLIENT_DIR = HERE.parents[2] / "webrtc_client"                     # sdk/examples/webrtc_client
PLAYWRIGHT = CLIENT_DIR / "playwright_client.py"

sys.path.insert(0, str(HERE))
from qualify_fg import GpuSampler  # noqa: E402  (same directory)

VARIANTS = [
    # name,            extra args,                                streams
    ("renderer",       ["--no-ui", "--no-stream", "--fg", "off"], False),
    ("renderer+ui",    ["--no-stream", "--fg", "off"],            False),
    ("stream-fg-off",  ["--marker", "--fg", "off", "webrtc"],     True),
    ("stream-fg-on",   ["--marker", "--fg", "on", "webrtc"],      True),
    # Same base render rate as stream-fg-on, without generation: isolates what
    # generation adds in latency and what it gives back in delivered frames.
    ("stream-30hz",    ["--marker", "--fg", "off", "--output-hz", "30", "webrtc"], True),
    # Experimental zero-copy path (stream from the mapped ovrtx output), for comparison
    # against the copy path above; not part of the default set.
    # --zero-copy works in place on the renderer's output and cannot carry
    # the UI, so these variants run headless.
    ("stream-fg-off-zc", ["--no-ui", "--marker", "--fg", "off", "--zero-copy", "webrtc"], True),
    ("stream-fg-on-zc",  ["--no-ui", "--marker", "--fg", "on", "--zero-copy", "webrtc"], True),
]
DEFAULT_VARIANTS = ["renderer", "renderer+ui", "stream-fg-off", "stream-fg-on", "stream-30hz"]
ECHOES = 20


def wait_for(log: Path, proc: subprocess.Popen, needle: str, timeout_s: float) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"app exited early (code {proc.returncode}):\n{log.read_text(encoding='utf-8', errors='replace')[-3000:]}")
        if log.exists() and needle in log.read_text(encoding="utf-8", errors="replace"):
            return
        time.sleep(0.5)
    proc.kill()
    raise RuntimeError(f"app did not report '{needle}' within {timeout_s:.0f} s")


def run_variant(name: str, extra: list, streams: bool, args, workdir: Path) -> dict:
    stats = workdir / f"{name}.stats.json"
    log = workdir / f"{name}.log"
    cmd = [args.python, str(MAIN), "--size", args.size, "--output-hz", str(args.output_hz),
           "--duration", str(args.duration), "--stats-json", str(stats), *extra]
    row = {"variant": name, "args": extra}
    with open(log, "w", encoding="utf-8") as log_file:
        proc = subprocess.Popen(cmd, stdout=log_file, stderr=subprocess.STDOUT, cwd=str(workdir))
        try:
            wait_for(log, proc, "Streaming.", args.startup_timeout)
            with GpuSampler() as gpu:
                if streams:
                    capture = max(5.0, args.duration - 12.0)
                    result = workdir / f"{name}.client.json"
                    client = subprocess.run(
                        [args.client_python, str(PLAYWRIGHT), "--client-dir", str(CLIENT_DIR), "--signal-port", "49100",
                         "--result-file", str(result), "--console-file", str(workdir / f"{name}.console.txt"),
                         "--marker-capture", str(capture), "--marker-echo", str(ECHOES), "--timeout-ms", "40000"],
                        capture_output=True, text=True, timeout=args.duration + 120)
                    row["client_exit"] = client.returncode
                    if client.returncode != 0:
                        row["client_stderr"] = client.stderr.strip()[-2000:]
                    if result.exists():
                        row["client"] = json.loads(result.read_text(encoding="utf-8"))
                    marker = (row.get("client") or {}).get("marker") or {}
                    if client.returncode != 0 or not (row.get("client") or {}).get("video_decoded"):
                        row["error"] = f"browser client failed (exit {client.returncode}, no decoded video)"
                    elif not marker.get("unique_frames") or marker.get("echo_seen", 0) == 0:
                        row["error"] = "browser client decoded video but read no marker frames or echoes"
                proc.wait(timeout=args.duration + 60)
            row["gpu"] = gpu.summary()
        finally:
            if proc.poll() is None:
                proc.kill()
    row["exit_code"] = proc.returncode
    if proc.returncode != 0:
        row["error"] = f"application exited with code {proc.returncode}"
    if stats.exists():
        row["stats"] = json.loads(stats.read_text(encoding="utf-8"))
        counters = row["stats"]["counters"]
        row["avg"] = {k: counters.get(k, 0) / args.duration for k in ("step", "frame", "present")}
    return row


def fmt(v, digits=1):
    if v is None:
        return "-"
    return f"{v:.{digits}f}" if isinstance(v, float) else str(v)


def summarize(rows: list) -> str:
    head = ("| variant | steps/s | frames/s | presents/s | step ms | render->ready ms | ui ms | stream_video ms "
            "| missed | drops | client fps (unique / decoded) | interval ms | latency p50 / p95 ms | to simulated p50 ms | kbps | SM MHz | throttle |")
    lines = [head, "|" + "---|" * 17]
    for r in rows:
        s = r.get("stats", {})
        ms = s.get("mean_ms", {})
        c = s.get("counters", {})
        avg = r.get("avg", {})
        m = (r.get("client") or {}).get("marker") or {}
        recv = m.get("receiver") or {}
        lat = m.get("latency_ms") or {}
        lat_sim = m.get("latency_simulated_ms") or {}
        gpu = r.get("gpu", {})
        client_fps = "-"
        if m:
            unique = 1000.0 / m["interval_ms_mean"] if m.get("interval_ms_mean") else None
            client_fps = f"{fmt(unique)} / {fmt(recv.get('decoded_fps'))}"
        lines.append("| {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | {} |".format(
            r["variant"], fmt(avg.get("step")), fmt(avg.get("frame")), fmt(avg.get("present")),
            fmt(ms.get("step_ms")), fmt(ms.get("gpu_ms")), fmt(ms.get("ui_ms")) if "ui_ms" in ms else "-",
            fmt(ms.get("stream_ms"), 2) if s.get("run", {}).get("servers") else "-",
            c.get("missed_step", 0), c.get("drop", 0), client_fps, fmt(m.get("interval_ms_mean"), 2),
            f"{fmt(lat.get('p50'))} / {fmt(lat.get('p95'))}" if lat else "-", fmt(lat_sim.get("p50")) if lat_sim else "-",
            fmt(recv.get("kbps"), 0),
            f"{gpu.get('sm_mhz_min', '?')}-{gpu.get('sm_mhz_max', '?')}", ", ".join(gpu.get("throttle", ["?"]))))
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", default="comparison.json")
    ap.add_argument("--duration", type=float, default=30.0, help="seconds per variant after startup")
    ap.add_argument("--size", default="1920x1080")
    ap.add_argument("--output-hz", type=int, default=60)
    ap.add_argument("--python", default=sys.executable, help="interpreter that runs the application")
    ap.add_argument("--client-python", default=os.environ.get("OVSTREAM_PLAYWRIGHT_PYTHON", sys.executable),
                    help="interpreter with Playwright installed, for the browser client")
    ap.add_argument("--variants", default=",".join(DEFAULT_VARIANTS),
                    help="comma-separated subset; also available: stream-fg-off-zc, stream-fg-on-zc (--zero-copy)")
    ap.add_argument("--startup-timeout", type=float, default=900.0)
    ap.add_argument("--cooldown", type=float, default=30.0,
                    help="idle seconds between variants so a power- or thermally-capped GPU recovers; "
                         "the GPU clock range each variant saw is in the table regardless")
    args = ap.parse_args()

    wanted = set(args.variants.split(","))
    rows = []
    with tempfile.TemporaryDirectory(prefix="app_stream_compare_") as tmp:
        for name, extra, streams in VARIANTS:
            if name not in wanted:
                continue
            if rows and args.cooldown > 0:
                print(f"cooling down for {args.cooldown:.0f} s ...", file=sys.stderr)
                time.sleep(args.cooldown)
            print(f"running {name} for {args.duration:.0f} s ...", file=sys.stderr)
            try:
                row = run_variant(name, extra, streams, args, Path(tmp))
            except Exception as e:  # keep the other variants
                row = {"variant": name, "error": f"{type(e).__name__}: {e}"}
                print(f"  -> {row['error']}", file=sys.stderr)
            rows.append(row)
            if "avg" in row:
                print(f"  -> {row['avg']['step']:.1f} steps/s, {row['avg']['present']:.1f} presents/s", file=sys.stderr)
    doc = {"generated": time.strftime("%Y-%m-%dT%H:%M:%S"), "duration_s": args.duration, "size": args.size,
           "output_hz": args.output_hz, "variants": rows}
    Path(args.out).write_text(json.dumps(doc, indent=1), encoding="utf-8")
    md = summarize([r for r in rows if "stats" in r])
    Path(args.out).with_suffix(".md").write_text(md + "\n", encoding="utf-8")
    print(md)
    return 0 if all("error" not in r for r in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
