# ovrtx + ovstream (Python)

**The minimal composition example.** Loads the stock `Robot-OVRTX` USD scene through [ovstage](https://github.com/NVIDIA-Omniverse/ovstage), renders it with [ovrtx](https://github.com/NVIDIA-Omniverse/ovrtx) using an auto-orbiting camera, and streams the result through ovstream.

The point of this example is that ovstream knows nothing about ovrtx and vice-versa; they are independent libraries composed by a short script. Replace ovrtx with any other CUDA-buffer producer (Warp, your own renderer, a video decoder) and the streaming half doesn't care. For the full reference implementation with a UI overlay, decoupled render cadence, and DLSS Frame Generation, see `../app_stream/`.

## Prerequisites

- Python 3.10–3.13 (ovrtx's supported range).
- An NVIDIA RTX-capable GPU.
- [uv](https://docs.astral.sh/uv/) (recommended).

## Run

```bash
uv run main.py
```

Default: WebRTC on signal port 49100. Pass `rtsp`, `native`, `shm`, or `cudashm` to override. For `cudashm`, attach the GPU viewer in a separate shell: `uv run ../local_stream/main_cudashm_viewer.py <stream_name>`.

## Interact with the stream

Open the [`../../webrtc_client/index.html`](../../webrtc_client/index.html) browser client and connect to `127.0.0.1:49100`. Once connected:

- **Left-drag** — take camera control (overrides the auto-orbit).
- **Mouse wheel** — zoom in / out.
- **Any key** — resume auto-orbit.

RTSP clients see the auto-orbit only (RTSP has no input channel).

## What it shows

- Three OV libraries composed by a single app, with no Kit / Carbonite involved: ovstage owns the scene, ovrtx renders, ovstream streams.
- `stream_scene.usda` sublayers the robot scene and adds the RenderProduct `/Render/Stream`. Its `BgraColor` render var is the renderer's `LdrColor` output passed through the SPG `Swizzle` node with `"zyxw"`, so ovrtx emits the BGRA8 layout ovstream expects and the script contains no pixel-format kernel.
- Each frame the script maps `BgraColor` to CUDA and hands the mapped pointer to `ovstream.Server.stream_video()`, passing the mapping's producer-done event as the `CudaSync` hint so the encoder waits for the render on the GPU. ovstream stages the frame before `stream_video` returns, so the mapping is released straight afterwards.
- The camera is animated by writing the camera prim's `omni:xform` through ovstage under a fresh ordinal each frame, then stepping the renderer at that ordinal.
- `ServerConfig(cuda_device=0)` pins the encoder to the GPU the frames are produced on; ovrtx renders in that device's primary CUDA context, so no `cuda_context` is needed here. A producer that renders in its own context must also pass `cuda_context` (see `../warp_stream/`).
- Input events from the WebRTC client are routed back into the camera via `ovstream.Server.on_input`.
- Pacing via `ovstream_utils.Loop`.

## Notes

- The RenderProduct is defined in the root layer on purpose. In ovrtx 0.5 a RenderProduct added through `ovstage.population.add_usd_reference_from_string` does not resolve its camera relationship reliably; a root layer that sublayers the scene does.
- The renderer picks up a newly populated RenderProduct one step after the stage change, so the script steps until the product appears before reading its resolution.
- The first run compiles and caches ovrtx shaders and takes noticeably longer than later runs.
