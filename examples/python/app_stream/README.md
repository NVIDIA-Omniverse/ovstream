# app_stream: streaming reference implementation (Python)

How to compose [ovrtx](https://github.com/NVIDIA-Omniverse/ovrtx), [ovstage](https://github.com/NVIDIA-Omniverse/ovstage), [ovui](https://pypi.org/project/ovui/), ovstream and DLSS Frame Generation into a low-latency streaming application built from NVIDIA libraries. Where `../ovrtx_stream/` is the smallest useful pairing, this example is the full reference implementation: a decoupled render cadence, frame generation paced into the delivered stream, explicit frame ownership, and a statistics readout that keeps renderer cost and streaming cost apart.

> Status: renders, generates frames, paces them into the delivered stream, wraps the viewport in an ovui application chrome, forwards the cursor, exchanges application messages with the browser client, honours client resize requests (`--resizable`), and streams over every ovstream transport.

## Prerequisites

- Python 3.12 or 3.13 (the ovui wheel requires 3.12) and [uv](https://docs.astral.sh/uv/) (recommended). `--no-ui` never imports ovui.
- An NVIDIA RTX GPU. DLSS Frame Generation needs an Ada (RTX 40-series) or newer GPU and a supported driver; the application detects at runtime whether it engaged.

## Run

```bash
uv run main.py --report          # print what is installed and what the run will try, then exit
uv run main.py                   # render + application chrome, stream over WebRTC on signal port 49100
uv run main.py --no-ui rtsp shm:demo   # render only, two transports
```

Transports use the same `<protocol>[:port-or-name]` arguments as the other examples. Open [`../../webrtc_client/index.html`](../../webrtc_client/index.html) and connect to `127.0.0.1:49100`; left-drag orbits the camera, the wheel zooms, any key resumes the auto-orbit. The top bar toggles frame generation and the auto orbit and resets the camera; the dock on the right shows the scene, rendering, camera, streaming and statistics state; pointer events over the chrome go to ovui, everything over the viewport to the camera. Keyboard and text follow the last click: after clicking the panel they go to ovui, otherwise any key resumes the auto-orbit. The cursor shape follows the pointer (arrow over the panel, crosshair over the scene, hand while dragging).

Useful options: `--fg auto|on|off` (default `auto`), `--fg-interpolated N` (interpolated frames to request per simulated frame; `0` lets the renderer choose), `--output-hz` (delivered rate, default 60; 120 for the high-rate profile), `--size WxH`, `--duration S`, `--publish-thread` (submit frames from a worker thread), `--stats-json FILE` (write the final statistics on exit), `--renderer-log FILE` (the renderer's own log at info level, which records why frame generation did or did not engage), `--resizable` (honour browser resize requests; `--size` becomes the maximum), `--marker` (draw the frame-identity pixel marker used for measurement), `--no-stream` (render and UI loop with no server, for renderer-only measurements), `--zero-copy` (experimental, see the zero-copy experiment below).

## What it shows

**Scene ownership.** ovstage owns the scene. The application writes a root layer that sublayers the requested scene and defines the streaming RenderProduct `/Render/Stream` (real-time path tracing, `LdrColor` only, the two frame-generation attributes), then opens it with `ovstage.population.open_usd`. The camera transform is published under a fresh ordinal each render step and the renderer is stepped at that ordinal.

**Two cadences on one thread.** The loop runs at the delivered rate (`ovstream_utils.Loop`, default 60 Hz). A render step is owed at the base rate, which follows from the detected frames per step: with frame generation producing one interpolated frame, the renderer steps at 30 Hz and the application presents one frame per tick, so 60 frames are delivered from 30 renders. Without frame generation the base rate equals the delivered rate. Steps are submitted with `step_async` and collected with two non-blocking polls (`Operation.wait(0)`, then `PendingFetch.fetch(0)`); at most one step is in flight, and a step owed while the previous one is still running is counted as missed rather than stalling the loop. Because a step completes in the background, its frames are ready by the time they are presented, so the transport's wait for the GPU inside `stream_video` is short; `--publish-thread` moves that wait off the loop entirely for transports where it is not; a ring slot stays owned until the worker has finished publishing it, and a tick that finds every slot in flight skips its frame (`skip:busy`) rather than overwrite one.

**Frame generation as a contract, not an assumption.** Each step's frames are classified per firing: interpolated frames first, the simulated frame last, strictly increasing in time and ending at the step boundary. Timing alone cannot tell a generated frame from a camera that fires twice per step, so the renderer's own word is used as well: ovrtx reports a `progression` of 0 for a frame it generated and at least 1 for one it rendered, and exactly one rendered frame per step is required. Anything else raises rather than being mistaken for frame generation. A detector watches the frame count; when generation is requested but does not engage, the checklist is printed and the run continues unchanged.

**Frame path.** `LdrColor` (RGBA8) is mapped to CUDA and copied on the application's CUDA stream into a BGRA8 ring slot, swizzled in place by a Warp kernel, and handed to `ovstream.Server.stream_video` with the slot's CUDA event as the sync hint. ovstream stages the frame before the call returns, so the slot is free again immediately. One swizzle per delivered frame; no host synchronisation in the frame path other than the wait the transport performs itself.

**The application chrome.** ovui runs headless on Vulkan at the full frame size and draws the application around a blank viewport region: a top bar with the title and the controls (frame generation, auto orbit, camera reset), a dock on the right with Scene, Rendering, Camera, Streaming and Statistics sections (including a live plot of step wall time and a connection indicator per server), and a status bar. Each tick the application injects the chrome's share of the input, ticks ovui, and exports the rendered frame to CUDA (`omni.ui.standalone.headless_frame`: a Vulkan-to-CUDA semaphore wait queued on the null stream, then a copy on the application's stream). The renderer renders at the viewport's size, a Warp kernel blits that image into the viewport region of the exported chrome, the whole frame is converted to BGRA8 in one pass, and the consumed signal goes back to ovui. The scene is never uploaded into ovui and no host copy is involved. Widget callbacks run inside the ovui tick, before the render submit, so a toggle is published under the same ordinal as the pointer input that caused it. Everything outside the viewport is UI for input routing; a pressed button pins the gesture to the surface it started on. `tools/snapshot.py` saves one streamed frame as a PNG, which is how the layout was checked while building it.

**Control channel.** On WebRTC and native servers the application registers the text, resize, cursor and application-message paths before `start()` (a client that handshakes early is otherwise told they are unavailable). Inbound `app_stream:set` messages carry any of `{"fg": bool, "camera_reset": true, "size": [w, h]}`; every quarter second the application broadcasts `app_stream:state` with the active frame-generation state, rates, size and counters, so a browser page can mirror the panel. Both go through `ovstream_utils.messaging.AppMessenger`; the stock browser client logs them to the console. All control-channel callbacks hand off to the main thread through the input bridge, so scene writes only ever happen on the loop thread.

**Resize.** A client resize request is debounced (150 ms after the last request), clamped to the encoder alignment rules and the frame-generation minimum, and applied by rewriting the RenderProduct's `resolution` attribute; the next step renders at the new size. The stream ring is allocated once at the maximum size, outside Warp's memory pool (the encoder registers the buffer directly on a resizable stream and cannot register pool memory), and each frame is handed to ovstream as a sub-array with the slot pitch, so a resize never reallocates GPU memory, which is what resizable WebRTC streams require. The panel re-anchors to the new corner.

**Statistics.** Once a second: steps/s, frames/s, presents/s, repeats, dropped frames, missed steps, step wall time (submit to complete), render-to-ready GPU time (CUDA events around the map, copy and swizzle; includes any remaining wait for the renderer), pipeline CPU time, `stream_video` time and, with the panel on, the ovui tick time. Renderer cost is reported separately from streaming cost on purpose. `--stats-json` writes the same figures on exit.

## Measurement: the pixel marker

The WebRTC path forwards pixels only, so `--marker` writes the identity of every delivered frame into the frame itself: three rows of 32 black-or-white cells in the bottom-left corner (`marker.py`), carrying the frame id, the scene ordinal whose render produced the image, the frame class (simulated, interpolated, repeat) and the last pointer position the render submit for that ordinal consumed, guarded by a checksum. Generated frames carry the ordinal of the simulated frame they lead to.

A client that reads the marker from its decoded video can count unique frames per class, measure delivery intervals, and time input against the render that saw it: it moves the pointer to a unique position, timestamps the move, and waits for the first frame whose marker echoes that position. A queued or repeated older image can never be counted as the response. `examples/webrtc_client/playwright_client.py --marker-capture S --marker-echo N` does exactly this in Chrome; the SHM tests read the same marker with the Python decoder.

With frame generation on, the first frame to echo an input is normally the interpolated frame that leads to the simulated one (both carry the parent's ordinal), so the client reports two latencies: to the first echo and to the simulated frame. The numbers are in the comparison below.

## Qualification

### Four-way comparison

`tools/compare.py` runs the same scene, camera path and resolution through five variants for a fixed duration each, with an idle cooldown between variants, and combines the application's statistics, the GPU clock range sampled during the run, and, for the streaming variants, what a real Chrome received (pixel marker: unique frames, interval, latency by pointer echo; receiver statistics: decoded rate, bitrate). Measurement starts after startup, so shader compilation is excluded.

1920x1080, 60 Hz delivered, robot scene, NVIDIA RTX PRO 5000 Blackwell Laptop GPU, driver 595.97, ovrtx 0.5.0, ovui 0.2.0, 30 s per variant, 60 s cooldown, Chrome on the same machine (loopback, so no network cost), 20 pointer echoes per streaming variant:

| variant | render steps/s | presents/s | step wall ms | render-to-ready ms | ui tick ms | stream_video ms | missed / drops | client unique / decoded fps | latency p50 / p95 ms | to simulated frame p50 ms | kbps |
|---|---|---|---|---|---|---|---|---|---|---|---|
| renderer only | 59.9 | 59.9 | 16.3 | 0.6 | - | - | 0 / 0 | - | - | - | - |
| renderer + ovui chrome | 59.7 | 59.7 | 16.3 | 0.5 | 0.2 | - | 5 / 0 | - | - | - | - |
| streaming, FG off, 60 Hz base | 59.7 | 59.7 | 16.5 | 5.9 | 0.2 | 0.02 | 5 / 0 | 59.8 / 59.8 | 67 / 90 | 67 | 11761 |
| streaming, FG on, 30 Hz base | 30.0 | 59.9 | 16.2 | 0.5 | 0.2 | 0.02 | 0 / 0 | 60.0 / 60.0 | 59 / 84 | 81 | 12801 |
| streaming, FG off, 30 Hz base | 29.8 | 29.8 | 33.0 | 0.5 | 0.4 | 0.10 | 1 / 0 | 29.9 / 29.9 | 93 / 101 | 93 | 7606 |

What the table says:

- **Streaming costs the renderer nothing measurable.** Render step wall time is 16.3 ms in every 60 Hz variant, with or without the chrome, with or without a server. The chrome adds a 0.2 ms ovui tick; `stream_video` returns in 0.02 ms because the frame is ready before it is submitted.
- **Every delivered frame reaches the client.** The receiver decodes the same rate the application presents, with zero drops and no unreadable markers, in all three streaming variants.
- **Frame generation halves renderer load at the same delivered rate** (30 steps/s for 60 presents/s) and, against FG off at the same 30 Hz base, delivers twice the frames with a first visible response about 34 ms earlier (p50 59 versus 93 ms). The fully simulated frame arrives at about the same time in both (81 versus 93 ms), so generation does not delay the simulated response on this path; it adds an earlier interpolated one.
- **Against a 60 Hz base with FG off**, generation moves the simulated response later by about 14 ms (81 versus 67 ms) while freeing half the renderer. That is the trade the default profile makes, and the reason FG is a runtime toggle.

The render-to-ready time is higher in the 60 Hz FG-off variant (5.9 ms) because each frame is presented right after its step completes, whereas the other variants have slack between the step and the present.

Runs without a cooldown on this laptop are not comparable: power and thermal capping from one variant carries into the next (SM clock down to 180 MHz), the ovui tick and `stream_video` inflate to 30 ms each waiting on the slow GPU, and the present queue starts dropping. The GPU clock range in the tool's output is what makes such a run recognisable.

### Frame-generation profiles

`tools/qualify_fg.py` runs four profiles for a fixed duration each on a local SHM stream (frame generation off and on, at 60 Hz and 120 Hz delivered), collects each run's statistics and the capability report, and writes `qualification.json` plus a Markdown table with run averages, the last-second rates, missed steps, drops, and the GPU clock range and throttle reasons sampled while each profile ran. `--require-fg` makes it fail when a profile that requests generation did not engage on a GPU that can run it (Ada, compute 8.9, or newer); on an older GPU the results are recorded, the GPU is named in the log, and the requirement is reported as not applicable. Each profile also keeps the renderer's own log lines about NGX and DLSS (`--renderer-log`), so a non-engagement on a capable GPU shows the renderer's reason.

Recorded so far, 1920x1080, robot scene, NVIDIA RTX PRO 5000 Blackwell Laptop GPU, driver 595.97, ovrtx 0.5.0, 20 s per profile, run averages. The laptop reports power-cap and thermal throttling during every profile (SM clock sampled between 190 and 2120 MHz), so the renderer-bound rows vary from run to run and are indicative only; the primary profile held its numbers on every run.

| profile | frames/step | steps/s | presents/s | missed steps | drops | result |
|---|---|---|---|---|---|---|
| FG on, 60 Hz delivered (primary) | 2 | 30 | 60 | 0 | 0 | engaged, clean |
| FG off, 60 Hz | 1 | 56-57 | 56-57 | 58-69 | 0 | renderer at the 60 Hz budget |
| FG on, 120 Hz delivered (high-rate) | 2 | 26-46 | 53-91 | 90-177 | 0 | engaged; the GPU cannot hold a 60 Hz base at 1080p |
| FG off, 120 Hz | 1 | 41-55 | 41-55 | 800-1100 | 0 | renderer-bound |

With the chrome on (default), a 45 s run at the primary profile delivered 2694 frames from 1348 steps with 0 drops, 1 missed step and 2 repeats; the ovui tick and export add well under a millisecond per frame on this GPU.

Frame generation engaged on this GPU although nvidia-smi reports no active display on it; the capability report records the display state and the app verifies engagement by frame count rather than assuming it.

## Troubleshooting

- **"Frame generation requested but not active."** The renderer returned one frame per step. Check the printed list: real-time path tracing render mode, `LdrColor` as the only render var on the RenderProduct (an SPG output disables generation), both dimensions at least 126, an Ada or newer GPU, a supported driver. The capability report records the display state nvidia-smi saw; generation engaged here on a GPU with no active display, so treat that as diagnostic, not a rule.
- **`stream_video: ... allocated from a CUDA memory pool`** on `--resizable`. The frame ring is allocated outside Warp's pool by this example; the message means another producer path fed a pool buffer to a resizable stream. Allocate with `cudaMalloc` or under `wp.ScopedMempool(device, False)`.
- **Many missed steps, delivered rate below target.** The renderer cannot hold the base rate at this resolution; the loop keeps presenting the last frame. Lower `--size`, drop `--output-hz` to 60, or turn generation on so the base rate halves. On a laptop, check `nvidia-smi -q -d PERFORMANCE` for power or thermal capping first; `tools/qualify_fg.py` records the clock range for that reason.
- **Black or blue-tinted video.** Blue tint means RGBA reached the encoder; every frame goes through the swizzle here, so it points at a modified pipeline. Black video with a connected client usually means the encoder is on a different GPU from the frames; pass `--cuda-device` matching the render GPU.
- **`omni.ui` import fails on Windows.** The ovui wheel needs its `omni/ui_scene` directory and a CUDA 12 runtime DLL on the search path; `ui/host.py` adds both from the active environment. Run `--no-ui` to confirm the rest of the stack.
- **Browser connects but never shows video.** On a resizable stream that is the memory-pool failure above (the encoder tears down after the first frames). Otherwise use `examples/webrtc_client/playwright_client.py --headed` to watch the client and `--app-stream-check` to confirm state messages flow.

### Zero-copy experiment

`--zero-copy` (experimental) skips the device copy: the mapped ovrtx output is converted and composited in place and streamed directly, with the mapping held until `stream_video` returns. It works on the renderer's output, so it runs with `--no-ui` and without `--resizable`; `tools/compare.py --variants stream-fg-off-zc,stream-fg-on-zc` runs it that way. Measured against the copy path in a paired run (same conditions as above, warm GPU, so absolute numbers are a little worse than the table):

| | copy path | zero-copy |
|---|---|---|
| render-to-ready ms, FG off | 2.8 | 2.1 |
| render-to-ready ms, FG on | 0.6 | 0.6 |
| pipeline CPU ms (SHM run) | 0.60 | 0.46 |
| latency p50 ms, FG off | 72 | 67 |
| latency p50 ms, FG on | 85 | 82 |
| client decoded fps | 59.8 / 59.9 | 59.7 / 59.3 |

The 1080p device copy costs about 0.1 ms of GPU time and 0.15 ms of CPU issue time per frame; the latency differences are inside the run-to-run spread of this laptop. What the copy buys is ownership: the ring is application memory with known lifetime and a fixed maximum-size allocation (which resizable streams need), the renderer's buffer is never written to, and a repeated frame can be re-composited without tracking whether its pixels were already swizzled. The copy stays the default; the switch remains so the measurement can be repeated.

## Beyond this example

- **C++ twin.** The module boundaries map one to one: `RenderDriver` becomes a real render thread with the same submit/poll contract, `FramePipeline` keeps the ring and CUDA events, `StreamLayer` uses the C API with the same callback-before-start rule, and the frame contract in `contract.py` is the specification for the classification code. `ovstream_utils` has C and C++ pacing twins under `include/ovstream_utils`.
- **Promotion.** The package is self-contained under this directory (no imports from the rest of the SDK tree beyond the `ovstream` and `ovstream_utils` wheels), so it can move to a standalone repository unchanged if the reference-implementation program calls for one.
- **Platforms.** Verified on Windows x86_64 with the qualification table above. Linux x86_64 runs through the opt-in CI lane once a GPU runner with the wheels is available; Linux aarch64 (DGX Spark) is untested pending ovui and ovrtx wheels for that platform.

## Known limits

- The ovui standalone backend writes an `imgui.ini` in the working directory.
- The exported ovui image is opaque, so the chrome cannot be blended over the scene; the viewport is a region of the frame the scene fills exactly.
- The browser page shows the state messages in its console only; it has no controls of its own yet.
- RTSP is fixed-size. With `--resizable` and an RTSP server in the same run, RTSP rejects every frame after the first resize and resumes only when the frame returns to the configured size; the other transports keep streaming.
- On a resizable WebRTC stream the transport gives no completion signal for a submitted frame. A ring slot is owned until `stream_video` has returned for every server, which is exact for fixed-size streams (the SDK stages the frame) but only a heuristic for resizable ones, where the four-slot ring depth is what protects a released slot. A per-frame completion signal is SDK work; until it exists `--zero-copy` is refused together with `--resizable`.

## Layout

```text
main.py                    entry point; sets process environment before any CUDA import
app_stream/
  app.py                   composition root and the per-tick frame path
  config.py                command-line options
  capabilities.py          startup capability report
  scene.py                 ovstage stage, RenderProduct authoring, per-frame writes
  camera.py                orbit camera and input handling
  render.py                ovrtx stepping and frame classification
  frames.py                CUDA stream, BGRA8 ring, swizzle kernel
  streaming.py             ovstream servers and publishing
  input_bridge.py          worker-thread events drained on the main thread
  pacing.py                cadence and rolling statistics
  contract.py              pure frame-contract logic (unit-tested, no GPU imports)
  marker.py                frame-identity pixel marker: encode/decode (unit-tested) and the draw kernel
  ui/host.py               headless ovui backend, pointer injection, CUDA export of the chrome
  ui/workbench.py          the application chrome: top bar, dock sections, status bar
  ui/layout.py             chrome regions and viewport placement (unit-tested)
  ui/keymap.py             ovstream key codes to ImGui keys (unit-tested)
  assets/render_products.usda   root-layer template for the streaming RenderProduct
tools/qualify_fg.py        frame-generation qualification over the profile matrix
tools/compare.py           four-way performance comparison (renderer, +ovui, streaming FG off, FG on)
tools/snapshot.py          save one streamed frame as a PNG (attach to an SHM stream)
```

## Notes

- With ovrtx 0.5 an extra render var on the RenderProduct disables frame generation, so the streaming product requests only `LdrColor` and the BGRA8 conversion happens in the application. `../ovrtx_stream/` shows the alternative, an SPG swizzle node producing BGRA8 renderer-side, for the case without frame generation.
- Frame generation adds about one base-frame interval of latency to the delivered stream (the interpolated frame is shown before the simulated frame it leads to). At a 30 Hz base that is about 33 ms; compare `--fg off` at the same delivered rate.
- The renderer picks up a newly populated RenderProduct one step after the stage change; the application settles before reading it.
- The first run compiles and caches ovrtx shaders and takes noticeably longer than later runs.
