# ovrtx + ovstream (Python)

**The headline composition demo.** Loads the stock `Robot-OVRTX` USD scene with [ovrtx](https://github.com/NVIDIA-Omniverse/ovrtx), renders it with an auto-orbiting camera, and streams the resulting frames through ovstream.

The point of this example is that ovstream knows nothing about ovrtx and vice-versa — they're independent libraries composed by a 200-line script. Replace ovrtx with any other CUDA-buffer producer (Warp, your own renderer, a video decoder) and the streaming half doesn't care.

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

- Two OV libraries composed by a single app, with no Kit / Carbonite involved.
- ovrtx renders into a render product output that the script maps to a CUDA buffer.
- The same CUDA buffer is handed to `ovstream.Server.stream_video()` every frame — zero-copy.
- `ServerConfig(cuda_device=0, cuda_context=int(wp.get_device("cuda:0").context))` pins the encoder to the GPU the frames are produced on **and** hands it the producer's CUDA context. On a multi-GPU host the default encoder GPU is the display GPU (stream connects but never decodes); and because ovrtx renders in its own CUDA context, `cuda_device` alone is not enough — without `cuda_context` StreamSDK fails the encode with `CUDA error invalid argument`.
- Input events from the WebRTC client are routed back into the ovrtx camera transform via `ovstream.Server.on_input`.
- Pacing via `ovstream_utils.Loop`.

If you're integrating ovstream into your own renderer, this example is the canonical reference for the producer side.
