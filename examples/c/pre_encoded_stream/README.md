# Pre-Encoded Stream (C)

Demonstrates ovstream's **pre-encoded video passthrough** path: instead of pushing raw CUDA BGRA8 frames, you push already-encoded H.264 / H.265 / AV1 bitstreams that ovstream packetizes and forwards without re-encoding.

This is the right path when your frames come from a hardware encoder, a recorded file, or an upstream network source — anything where the bytes on the wire are already a valid bitstream.

> [!WARNING]
> The synthetic H.264 frames this example emits are **not valid decodable video.** Clients (ffplay, VLC, etc.) will connect successfully but nothing will render. The point is to exercise the SDK's pre-encoded path end-to-end without pulling in NVENC as a dependency. In a real application, replace `generateFakeH264Frame()` with NVENC / x264 / hardware encoder output.

## Prerequisites

### Linux

```bash
sudo apt-get install build-essential cmake
```

### Windows

[Visual Studio 2019 or newer](https://visualstudio.microsoft.com/downloads/).

When built standalone via this subdirectory's `CMakeLists.txt` the example doesn't allocate any CUDA buffers and a CUDA toolkit is not strictly required. When built from the umbrella `examples/c/CMakeLists.txt` (which configures the CUDA language for sibling examples like `basic_stream`), CUDA is required.

## Build

```bash
cmake -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build --config Release
```

## Run

Linux:

```bash
./build/pre_encoded_stream
```

Windows:

```pwsh
.\build\Release\pre_encoded_stream.exe
```

The example starts an RTSP server on `rtsp://localhost:8554/stream` and pushes synthetic H.264 frames at 30 FPS.

## What it shows

- `cfg.video_input = OVSTREAM_VIDEO_INPUT_H264` — switching the server into pre-encoded mode.
- `ovstream_video_frame_t` with `size_bytes` populated (instead of `pitch_bytes`) — the discriminant that tells ovstream "this is a bitstream, not a CUDA buffer."
- Per-iteration `std::vector` payloads, relying on the lifetime contract in `ovstream_types.h` (`ovstream_stream_video` stages the bitstream into server-owned memory before returning, so the source vector can go out of scope on the next loop iteration).

For the raw-CUDA path, see [`basic_stream`](../basic_stream/README.md).
