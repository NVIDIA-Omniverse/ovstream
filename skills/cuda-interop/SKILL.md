---
name: cuda-interop
description: CUDA buffer allocation, pitch alignment, BGRA channel order, and ctypes-bound cudart usage. Use when user asks about CUDA interop, pitched allocations, frame layout, or zero-copy GPU integration.
---

# CUDA Interop

## Overview

ovstream consumes raw video frames as **BGRA8 CUDA buffers** directly from device memory. No CPU copy, no upload — the SDK reads the GPU pointer you hand it and encodes via NVENC.

The frame descriptor (`VideoFrame` / `ovstream_video_frame_t`) holds:

- `buffer` — a CUDA device pointer.
- `width`, `height` — pixel dimensions.
- `pitch_bytes` — row stride in bytes (≥ `width * 4`).

## Allocate with `cudaMallocPitch`

NVENC tolerates the buffer being row-padded for DMA alignment. Let CUDA pick the pitch:

> **Source:** `examples/c/basic_stream/main.cu` snippet `cuda-buffer-alloc`

`cudaMallocPitch(&buf, &pitch, widthBytes, height)` returns a device pointer plus the actual pitch CUDA picked (often `width * 4` rounded up to a 256 / 512-byte multiple). Pass that exact `pitch` into the `VideoFrame.pitch_bytes` field.

## Python: ctypes-bound cudart

The Python wheel bundles `cudart64_*.dll` / `libcudart.so` alongside `ovstream.dll`, so a Python app can use ctypes to call `cudaMalloc` / `cudaMemset` / `cudaFree` directly without any extra CUDA Python package. The example discovers the bundled CUDA runtime via `ovstream._bindings._find_library()` and loads it with ctypes.

> **Source:** `examples/python/basic_stream/main.py` snippet `bundled-cudart`

If you'd rather use a higher-level CUDA Python package, [`CuPy`](https://cupy.dev), [`PyCUDA`](https://documen.tician.de/pycuda/), or [`NVIDIA Warp`](https://developer.nvidia.com/warp-python) all expose `__cuda_array_interface__` or a `.data_ptr()` accessor that yields a raw device pointer — pass that as `VideoFrame.buffer`. See the `ovrtx_stream` example for a Warp-based version.

## BGRA, not RGBA

ovstream's CUDA video format is BGRA8 (blue first), not RGBA8. If your producer outputs RGBA, swizzle on the GPU before submitting. (At the time of writing, ovrtx's `LdrColor` render var is RGBA8 — a producer-side BGRA8 path is in flight; until that lands, the `ovrtx_stream` example does the swizzle itself.) Sketch:

```cpp
// CUDA kernel sketch
uint8_t* px = buffer + y * pitch + x * 4;
px[0] = rgba[2];  // B
px[1] = rgba[1];  // G
px[2] = rgba[0];  // R
px[3] = rgba[3];  // A
```

A future ovrtx release may emit BGRA8 directly, removing the swizzle step.

## Lifetime / re-use pattern

`stream_video` stages the frame data into server-owned memory before returning, so the caller may reuse or free the buffer immediately after the call returns. The recommended pattern is still **one long-lived CUDA buffer that you re-fill in place** each frame — it avoids per-frame allocator churn — but per-frame `cudaMalloc` / `cudaFree` is now also safe (the encoder never reads through the caller's pointer after return).

## Synchronization

`stream_video` does **not** synchronize internally by default — the caller is responsible for making sure the buffer's pixels are visible to the encoder when the call lands. Two patterns:

- **Caller pre-syncs.** Leave `frame.sync` zero-initialized (the default). The contract is "the buffer is safe to read on entry to `stream_video`". `cudaDeviceSynchronize` between your render kernel and `stream_video` is sufficient (and is what `basic_stream` does for simplicity).
- **Caller hands a sync hint.** Set `frame.sync.wait_event` (a `cudaEvent_t` recorded on your stream after the producer kernel) or `frame.sync.stream` (the CUDA stream the kernel ran on). ovstream chains on the event/stream without a global device sync. SHM and CUDASHM use `cudaStreamWaitEvent` and avoid any host block; RTSP / WebRTC host-block on the event (or stream) before handing the buffer to the encoder. `wait_event` takes precedence when both are set.

The sync hint is the right pattern for production render loops that already manage their own CUDA streams — it avoids the global `cudaDeviceSynchronize` cost between producer and ovstream.

## Multi-server zero-copy

When you feed multiple servers from one buffer (e.g. WebRTC + RTSP simultaneously, see `basic_stream` example), each server reads the same device pointer. No extra copies — NVENC encodes once per server, but the source data is shared.

## Multi-GPU device selection

On a multi-GPU host the GPU your frames live on is often **not** the GPU the backend uses by default: WebRTC/native default to StreamSDK's display adapter, RTSP to the encoder element's registered device, and SHM/CUDASHM to the calling thread's current device. A mismatch yields a connected client with no decodable video (WebRTC/native) or a cross-device copy failure (SHM/CUDASHM).

Set `ServerConfig.cuda_device` (C: `config.cuda_device`) to the ordinal your frame buffers are allocated on — every CUDA-input backend honors it.

**For WebRTC/native, also pass `cuda_context`** — the producer's `CUcontext` as an int. This is **required** whenever the producer allocates frames in its own CUDA context, which Warp, ovrtx, and most real renderers do. StreamSDK encodes from the device pointer through that context; with `cuda_context=0` it falls back to the device's *primary* context, which cannot read a buffer allocated in a different context and fails the encode (`CUDA error invalid argument: Copying device buffer to VideoFrameResourceCuda`). `cuda_context=0` is only safe when the frames live in the primary context already, e.g. a plain `cudaMalloc` buffer.

```python
# Warp / ovrtx producer (own CUDA context): pass both.
cfg = ovstream.ServerConfig(
    width=W, height=H,
    cuda_device=0,
    cuda_context=int(wp.get_device("cuda:0").context),
)

# Plain cudaMalloc producer (primary context): device alone is enough.
cfg = ovstream.ServerConfig(width=W, height=H, cuda_device=0)
```

Left at the default `-1`, `cuda_device` preserves each backend's prior behavior, so single-GPU apps need not set either field.

## Key Types / Functions

| Python | C |
|--------|---|
| `ovstream.VideoFrame(buffer=ptr, width=w, height=h, pitch_bytes=p)` | `ovstream_video_frame_t { .buffer = ptr, .width = w, .height = h, .pitch_bytes = p }` |
| Any ctypes / CuPy / Warp device pointer (int) | `void*` device pointer |

## Common Pitfalls

- **BGRA, not RGBA.** This is the #1 source of "my stream looks blue-tinted" reports.
- **Don't pass a host pointer.** `buffer` must be a CUDA *device* pointer. ovstream does not check (passing a host pointer crashes inside NVENC).
- **Freeing the buffer mid-stream is safe (since v0.4).** `stream_video` stages each frame into server-owned memory before returning, so `cudaFree` immediately after a `stream_video` call doesn't race the encoder. Earlier ovstream versions held the source by reference and required the buffer to outlive the encoder read; if you're targeting both, keep the buffer alive until `stop()`.
- **`pitch * height`, not `width * 4 * height`.** If your frame producer asserts on `pitch == width * 4`, you may need to allocate with plain `cudaMalloc` instead. That works too, but you lose the alignment benefit.
- **Multi-GPU: set `cuda_device`.** The backend's default encode/copy GPU is often not where your frames live (the display GPU for WebRTC/native). Set `ServerConfig.cuda_device` to your buffers' device — see *Multi-GPU device selection* above. Single-GPU apps can ignore it.
