# Basic Stream (C)

The canonical ovstream "hello world": allocate a CUDA buffer, animate a BGRA8 gradient, and stream it via WebRTC (the default), RTSP, the low-latency native protocol, SHM, CUDASHM, or any combination simultaneously.

## Prerequisites

### Linux

```bash
sudo apt-get install build-essential cmake
```

### Windows

[Visual Studio 2019 or newer](https://visualstudio.microsoft.com/downloads/).

A working CUDA toolkit (for `nvcc`) is required on both platforms.

## Build

```bash
cmake -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build --config Release
```

`ovstream_fetch()` (defined in [`../cmake/ovstream.cmake`](../cmake/ovstream.cmake)) downloads the matching ovstream release archive from GitHub Releases automatically. If you have a local build of ovstream you'd rather use, pass `-DOVSTREAM_LOCAL_PACKAGE_DIR=<path-to-extracted-zip>` to the first cmake invocation.

## Run

Linux:

```bash
./build/basic_stream                  # WebRTC on signal port 49100
./build/basic_stream rtsp             # RTSP on port 8554
./build/basic_stream shm:demo         # SHM (host-resident) with stream name "demo"
./build/basic_stream cudashm:demo     # CUDASHM (GPU-resident) with stream name "demo"
./build/basic_stream webrtc rtsp      # both at once
```

Windows:

```pwsh
.\build\Release\basic_stream.exe
.\build\Release\basic_stream.exe rtsp
.\build\Release\basic_stream.exe shm:demo
.\build\Release\basic_stream.exe cudashm:demo
```

## View the stream

- **WebRTC** — open [`../../webrtc_client/index.html`](../../webrtc_client/index.html) in a browser, enter `127.0.0.1:49100`.
- **RTSP** — `ffplay rtsp://localhost:8554/stream`, or VLC.
- **SHM** — `python ../../python/local_stream/main_viewer.py demo` from the Python examples.
- **CUDASHM** — `python ../../python/local_stream/main_cudashm_viewer.py demo`. Must run in a separate process from the producer: CUDA forbids `cudaIpcOpenMemHandle` in the process that called `cudaIpcGetMemHandle`.
- **Native** — requires a native StreamSDK client; no browser equivalent.

## What it shows

- `ovstream_initialize` / `ovstream_shutdown` ref-counted lifecycle.
- `ovstream_create_server` for each requested transport (RTSP / WEBRTC / NATIVE / SHM / CUDASHM).
- Connection callback on all transports; message / input callbacks on every transport with a reverse channel (WebRTC, native, SHM, CUDASHM — not RTSP).
- `ovstream_config_defaults` + per-protocol port and stream-name overrides.
- A single shared CUDA buffer fed to every server in the streaming loop.
- Graceful shutdown on Ctrl+C.
