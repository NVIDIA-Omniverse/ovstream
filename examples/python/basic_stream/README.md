# Basic Stream (Python)

The canonical ovstream "hello world" in Python: allocate a CUDA buffer via ctypes-bound `cudart`, animate a BGRA8 fill, and stream it via WebRTC (the default), RTSP, the low-latency native protocol, SHM, CUDASHM, or any combination simultaneously.

## Prerequisites

- Python 3.8 or newer.
- [uv](https://docs.astral.sh/uv/) (recommended) or pip.
- An NVIDIA GPU with a compatible driver.

## Run with uv

```bash
uv run main.py                  # WebRTC on signal port 49100
uv run main.py rtsp             # RTSP on port 8554
uv run main.py shm:demo         # SHM (host-resident) with stream name "demo"
uv run main.py cudashm:demo     # CUDASHM (GPU-resident) with stream name "demo"
uv run main.py webrtc rtsp      # both at once
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
- **CUDASHM** — `python ../local_stream/main_cudashm_viewer.py demo` (requires `opencv-python` + `numpy`). Must run in a separate process from the producer: CUDA forbids `cudaIpcOpenMemHandle` in the process that called `cudaIpcGetMemHandle`.
- **Native** — requires a native StreamSDK client; no browser equivalent.

## What it shows

- `ovstream.initialize()` / `ovstream.shutdown()` ref-counted lifecycle.
- `ovstream.Server(ServerType.X)` for each requested transport.
- Connection callback on all transports; message / input callbacks on every transport with a reverse channel (WebRTC, native, SHM, CUDASHM — not RTSP).
- `ovstream.ServerConfig` with per-protocol port / stream-name overrides.
- Optional `Server.set_webrtc_ice_servers(...)` for NAT traversal — see below.
- A single shared CUDA buffer fed to every server in the streaming loop.
- Optional `ovstream_utils.Loop` for clean fps-paced rendering.
- Graceful Ctrl+C shutdown.

## STUN / TURN credentials (WebRTC, native)

Connecting a browser to the server from a different network requires STUN (for NAT discovery) and sometimes TURN (for relay when direct paths are blocked). Supply both via `OVSTREAM_ICE_SERVERS`, a `|`-separated list of `urls[,username,credential]` entries:

```bash
OVSTREAM_ICE_SERVERS="stun:stun.l.google.com:19302" uv run main.py
OVSTREAM_ICE_SERVERS="stun:stun.l.google.com:19302|turn:turn.example.net:3478,alice,secret" uv run main.py
```

The STUN URL above is Google's free public STUN server. It is widely used for development and testing, but Google provides it as an un-monitored service with no SLA, so commercial or mission-critical deployments should run their own dedicated STUN / TURN servers. There is also no good free public TURN service — TURN relays user bandwidth, so it's never free. For real-world testing run [coturn](https://github.com/coturn/coturn) locally:

```bash
docker run -p 3478:3478 coturn/coturn -a -u user:pass -r ovstream-test
```

or use credentials from a TURN-as-a-service provider (Twilio Network Traversal Service, Cloudflare Calls, Xirsys). The setter is also live-updatable: call `server.set_webrtc_ice_servers(...)` again after `start()` to refresh time-limited TURN tokens without dropping the stream.
