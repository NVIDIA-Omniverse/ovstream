# Warp Stream (Python)

Same idea as [`basic_stream`](../basic_stream/README.md), but the per-frame CUDA fill is computed by an [NVIDIA Warp](https://developer.nvidia.com/warp-python) kernel instead of `cudart.cudaMemset`. The Warp kernel writes directly into a `wp.array3d(dtype=wp.uint8)`; `ovstream.VideoFrame.from_dlpack(...)` hands the buffer to the server zero-copy via DLPack, using `VideoInput.TENSOR`.

This is the canonical "produce frames in Warp, stream them with ovstream" pattern — the simplest composition of two OV libraries (after the ovrtx_stream example).

## Prerequisites

- Python 3.8 or newer.
- An NVIDIA GPU with a compatible driver.
- [uv](https://docs.astral.sh/uv/) (recommended).

## Run

```bash
uv run main.py                  # WebRTC on signal port 49100
uv run main.py rtsp             # RTSP on port 8554
uv run main.py shm:demo         # SHM (host-resident) with stream name "demo"
uv run main.py cudashm:demo     # CUDASHM (GPU-resident) with stream name "demo"
uv run main.py webrtc rtsp      # multiple transports at once
```

`uv` installs both `ovstream` and `warp-lang` from PyPI on first run.

## View the stream

- **WebRTC** — open [`../../webrtc_client/index.html`](../../webrtc_client/index.html) in a browser, enter `127.0.0.1:49100`.
- **RTSP** — `ffplay rtsp://localhost:8554/stream`, or VLC.
- **SHM** — `python ../local_stream/main_viewer.py demo` (requires `opencv-python`).
- **CUDASHM** — `python ../local_stream/main_cudashm_viewer.py demo` (requires `opencv-python` + `numpy`). Must run in a separate process from the producer: CUDA forbids `cudaIpcOpenMemHandle` in the process that called `cudaIpcGetMemHandle`.
- **Native** — requires a native StreamSDK client; no browser equivalent.

## What it shows

- A `@wp.kernel`-decorated function (`draw_gradient`) writing pixels in parallel.
- Zero-copy frame submission via `ovstream.VideoFrame.from_dlpack(buf)` against `VideoInput.TENSOR` — Warp's tensor and ovstream's `VideoFrame` share the same device buffer through the DLPack capsule.
- `ServerConfig(cuda_device=0, cuda_context=int(wp.get_device("cuda:0").context))` pins every backend to the GPU the Warp buffer lives on and passes Warp's CUDA context. The default encode/copy device may differ on multi-GPU hosts; and since Warp allocates in its own context, WebRTC/native need `cuda_context` too or StreamSDK fails the encode.
- Same multi-transport spec parser as `basic_stream`, so you can compare the Warp and ctypes-cudart approaches side-by-side.
