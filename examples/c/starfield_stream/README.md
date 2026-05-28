# Starfield Stream (C)

A richer streaming example: a CUDA-accelerated animated starfield plus a looping 48 kHz stereo PCM audio track. Mouse input hides stars near the cursor (WebRTC / native / SHM); audio is silently dropped on transports that don't support it (RTSP, SHM).

This example also demonstrates `ovstream_utils::Loop`, the optional frame-pacer bundled with ovstream as a header-only utility (`<ovstream_utils/loop.hpp>`).

## Prerequisites

Same as [`basic_stream`](../basic_stream/README.md).

## Build

```bash
cmake -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build --config Release
```

The CMakeLists copies `data/audio_sample_48khz.pcm` next to the built executable as a post-build step.

## Run

Linux:

```bash
./build/starfield_stream             # WebRTC default
./build/starfield_stream rtsp        # RTSP
./build/starfield_stream shm:stars   # SHM stream name "stars"
./build/starfield_stream webrtc rtsp # combined
```

Windows:

```pwsh
.\build\Release\starfield_stream.exe
```

## View the stream

Same client options as `basic_stream` — see that example's README.

## What it shows

- Multi-transport streaming with simultaneous audio + video.
- Mouse input callback that mutates per-frame CUDA state (avoidance zones in the starfield).
- `ovstream_utils::Loop` for clean fps-paced rendering with `dt`, frame index, and FPS-stats reporting.
- Asset shipping (the audio PCM file alongside the binary) via a CMake `POST_BUILD` step.
