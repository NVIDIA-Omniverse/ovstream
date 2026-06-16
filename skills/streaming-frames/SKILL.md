---
name: streaming-frames
description: Submitting video frames to ovstream (raw CUDA BGRA8 or pre-encoded bitstreams). Use when user asks how to push frames, the video frame format, pitch alignment, or pre-encoded passthrough.
---

# Streaming Frames

## Overview

`stream_video` / `ovstream_stream_video` is the per-frame entry point. It accepts a `VideoFrame` / `ovstream_video_frame_t` descriptor that **discriminates** on which fields are non-zero:

- **`pitch_bytes > 0`** → raw CUDA BGRA8 buffer. `buffer` points at GPU memory; the SDK reads it directly and encodes via NVENC. This is the path 95% of apps take.
- **`size_bytes > 0`** → pre-encoded bitstream (H.264 / H.265 / AV1, or a `CUSTOM` GStreamer-pipeline payload on RTSP). `buffer` is a **host** pointer to the encoded payload; the SDK packetizes and forwards without re-encoding. (Unlike raw CUDA, pre-encoded paths do not accept GPU-resident buffers.)
- **Both `pitch_bytes == 0` and `size_bytes == 0`** → `TENSOR` input: `buffer` is a `DLTensor*` (DLPack) and `width` / `height` are also 0; the SDK reads shape, dtype, stride, and device from the DLTensor itself. Construct via `VideoFrame.from_dlpack(obj)` in Python (any framework that exposes `__dlpack__` — Warp, PyTorch, JAX, CuPy). The server's configured `video_input` must be `TENSOR`.

The exception above aside, exactly one of `pitch_bytes` / `size_bytes` must be non-zero, and the choice must match the server's configured `video_input`.

## Python

> **Source:** `examples/python/basic_stream/main.py` snippet `stream-loop`

Construct a `VideoFrame` once, reuse the same buffer pointer across frames, mutate the buffer contents in place between calls.

```python
frame = ovstream.VideoFrame(
    buffer=cuda_ptr.value,   # device pointer (int)
    width=1920, height=1080,
    pitch_bytes=1920 * 4,    # BGRA = 4 bytes/px
)

for _ in range(num_frames):
    # ... fill the CUDA buffer with this frame's content ...
    try:
        server.stream_video(frame)
    except ovstream.OvstreamError:
        pass  # no client connected yet, etc.
```

## C

> **Source:** `examples/c/basic_stream/main.cu` snippet `stream-loop`

## Buffer lifetime contract

`stream_video` **stages the frame data into server-owned memory before returning** on every backend (RTSP / WebRTC / native / SHM / CUDASHM). The caller may reuse or free `buffer` (including a stack buffer or a per-iteration `std::vector`) as soon as the call returns. Reusing a single long-lived buffer across frames is still the recommended pattern because it avoids per-frame allocation, but per-frame allocations are equally safe. Producers that want to skip the staging copy and overlap their next render with the encoder's read should pass a CUDA event via `frame.sync.wait_event` — backends host-block on it before staging, so the producer's render can chain after the wait.

## Pitch

`pitch_bytes` is the stride of one row, not `width * 4`. NVENC tolerates the buffer being padded for DMA alignment — use `cudaMallocPitch` to get a pitch that NVENC likes.

> **Source:** `examples/c/basic_stream/main.cu` snippet `cuda-buffer-alloc`

## Pre-encoded passthrough

If you already have encoded NAL units / OBUs (e.g. from a video decoder, a recorded file, or an upstream encoder), set `video_input` in `ServerConfig` to one of `H264 / H265 / AV1` at server start, then submit frames with `size_bytes` populated instead of `pitch_bytes`. The SDK forwards the bitstream as-is.

> **Source:** `examples/c/pre_encoded_stream/main.cpp` snippet `configure-pre-encoded`
>
> Followed by: `examples/c/pre_encoded_stream/main.cpp` snippet `pre-encoded-loop`

RTSP and WebRTC both support pre-encoded H.264/H.265; WebRTC additionally supports AV1. RTSP does not currently support AV1. Neither SHM nor CUDASHM supports pre-encoded — both carry raw BGRA8 only.

## Per-frame metadata (optional)

`ovstream_video_frame_t` has three optional metadata fields — `metadata` (blob pointer), `metadata_size` (bytes), and `metadata_uuid` (16-byte type identifier). Populate them on the frame descriptor you pass to the regular `stream_video` call; there are no separate `*WithMetadata` entry points.

- For RTSP pre-encoded streams, the SDK injects the metadata as SEI User Data Unregistered NAL units (ITU-T H.264/H.265).
- For RTSP raw-CUDA streams (encoder owns the bitstream) and on WebRTC / SHM / CUDASHM, metadata is currently ignored.

Use only if you need per-frame tags that traverse the wire.

## Key Types / Functions

| Python | C |
|--------|---|
| `ovstream.VideoFrame(buffer=..., width=..., height=..., pitch_bytes=...)` | `ovstream_video_frame_t` |
| `ovstream.VideoFrame.from_dlpack(obj)` (TENSOR input) | `ovstream_video_frame_t` with `buffer` set to a `DLTensor*` |
| `server.stream_video(frame)` | `ovstream_stream_video(server, &frame)` |
| `ovstream.VideoInput.{CUDA, TENSOR, CUSTOM, H264, H265, AV1}` | `OVSTREAM_VIDEO_INPUT_{CUDA, TENSOR, CUSTOM, H264, H265, AV1}` |

## Common Pitfalls

- `pitch_bytes` and `size_bytes` are mutually exclusive. Setting both is an error. The only frame shape where both are zero is `TENSOR` input (dimensions and stride come from the DLTensor).
- For BGRA8, `pitch_bytes >= width * 4`. Using `pitch_bytes = width * 4` is fine when `cudaMallocPitch` happens to align that way, but trust `cudaMallocPitch`'s returned pitch — don't hard-code.
- A non-`SUCCESS` `stream_video` return is normal early in a stream's life (no client connected yet) — backends mostly accept early frames silently for pipeline warm-up, but if they do surface a failure, log at verbose and continue.
- BGRA channel order, not RGBA. Any producer that emits RGBA needs to swizzle on the GPU before handing the buffer to ovstream. At the time of writing, ovrtx's `LdrColor` is RGBA8 — a producer-side BGRA8 path is in flight inside ovrtx; until that lands, [`examples/python/ovrtx_stream/main.py`](../../examples/python/ovrtx_stream/main.py) does the swizzle in the application layer between the renderer and ovstream.
- Audio frames go through `stream_audio` / `ovstream_stream_audio` (16-bit PCM, WebRTC/native only). RTSP, SHM, and CUDASHM don't support audio — `stream_audio` returns `OVSTREAM_API_NOT_SUPPORTED` (Python: `OvstreamError` with `.status == ApiStatus.NOT_SUPPORTED`) on those.
