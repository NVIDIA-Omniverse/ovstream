# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary
#
# NVIDIA CORPORATION, its affiliates and licensors retain all intellectual
# property and proprietary rights in and to this material, related
# documentation and any modifications thereto. Any use, reproduction,
# disclosure or distribution of this material and related documentation
# without an express license agreement from NVIDIA CORPORATION or
# its affiliates is strictly prohibited.

"""Headless-browser WebRTC client driver for the browser-client-decode scenario.

Serves examples/webrtc_client/ over a local HTTP server, loads it in a headless
browser, connects to a loopback ovstream WebRTC-backend server, and verifies a
video frame actually decodes. Writes a JSON result and the browser console log
to files. Runs as its own process so the Playwright dependency (and its event
loop) is isolated from the pytest process -- mirroring how ovstream_webrtc_probe
isolates the StreamSDK client.

This is the one lane that exercises the real browser WebRTC path (the WebRTC
backend), which the StreamSDK C++ probe deliberately does not.

Result JSON fields:
    browser_unavailable  true if no Chrome/Chromium could be launched -> the
                         orchestrator treats this as a SKIP, not a failure.
    connected            the page reported a "Connected" status.
    video_decoded        the <video> element reached HAVE_CURRENT_DATA with
                         non-zero dimensions and an advancing currentTime.
    video_width          decoded video width (px).
    browser              which browser launched ("chrome").
    app_echo_small       (--app-echo-check) a single-part application message sent
                         from the page came back from the server's echo intact.
    app_echo_big         (--app-echo-check) a multi-part application message came
                         back as one reassembled delivery with the full payload.
    cursor_applied       (--app-echo-check) the server's Hand cursor, reported in
                         the config handshake, was applied to the <video> element.

Browser choice: Google Chrome (the Playwright `chrome` channel) is required
because ovstream encodes H.264 and Playwright's bundled Chromium lacks the
proprietary H.264 codec. If Chrome is unavailable the driver reports
browser_unavailable so the test skips rather than false-failing.
"""

from __future__ import annotations

import argparse
import functools
import json
import sys
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def _serve(directory: str):
    """Start a background HTTP server bound to an ephemeral loopback port.

    Returns (httpd, port). Serving over http:// (rather than file://) keeps the
    page origin well-defined so the browser reliably allows the ws:// signaling
    connection to the loopback server.
    """
    # Subclass to suppress the default per-request stderr logging. (Setting
    # log_message on the functools.partial has no effect -- the partial is not
    # the handler class, so instances keep the base log_message.)
    class _QuietHandler(SimpleHTTPRequestHandler):
        def log_message(self, *args, **kwargs):
            pass

    handler = functools.partial(_QuietHandler, directory=directory)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


def _launch_browser(p, headed=False):
    """Launch Google Chrome (the `chrome` channel).

    Chrome is required rather than Playwright's bundled Chromium because
    ovstream encodes H.264 and Chromium lacks the proprietary H.264 codec --
    Chromium would connect but never decode a frame, which is a false failure
    rather than a real product issue. If Chrome is unavailable we report it so
    the orchestrator skips (browser_unavailable) instead of failing.

    Returns (browser, label) or (None, None) if Chrome could not be launched.
    """
    launch_args = ["--no-sandbox", "--autoplay-policy=no-user-gesture-required"]
    try:
        browser = p.chromium.launch(headless=not headed, channel="chrome", args=launch_args)
        return browser, "chrome"
    except Exception:  # noqa: BLE001 - Chrome not installed / not launchable
        return None, None


def main() -> int:
    ap = argparse.ArgumentParser(description="Playwright WebRTC browser client")
    ap.add_argument("--client-dir", default=str(Path(__file__).resolve().parent),
                    help="directory containing index.html (default: this script's directory)")
    ap.add_argument("--server-ip", default="127.0.0.1")
    ap.add_argument("--signal-port", type=int, required=True)
    ap.add_argument("--timeout-ms", type=int, default=30000)
    ap.add_argument("--result-file", required=True)
    ap.add_argument("--console-file", default="")
    ap.add_argument("--ice-server", action="append", default=[],
                    help="ICE server URL to inject into the browser's "
                         "RTCPeerConnection (repeatable), e.g. "
                         "'turn:1.2.3.4:3478' or 'turn:1.2.3.4:3478?transport=tcp'")
    ap.add_argument("--turn-user", default="")
    ap.add_argument("--turn-cred", default="")
    ap.add_argument("--force-relay", action="store_true",
                    help="set iceTransportPolicy='relay' so the browser uses "
                         "ONLY relay candidates -- a decoded frame then proves "
                         "the TURN relay path works")
    ap.add_argument("--app-echo-check", action="store_true",
                    help="after the frame decodes, send application messages "
                         "from the page and expect the server (started with "
                         "--app-echo) to echo them; also check the cursor")
    ap.add_argument("--app-stream-check", action="store_true",
                    help="after the frame decodes, expect app_stream:state messages, "
                         "send app_stream:set {fg: false} and expect the state to follow")
    ap.add_argument("--marker-capture", type=float, default=0.0,
                    help="seconds to capture decoded frames and read the app_stream pixel marker "
                         "(server started with --marker); reports frame classes and unique frames")
    ap.add_argument("--marker-echo", type=int, default=0,
                    help="number of pointer moves to time against the marker's echoed pointer "
                         "position (input-to-visible latency); requires --marker-capture")
    ap.add_argument("--headed", action="store_true", help="launch a visible browser window")
    ap.add_argument("--viewport", default="", help="WxH page viewport (default: Playwright's 1280x720)")
    ap.add_argument("--resize", default="",
                    help="WxH: after the frame decodes, request this stream size and "
                         "expect the video element to follow (server started resizable)")
    args = ap.parse_args()

    console_lines: list[str] = []
    result = {
        "browser_unavailable": False,
        "connected": False,
        "video_decoded": False,
        "video_width": 0,
        "current_time": 0.0,
        "browser": None,
        "relay_forced": bool(args.force_relay),
        "selected_candidate_type": None,
        "app_echo_small": False,
        "app_echo_big": False,
        "cursor_applied": False,
        "app_stream_state": False,
        "app_stream_fg_off": False,
        "resized_to": None,
        "marker": None,
    }
    # Application messages the page received, read from the example client's
    # `console.log('app message', msg)` line in its onCustomEvent handler.
    app_messages: list[dict] = []

    # Build the RTCPeerConnection-injection init script. It runs before any page
    # script, so the override is in place before the streaming-library bundle
    # captures window.RTCPeerConnection. We merge our ICE servers into whatever
    # config the library passes and, when --force-relay is set, pin
    # iceTransportPolicy='relay' so the browser uses ONLY relay candidates --
    # making a successful decode proof that the TURN relay path works.
    ice_servers = []
    for url in args.ice_server:
        entry = {"urls": url}
        if args.turn_user:
            entry["username"] = args.turn_user
        if args.turn_cred:
            entry["credential"] = args.turn_cred
        ice_servers.append(entry)
    inject_script = (
        "(() => {\n"
        f"  const ICE = {json.dumps(ice_servers)};\n"
        f"  const FORCE_RELAY = {json.dumps(bool(args.force_relay))};\n"
        "  const Orig = window.RTCPeerConnection || window.webkitRTCPeerConnection;\n"
        "  if (!Orig) return;\n"
        "  const Patched = function(cfg) {\n"
        "    cfg = cfg || {};\n"
        "    cfg.iceServers = (cfg.iceServers || []).concat(ICE);\n"
        "    if (FORCE_RELAY) { cfg.iceTransportPolicy = 'relay'; }\n"
        "    console.log('[ice-inject] new RTCPeerConnection policy=' + cfg.iceTransportPolicy + ' servers=' + JSON.stringify(cfg.iceServers));\n"
        "    const pc = new Orig(cfg);\n"
        "    window.__lastPc = pc;\n"
        "    return pc;\n"
        "  };\n"
        "  Patched.prototype = Orig.prototype;\n"
        "  window.RTCPeerConnection = Patched;\n"
        "  window.webkitRTCPeerConnection = Patched;\n"
        "})();"
    )

    def finish(code: int) -> int:
        Path(args.result_file).write_text(json.dumps(result), encoding="utf-8")
        if args.console_file:
            Path(args.console_file).write_text("\n".join(console_lines), encoding="utf-8")
        print(json.dumps(result))
        return code

    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:  # noqa: BLE001
        result["browser_unavailable"] = True
        console_lines.append(f"[import-error] {exc!r}")
        return finish(0)

    httpd, http_port = _serve(args.client_dir)
    url = f"http://127.0.0.1:{http_port}/index.html"

    try:
        with sync_playwright() as p:
            browser, label = _launch_browser(p, headed=args.headed)
            if browser is None:
                result["browser_unavailable"] = True
                console_lines.append("[no-browser] neither chrome nor chromium launched")
                return finish(0)
            result["browser"] = label

            if args.viewport:
                vw, vh = (int(v) for v in args.viewport.lower().split("x"))
                page = browser.new_page(viewport={"width": vw, "height": vh})
            else:
                page = browser.new_page()

            def on_console(m):
                console_lines.append(f"[{m.type}] {m.text}")
                if m.text.startswith("app message") and len(m.args) > 1:
                    try:
                        app_messages.append(m.args[1].json_value())
                    except Exception as exc:  # noqa: BLE001
                        console_lines.append(f"[app-message-parse] {exc!r}")

            page.on("console", on_console)
            page.on("pageerror", lambda e: console_lines.append(f"[pageerror] {e}"))
            # Inject the ICE-server / transport-policy override before any page
            # script runs (only when there's something to inject).
            # The marker capture also reads receiver statistics from the peer
            # connection the script captures, so install it for that case too.
            if ice_servers or args.force_relay or args.marker_capture > 0:
                page.add_init_script(inject_script)
            page.goto(url, wait_until="load")

            page.fill("#server-ip", args.server_ip)
            page.fill("#signal-port", str(args.signal_port))
            page.click("#connect-button")

            # Poll the <video> element until a frame has actually decoded: it
            # has dimensions, reached HAVE_CURRENT_DATA (readyState >= 2), and
            # advanced past t=0. readyState alone can be satisfied by metadata,
            # so the currentTime check is what proves a frame was rendered.
            deadline = time.monotonic() + args.timeout_ms / 1000.0
            while time.monotonic() < deadline:
                info = page.evaluate(
                    """() => {
                        const v = document.getElementById('remote-video');
                        const s = document.getElementById('status');
                        if (!v) return null;
                        return { w: v.videoWidth, h: v.videoHeight,
                                 t: v.currentTime, rs: v.readyState,
                                 status: s ? s.textContent : '' };
                    }""")
                if info:
                    if info.get("status") == "Connected":
                        result["connected"] = True
                    if info["w"] > 0 and info["rs"] >= 2 and info["t"] > 0:
                        result["video_decoded"] = True
                        result["video_width"] = info["w"]
                        result["current_time"] = info["t"]
                        break
                time.sleep(0.5)

            if args.app_echo_check and result["video_decoded"]:
                # Let the config handshake settle so the client sends the
                # id/part/numParts envelope and has applied the cursor.
                page.wait_for_timeout(1000)
                small = {"n": 1, "text": "small"}
                # Larger than one transport message (64 KiB), so it can only
                # arrive if both the browser library and the server split
                # and reassemble it.
                big = {"blob": "x" * 100000}
                page.evaluate("(m) => streamer.sendMessage(m)",
                              {"event_type": "echo-small", "payload": small})
                page.evaluate("(m) => streamer.sendMessage(m)",
                              {"event_type": "echo-big", "payload": big})
                deadline = time.monotonic() + 10.0
                while time.monotonic() < deadline and len(app_messages) < 2:
                    # wait_for_timeout pumps Playwright's event dispatch, which
                    # a bare sleep does not.
                    page.wait_for_timeout(200)
                small_back = [m for m in app_messages if m.get("event_type") == "echo-small"]
                big_back = [m for m in app_messages if m.get("event_type") == "echo-big"]
                result["app_echo_small"] = len(small_back) == 1 and small_back[0].get("payload") == small
                result["app_echo_big"] = len(big_back) == 1 and big_back[0].get("payload") == big
                cursor = page.evaluate(
                    "() => document.getElementById('remote-video').style.cursor")
                result["cursor_applied"] = cursor == "pointer"
                console_lines.append(
                    f"[app-echo-check] received={len(app_messages)} cursor={cursor!r}")

            if args.app_stream_check and result["video_decoded"]:
                def states():
                    return [m for m in app_messages if m.get("event_type") == "app_stream:state"]
                deadline = time.monotonic() + 5.0
                while time.monotonic() < deadline and not states():
                    page.wait_for_timeout(200)
                result["app_stream_state"] = bool(states())
                page.evaluate("(m) => streamer.sendMessage(m)",
                              {"event_type": "app_stream:set", "payload": {"fg": False}})
                deadline = time.monotonic() + 10.0
                while time.monotonic() < deadline:
                    page.wait_for_timeout(250)
                    recent = states()[-1:]
                    if recent and recent[0].get("payload", {}).get("fg_active") is False:
                        result["app_stream_fg_off"] = True
                        break
                console_lines.append(f"[app-stream-check] states={len(states())} "
                                     f"last={states()[-1].get('payload') if states() else None}")

            if args.resize and result["video_decoded"]:
                w, h = (int(v) for v in args.resize.lower().split("x"))
                dims_js = ("() => { const v = document.getElementById('remote-video'); "
                           "return [v.videoWidth, v.videoHeight]; }")
                before = page.evaluate(dims_js)
                page.evaluate("([w, h]) => streamer.resize(w, h)", [w, h])
                deadline = time.monotonic() + 10.0
                while time.monotonic() < deadline:
                    page.wait_for_timeout(250)
                    dims = page.evaluate(dims_js)
                    # Both dimensions must have moved off the pre-resize size; the server
                    # floors to its alignment rules, so the exact target is checked by the caller.
                    if dims[0] != before[0] and dims[1] != before[1]:
                        result["resized_to"] = dims
                        break
                console_lines.append(f"[resize] requested {w}x{h} -> {result['resized_to']}")


            # ---- pixel marker capture (see app_stream/marker.py) --------------
            # Each decoded frame is drawn to a canvas and the 3 x 32 cell block in
            # the bottom-left corner is sampled at cell centres; a checksum guards
            # every read. Pointer moves are timestamped and matched against the
            # marker's echoed pointer position for input-to-visible latency.
            if args.marker_capture > 0 and result["video_decoded"]:
                page.evaluate("""() => {
                    const CELL = 8, COLS = 32, ROWS = 3, MARGIN = 8;
                    const v = document.getElementById('remote-video');
                    const canvas = document.createElement('canvas');
                    const ctx = canvas.getContext('2d', { willReadFrequently: true });
                    const st = window.__marker = { frames: [], lastId: -1, pending: [], latencies: [], latenciesSim: [], bad: 0 };
                    function checksum(w0, w1, w2h) {
                        let c = 0xA5;
                        for (const w of [w0, w1, w2h]) for (const sh of [0, 8, 16, 24]) c ^= (w >>> sh) & 0xFF;
                        return c;
                    }
                    function read() {
                        const W = v.videoWidth, H = v.videoHeight;
                        if (!W || !H) return null;
                        if (canvas.width !== W || canvas.height !== H) { canvas.width = W; canvas.height = H; }
                        ctx.drawImage(v, 0, 0, W, H);
                        const x0 = MARGIN, y0 = H - MARGIN - CELL * ROWS;
                        const data = ctx.getImageData(x0, y0, CELL * COLS, CELL * ROWS).data;
                        const words = [];
                        for (let r = 0; r < ROWS; r++) {
                            let w = 0;
                            for (let c = 0; c < COLS; c++) {
                                const px = c * CELL + CELL / 2, py = r * CELL + CELL / 2;
                                const lum = data[(py * CELL * COLS + px) * 4];
                                if (lum >= 128) w |= (1 << (COLS - 1 - c));
                            }
                            words.push(w >>> 0);
                        }
                        const [w0, w1, w2] = words;
                        if ((w2 & 0xFF) !== checksum(w0, w1, (w2 & 0xFFFFFF00) >>> 0)) { st.bad++; return null; }
                        return { id: w0, ordinal: w1 >>> 8, cls: (w1 >>> 6) & 3,
                                 px: (w2 >>> 20) & 0xFFF, py: (w2 >>> 8) & 0xFFF, t: performance.now() };
                    }
                    function onFrame(now, meta) {
                        const m = read();
                        if (m && m.id !== st.lastId) {
                            st.lastId = m.id;
                            st.frames.push([m.t, m.id, m.ordinal, m.cls, m.px, m.py]);
                            // Resolve pending pointer echoes: the first frame whose marker carries the moved-to
                            // position (any class) and, separately, the first simulated frame that does. With
                            // generation on the first echo is normally the interpolated frame leading to it.
                            for (let i = st.pending.length - 1; i >= 0; i--) {
                                const p = st.pending[i];
                                // The client library and the server round the pointer independently; allow a few pixels.
                                if (Math.abs(m.px - p.x) <= 3 && Math.abs(m.py - p.y) <= 3) {
                                    if (!p.first) {
                                        p.first = true;
                                        st.latencies.push({ sent: p.t, seen: m.t, ms: m.t - p.t, cls: m.cls, ordinal: m.ordinal });
                                    }
                                    if (m.cls === 0 && !p.sim) {
                                        p.sim = true;
                                        st.latenciesSim.push({ sent: p.t, seen: m.t, ms: m.t - p.t, ordinal: m.ordinal });
                                    }
                                    if (p.first && p.sim) st.pending.splice(i, 1);
                                }
                            }
                        }
                        v.requestVideoFrameCallback(onFrame);
                    }
                    v.requestVideoFrameCallback(onFrame);
                }""")
                RTP_STATS_JS = """async () => {
                    const pc = window.__lastPc;
                    if (!pc || !pc.getStats) return null;
                    const stats = await pc.getStats();
                    let out = null;
                    stats.forEach(r => {
                        if (r.type === 'inbound-rtp' && r.kind === 'video') {
                            out = { t: performance.now(), framesDecoded: r.framesDecoded, framesDropped: r.framesDropped,
                                    bytesReceived: r.bytesReceived, jitter: r.jitter, frameWidth: r.frameWidth,
                                    frameHeight: r.frameHeight, packetsLost: r.packetsLost };
                        }
                    });
                    return out;
                }"""
                rtp_start = page.evaluate(RTP_STATS_JS)
                echoes_left = args.marker_echo
                # Pointer targets walk a diagonal in the scene area (away from the panel in the top-right).
                box = page.evaluate("() => { const r = document.getElementById('remote-video').getBoundingClientRect();"
                                    " const v = document.getElementById('remote-video');"
                                    " return [r.left, r.top, r.width, r.height, v.videoWidth, v.videoHeight]; }")
                left, top, cw, ch, vw, vh = box
                deadline = time.monotonic() + args.marker_capture
                k = 0
                # The application echoes the pointer position its next render submit consumed,
                # so a target sent before the previous one has been echoed is swallowed whenever
                # the renderer submits less often than targets arrive. Each target waits
                # (bounded) for its own echo before the next move, and the echo phase runs to
                # completion even past the capture window so every target gets its chance.
                echo_wait_s = 1.5
                while echoes_left > 0 or time.monotonic() < deadline:
                    if echoes_left > 0:
                        # Unique stream-space target inside the current video size (the stream may
                        # have been resized); scale to CSS pixels for the move.
                        sx = 100 + (k * 37) % max(1, vw - 200)
                        sy = 80 + (k * 53) % max(1, vh - 160)
                        page.evaluate("([x, y, k]) => window.__marker.pending.push({x, y, k, t: performance.now()})",
                                      [sx, sy, k])
                        page.mouse.move(left + sx * cw / vw, top + sy * ch / vh)
                        echoes_left -= 1
                        echo_deadline = time.monotonic() + echo_wait_s
                        while time.monotonic() < echo_deadline and not page.evaluate(
                                "(k) => { const p = window.__marker.pending.find(e => e.k === k); return !p || !!p.first; }", k):
                            page.wait_for_timeout(50)
                        k += 1
                        page.wait_for_timeout(100)
                    else:
                        page.wait_for_timeout(200)
                marker = page.evaluate("() => window.__marker")
                rtp_end = page.evaluate(RTP_STATS_JS)
                receiver = None
                if rtp_start and rtp_end and rtp_end["t"] > rtp_start["t"]:
                    span_s = (rtp_end["t"] - rtp_start["t"]) / 1000.0
                    receiver = {
                        "decoded_fps": round((rtp_end["framesDecoded"] - rtp_start["framesDecoded"]) / span_s, 1),
                        "dropped": (rtp_end.get("framesDropped") or 0) - (rtp_start.get("framesDropped") or 0),
                        "kbps": round((rtp_end["bytesReceived"] - rtp_start["bytesReceived"]) * 8 / 1000.0 / span_s),
                        "jitter_ms": round((rtp_end.get("jitter") or 0) * 1000.0, 1),
                        "packets_lost": (rtp_end.get("packetsLost") or 0) - (rtp_start.get("packetsLost") or 0),
                        "size": [rtp_end.get("frameWidth"), rtp_end.get("frameHeight")],
                    }
                frames = marker["frames"]
                classes = {"simulated": 0, "interpolated": 0, "repeat": 0}
                for _, _, _, cls, _, _ in frames:
                    classes[("simulated", "interpolated", "repeat")[cls]] += 1
                lat = sorted(l["ms"] for l in marker["latencies"])
                # With frame generation the first echo is usually the interpolated frame that
                # leads to the simulated one; latency to the simulated frame itself is reported too.
                lat_sim = sorted(l["ms"] for l in marker["latenciesSim"])
                def pct(p, values=None):
                    values = lat if values is None else values
                    return round(values[min(len(values) - 1, int(p * len(values)))], 1) if values else None
                intervals = [b[0] - a[0] for a, b in zip(frames, frames[1:])]
                result["marker"] = {
                    "unique_frames": len(frames),
                    "unreadable": marker["bad"],
                    "classes": classes,
                    "unique_ordinals": len({f[2] for f in frames}),
                    "interval_ms_mean": round(sum(intervals) / len(intervals), 2) if intervals else None,
                    "echo_sent": args.marker_echo,
                    "echo_seen": len(lat),
                    "echo_unanswered": sum(1 for p in marker["pending"] if not p.get("first")),
                    "latency_ms": {"p50": pct(0.5), "p95": pct(0.95), "p99": pct(0.99),
                                   "min": round(lat[0], 1) if lat else None, "max": round(lat[-1], 1) if lat else None},
                    "latency_simulated_ms": {"p50": pct(0.5, lat_sim), "p95": pct(0.95, lat_sim), "n": len(lat_sim)},
                    "receiver": receiver,
                }
                console_lines.append(f"[marker] {json.dumps(result['marker'])}")

            # Diagnostic: report the selected ICE candidate type (host/srflx/
            # relay) from the peer connection's stats. Under --force-relay this
            # should be 'relay'; it confirms HOW the media path was established.
            try:
                cand = page.evaluate(
                    """async () => {
                        const pc = window.__lastPc;
                        if (!pc || !pc.getStats) return null;
                        const stats = await pc.getStats();
                        let sel = null;
                        stats.forEach(r => {
                            if (r.type === 'candidate-pair' && (r.selected || r.nominated) && r.state === 'succeeded') sel = r;
                        });
                        if (!sel) return null;
                        const local = stats.get(sel.localCandidateId);
                        return local ? local.candidateType : null;
                    }""")
                result["selected_candidate_type"] = cand
            except Exception as exc:  # noqa: BLE001 - diagnostic only
                console_lines.append(f"[stats-error] {exc!r}")

            browser.close()
    except Exception as exc:  # noqa: BLE001 - report any driver failure as a result
        console_lines.append(f"[driver-exception] {exc!r}")
    finally:
        httpd.shutdown()

    return finish(0 if result["video_decoded"] else 1)


if __name__ == "__main__":
    sys.exit(main())
