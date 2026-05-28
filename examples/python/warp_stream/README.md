# Warp Stream (Python)

Same idea as [`basic_stream`](../basic_stream/README.md), but the per-frame CUDA fill is computed by an [NVIDIA Warp](https://developer.nvidia.com/warp-python) kernel instead of `cudart.cudaMemset`. The Warp kernel writes directly into a `wp.array3d(dtype=wp.uint8)`; `ovstream.VideoFrame.from_dlpack(...)` hands the buffer to the server zero-copy via DLPack, using `VideoInput.TENSOR`.

This is the canonical "produce frames in Warp, stream them with ovstream" pattern — the simplest composition of two OV libraries (after the ovrtx_stream example).

## Prerequisites

- Python 3.8 or newer.
- An NVIDIA GPU with a compatible driver.
- [uv](https://docs.astral.sh/uv/) (recommended).

## Run

```bash
uv run main.py             # WebRTC on signal port 49100
uv run main.py rtsp        # RTSP on port 8554
uv run main.py shm:demo    # SHM with stream name "demo"
uv run main.py webrtc rtsp # multiple transports at once
```

`uv` installs both `ovstream` and `warp-lang` from PyPI on first run.

## View the stream

See [`basic_stream`'s README](../basic_stream/README.md) — same client options apply.

## What it shows

- A `@wp.kernel`-decorated function (`draw_gradient`) writing pixels in parallel.
- Zero-copy frame submission via `ovstream.VideoFrame.from_dlpack(buf)` against `VideoInput.TENSOR` — Warp's tensor and ovstream's `VideoFrame` share the same device buffer through the DLPack capsule.
- Same multi-transport spec parser as `basic_stream`, so you can compare the Warp and ctypes-cudart approaches side-by-side.
