# Starfield Stream (C)

A richer streaming example: a CUDA-accelerated animated starfield plus a looping 48 kHz stereo PCM audio track. Mouse input hides stars near the cursor (WebRTC / native / SHM / CUDASHM); audio is silently dropped on transports that don't support it (RTSP, SHM, CUDASHM).

This example also demonstrates `ovstream_utils::Loop`, the optional frame-pacer bundled with ovstream as a header-only utility (`<ovstream_utils/loop.hpp>`).

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

The CMakeLists copies `data/audio_sample_48khz.pcm` next to the built executable as a post-build step.

## Run

Linux:

```bash
./build/starfield_stream                  # WebRTC default
./build/starfield_stream rtsp             # RTSP
./build/starfield_stream shm:stars        # SHM (host-resident) with stream name "stars"
./build/starfield_stream cudashm:stars    # CUDASHM (GPU-resident) with stream name "stars"
./build/starfield_stream webrtc rtsp      # combined
```

Windows:

```pwsh
.\build\Release\starfield_stream.exe
```

## View the stream

- **WebRTC** — open [`../../webrtc_client/index.html`](../../webrtc_client/index.html) in a browser, enter `127.0.0.1:49100`.
- **RTSP** — `ffplay rtsp://localhost:8554/stream`, or VLC.
- **SHM** — `python ../../python/local_stream/main_viewer.py stars` from the Python examples.
- **CUDASHM** — `python ../../python/local_stream/main_cudashm_viewer.py stars`. Must run in a separate process from the producer: CUDA forbids `cudaIpcOpenMemHandle` in the process that called `cudaIpcGetMemHandle`.
- **Native** — requires a native StreamSDK client; no browser equivalent.

## What it shows

- Multi-transport streaming with simultaneous audio + video.
- Mouse input callback that mutates per-frame CUDA state (avoidance zones in the starfield).
- `ovstream_utils::Loop` for clean fps-paced rendering with `dt`, frame index, and FPS-stats reporting.
- Asset shipping (the audio PCM file alongside the binary) via a CMake `POST_BUILD` step.
