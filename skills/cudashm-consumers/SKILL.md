---
name: cudashm-consumers
description: Writing a CUDASHM reader/consumer that attaches to an ovstream CUDASHM producer over CUDA IPC. Use when user asks about consuming GPU-resident frames, reading from a CUDA IPC ring, writing a simulation/post-processing kernel that ingests rendered output, or running a consumer in a separate container.
---

# CUDASHM Consumers

## Overview

The CUDASHM transport keeps frames **in GPU memory end-to-end**. The producer pre-allocates a ring of CUDA buffers, exports each via `cudaIpcGetMemHandle`, and `cudaMemcpy2DAsync` D2Ds each new frame into the next slot. The consumer attaches by stream name, imports every IPC handle once at attach time, and on each `wait_frame` gets back a CUDA device pointer that addresses the slot directly. No device-to-host copy happens inside ovstream.

This is the right transport when the consumer is itself running CUDA kernels (simulation, ML pre/post-processing, GPU compositing) and would otherwise have to upload host pixels back to the GPU after a SHM read.

Multiple consumers can attach to one producer concurrently. Consumers can come and go independently of the producer.

> [!IMPORTANT]
> The consumer **must run in a separate process** from the producer. CUDA forbids `cudaIpcOpenMemHandle` in the process that called `cudaIpcGetMemHandle`. Docker containers on the same host are fine (with `--ipc=host --gpus all` so IPC handles cross the container boundary).

The unified `ovstream.Client` / `ovstream_create_client` is backend-agnostic — the transport is chosen by `ClientType`. This skill covers `ClientType.CUDASHM`; the same `Client` also speaks `ClientType.SHM` (host-resident, see the `shm-consumers` skill) and `ClientType.NATIVE` (network, see `protocol-selection`).

## Python: `ovstream.Client(ovstream.ClientType.CUDASHM)`

The minimal pattern:

```python
with ovstream.Client(ovstream.ClientType.CUDASHM, stream_name="my-stream") as client:
    while client.is_alive():
        frame = client.wait_frame(timeout_ms=500)
        if frame is None:
            continue
        # frame.device_ptr is a CUDA device pointer into the producer's
        # imported ring buffer. Launch your kernel directly against it,
        # or cudaMemcpy to host if you really need CPU pixels.
        my_consumer_kernel(frame.device_ptr,
                           frame.width, frame.height,
                           frame.pitch_bytes)
```

`wait_frame(timeout_ms=...)` blocks up to the timeout, returning `None` on timeout or when no new frame has arrived. `is_alive()` flips to `False` within ~100 ms of producer exit. The returned `ovstream.Frame` has `frame.source == ovstream.ClientType.CUDASHM` and exposes the imported device pointer in `frame.device_ptr`. The same reverse-channel `send_message` / `send_input_event` / `send_unicode` / `on_message` API is available — the producer's regular callbacks fire for CUDASHM events too.

For a visual reader using OpenCV (which D2Hs each slot for screen display), see [`examples/python/local_stream/main_cudashm_viewer.py`](../../examples/python/local_stream/main_cudashm_viewer.py).

## C / C++: `<ovstream/ovstream_client.h>`

The C API surface (`type` is `OVSTREAM_CLIENT_CUDASHM`, with `config.cudashm.stream_name` set):

- `ovstream_create_client(type, &config, &client)` — attach to a named producer. Imports every IPC handle into the calling thread's active CUDA context.
- `ovstream_client_wait_frame(client, timeoutMs, &frame)` — block for a frame; populates `frame.device_ptr` with the imported device pointer for the latest slot.
- `ovstream_client_is_alive(client, &alive)` — watchdog.
- `ovstream_destroy_client(client)` — detach. Closes every imported IPC handle.
- `ovstream_client_get_producer_device(client, &device)` — the GPU ordinal the producer allocated its ring on.
- `ovstream_client_send_input_event(client, &event)` — see [Reverse channel](#reverse-channel-driving-the-producer-from-the-consumer).
- `ovstream_client_send_message(client, message)` — see [Reverse channel](#reverse-channel-driving-the-producer-from-the-consumer).
- `ovstream_client_send_unicode(client, text)` — see [Reverse channel](#reverse-channel-driving-the-producer-from-the-consumer).
- `ovstream_client_set_message_callback(client, cb, user_data)` — see [Reverse channel](#reverse-channel-driving-the-producer-from-the-consumer).

The returned `ovstream_frame_t` carries `source == OVSTREAM_CLIENT_CUDASHM` and the imported device pointer in `frame.device_ptr`. Useful when writing a native simulator, a GPU post-processing pipeline, or any C/C++ consumer that already has a CUDA context and wants pixels delivered as device pointers. The `ovstream_client` shared library links the CUDA runtime (needed for `cudaIpcOpenMemHandle` / `cudaIpcCloseMemHandle`) and StreamSDK (for the NATIVE backend), but the CUDASHM transport itself uses neither StreamSDK nor GStreamer at runtime.

## Reverse channel: driving the producer from the consumer

CUDASHM, like SHM, isn't one-way. Every connected client gets a sibling control channel (Unix-domain socket on POSIX, named pipe on Windows) that carries client → producer events. The producer's regular `on_message` / `on_input` / `on_unicode` callbacks fire for CUDASHM events the same way they do for WebRTC / native / SHM — producer code is transport-agnostic.

Python:

```python
client = ovstream.Client(ovstream.ClientType.CUDASHM, stream_name="my-stream")
client.send_message("hello from the CUDASHM client")
client.send_input_event(
    ovstream.InputEvent(
        type=ovstream.InputEventType.MOUSE,
        mouse=ovstream.MouseEvent(
            type=ovstream.MouseEventType.BUTTON,
            modifiers=0, x=100, y=200, data=1, data2=0,
            button_state=ovstream.KeyState.DOWN,
        ),
    )
)
client.on_message = lambda text: print(f"from producer: {text}")
```

C: see the corresponding `ovstream_client_send_*` entries above. Constraints (16 MiB control-channel line limit, no embedded `\n` / `\r`) match SHM.

## Multi-consumer

Spawn N consumer processes, all using the same stream name; each independently imports the producer's IPC handles into its own CUDA context and observes the same ring. No special multi-client setup on the producer. **Each consumer is responsible for keeping up** — see the lifetime / ring-depth discussion below.

## Lifetime contract: the ring-depth knob

The producer's ring rotates asynchronously: as soon as `ovstream_stream_video` returns, the slot you just received from `wait_frame` is a candidate for being overwritten on the next produce. The protocol uses a sequence number recheck on the metadata so `wait_frame` itself returns a coherent snapshot — but the **pixels** can be clobbered by the producer while your consumer kernel is still running against them.

Sizing the ring deep enough is the contract:

- Default `cudashm_slot_count` is **4**, clamped to `[2, 8]`.
- 4 slots tolerate a consumer kernel that takes up to ~3 frame intervals to complete (the producer needs 4 frames to wrap onto the same slot).
- If your consumer is slower than that, bump `cudashm_slot_count` on the **producer side** (the consumer just imports whatever slots the producer exported). Each slot costs `width * height * 4` bytes of GPU memory.
- If you can't bump it, copy out the slot immediately (`cudaMemcpyAsync` to a consumer-owned buffer) and process from the copy.

There is **no per-frame ACK** from consumer to producer; the producer never waits on consumer completion. This keeps the producer's `stream_video` cost identical to its host-resident SHM equivalent and avoids back-pressure when one consumer falls behind.

## Resilience

- If the producer is killed (clean exit or hard kill), `wait_frame` returns `None` and `is_alive` flips. Consumer should detect that and exit cleanly.
- If a consumer is killed, the producer's `on_connection(False)` fires shortly after.
- Producer or consumer can start first; the other waits via attach-with-retry (see the viewer's `--retry-seconds` loop).

## CUDASHM stream names

Pick any short ASCII string (max 63 bytes). The same name must be used on both sides. The CUDASHM backend uses a distinct internal OS-path prefix (`cuda-…`) so the same `stream_name` can coexist with a parallel SHM server off the same producer with no collision.

Cleanup: same as SHM. The producer unlinks its host-shared metadata region at clean shutdown; a hard-killed producer leaves the region behind:

- Linux: `rm /dev/shm/ovstream-cuda-<stream_name>` if needed (the next producer `start()` with the same name unlinks any stale region before recreating).
- Windows: the named file mapping is reference-counted by the kernel and closes when the last process exits.

The GPU memory of the ring buffers themselves is freed by the producer on `stop()`; consumers' `cudaIpcOpenMemHandle` imports are reference-counted by the CUDA driver and released cleanly on `destroy()`.

## Key Types / Functions

| Python | C |
|--------|---|
| `ovstream.Client(ovstream.ClientType.CUDASHM, stream_name=…)` | `ovstream_create_client(OVSTREAM_CLIENT_CUDASHM, &config, &client)` |
| `client.wait_frame(timeout_ms=N)` | `ovstream_client_wait_frame(client, N, &frame)` |
| `frame.device_ptr` / `frame.pitch_bytes` / `frame.width` / `frame.height` / `frame.slot_index` | (same fields on the C `ovstream_frame_t`) |
| `client.is_alive()` | `ovstream_client_is_alive(client, &alive)` |
| `client.get_producer_device()` | `ovstream_client_get_producer_device(client, &device)` |
| `client.close()` / context manager exit | `ovstream_destroy_client(client)` |

## Common Pitfalls

- **Same-process attach fails.** CUDA forbids `cudaIpcOpenMemHandle` in the producer's own process. Run the producer and the consumer in separate processes (or containers).
- **Container setup needs `--ipc=host --gpus all`.** Without `--ipc=host`, the underlying IPC primitives can't cross the container boundary; without `--gpus all` (or an explicit device mapping), the consumer's CUDA context can't see the producer's device memory.
- **Stream-name mismatch.** Both sides must pass the exact same string. The producer's default name is `ovstream-<pid>`, so an external consumer has no way to derive it without being told — name explicitly in production.
- **Producer and consumer must be on the same GPU.** CUDASHM IPC handles are tied to a single CUDA device. The producer allocates its ring on `config.cuda_device` (or, when that's `-1`, whatever device the calling thread is on at `ovstream_start` time — `cudaSetDevice(N)` first to pin it). The consumer's process must select the same device (`cudaSetDevice`) before calling `ovstream_create_client(OVSTREAM_CLIENT_CUDASHM, …)`, unless the two GPUs support peer access (the import enables it lazily). Match it out-of-band with the producer's `cuda_device`; after attaching, confirm with `client.get_producer_device()` / `ovstream_client_get_producer_device`, and on a mismatch the attach error names the producer's device. Cross-GPU CUDA IPC without peer access is not supported by the CUDA runtime.
- **Don't free the imported pointer.** `cudaFree` on a `frame.device_ptr` would be a use-after-free of the producer's GPU buffer. The consumer's lifetime contract is: read pixels (or copy them out), then call `wait_frame` again. Cleanup happens via `client.close()` / `ovstream_destroy_client`, which calls `cudaIpcCloseMemHandle` on every imported handle.
- **The viewer is just a debug tool.** `main_cudashm_viewer.py` does a per-frame D2H copy purely so OpenCV can display the pixels. A real consumer (sim kernel, post-processing) should launch its CUDA work against `frame.device_ptr` directly and skip the host round-trip — that's the whole point of the transport.
- **BGRA8 only.** Same as SHM; pre-encoded codecs are rejected at `ovstream_start`.
