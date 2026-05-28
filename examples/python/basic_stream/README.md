# Basic Stream (Python)

The canonical ovstream "hello world" in Python: allocate a CUDA buffer via ctypes-bound `cudart`, animate a BGRA8 fill, and stream it via WebRTC (the default), RTSP, the low-latency native protocol, SHM, or any combination simultaneously.

## Prerequisites

- Python 3.8 or newer.
- [uv](https://docs.astral.sh/uv/) (recommended) or pip.
- An NVIDIA GPU with a compatible driver.

## Run with uv

```bash
uv run main.py             # WebRTC on signal port 49100
uv run main.py rtsp        # RTSP on port 8554
uv run main.py shm:demo    # SHM with stream name "demo"
uv run main.py webrtc rtsp # both at once
```

`uv` resolves `ovstream` from PyPI on first run and caches it.

## Run with pip

```bash
pip install ovstream
python main.py
```

## View the stream

- **WebRTC** — open [`../../webrtc_client/index.html`](../../webrtc_client/index.html) in a browser, enter `127.0.0.1:49100`.
- **RTSP** — `ffplay rtsp://localhost:8554/stream`, or VLC.
- **SHM** — `python ../local_stream/main_viewer.py demo` (requires `opencv-python`).
- **Native** — requires a native StreamSDK client; no browser equivalent.

## What it shows

- `ovstream.initialize()` / `ovstream.shutdown()` ref-counted lifecycle.
- `ovstream.Server(ServerType.X)` for each requested transport.
- Connection callback on all transports; message / input callbacks on every transport with a reverse channel (WebRTC, native, SHM — not RTSP).
- `ovstream.ServerConfig` with per-protocol port / stream-name overrides.
- A single shared CUDA buffer fed to every server in the streaming loop.
- Optional `ovstream_utils.Loop` for clean fps-paced rendering.
- Graceful Ctrl+C shutdown.
