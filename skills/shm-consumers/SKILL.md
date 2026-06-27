---
name: shm-consumers
description: Writing an SHM reader/consumer that attaches to an ovstream SHM producer. Use when user asks about consuming SHM frames, reading from shared memory, writing an OpenCV / Electron / custom viewer, or multi-reader patterns.
---

# SHM Consumers

## Overview

The SHM transport writes raw BGRA8 frames into a **named shared-memory ring buffer** for same-machine consumers. A reader attaches by stream name, pulls frames, and gets a zero-copy view directly into the shared region — no CUDA involved on the consumer side.

Multiple readers can attach to one producer concurrently. Readers can come and go independently of the producer.

The unified `ovstream.Client` / `ovstream_create_client` is backend-agnostic — the transport is chosen by `ClientType`. This skill covers `ClientType.SHM`; the same `Client` also speaks `ClientType.CUDASHM` (GPU-resident, see the `cudashm-consumers` skill) and `ClientType.NATIVE` (network, see `protocol-selection`).

## Python: `ovstream.Client(ovstream.ClientType.SHM)`

> **Source:** `examples/python/local_stream/main.py` snippet `shm-consumer`

The minimal pattern:

```python
with ovstream.Client(ovstream.ClientType.SHM, stream_name="my-stream") as client:
    while client.is_alive():
        frame = client.wait_frame(timeout_ms=500)
        if frame is None:
            continue
        # frame.as_numpy() returns a zero-copy numpy view of the BGRA pixels
        pixels = frame.as_numpy()
        # ... process ...
```

`wait_frame(timeout_ms=...)` blocks up to the timeout, returning `None` on timeout or when no new frame has arrived. `is_alive()` flips to `False` within ~100 ms of producer exit. The returned `ovstream.Frame` has `frame.source == ovstream.ClientType.SHM`, and for SHM the pixels are host-resident (`frame.data` / `frame.as_numpy()` / `frame.as_buffer()`).

For a visual reader using OpenCV, see [`examples/python/local_stream/main_viewer.py`](../../examples/python/local_stream/main_viewer.py) — ~60 lines that open a window and render each incoming frame.

## C / C++: `<ovstream/ovstream_client.h>`

The C API surface (`type` is `OVSTREAM_CLIENT_SHM`, with `config.shm.stream_name` set):

- `ovstream_create_client(type, &config, &client)` — attach to a named producer.
- `ovstream_client_wait_frame(client, timeoutMs, &frame)` — block for a frame.
- `ovstream_client_release_frame(client)` — documented V1 no-op; reserved for future protocol versions that may need explicit reader-side bookkeeping. Calling it is harmless; not calling it is correct.
- `ovstream_client_is_alive(client, &alive)` — watchdog.
- `ovstream_destroy_client(client)` — detach.
- `ovstream_client_send_input_event(client, &event)` — see [Reverse channel](#reverse-channel-driving-the-producer-from-the-consumer).
- `ovstream_client_send_message(client, message)` — see [Reverse channel](#reverse-channel-driving-the-producer-from-the-consumer).
- `ovstream_client_send_unicode(client, text)` — see [Reverse channel](#reverse-channel-driving-the-producer-from-the-consumer).
- `ovstream_client_set_message_callback(client, cb, user_data)` — see [Reverse channel](#reverse-channel-driving-the-producer-from-the-consumer).

The returned `ovstream_frame_t` carries `source == OVSTREAM_CLIENT_SHM`; for SHM the pixels are host-resident in `frame.data`. Same shape as the Python wrapper. Useful when writing an Electron N-API addon or a non-Python desktop consumer. For third-party clients that need to speak the wire protocol directly (no `ovstream_client` link), the protocol is documented in the "SHM transport wire protocol" section of `docs/ARCHITECTURE.md`.

## Reverse channel: driving the producer from the consumer

SHM isn't a one-way pipe. Alongside the pixel ring, every connected client has a sibling control channel (Unix-domain socket on POSIX, named pipe on Windows) that carries client → producer events. The producer's regular `on_message` / `on_input` / `on_unicode` callbacks fire for these, so the same producer code works for WebRTC, native, and SHM consumers.

Python:

```python
client = ovstream.Client(ovstream.ClientType.SHM, stream_name="my-stream")
client.send_message("hello from the SHM client")
client.send_unicode("é")  # IME / composed-text event
client.send_input_event(
    ovstream.InputEvent(
        type=ovstream.InputEventType.KEYBOARD,
        keyboard=ovstream.KeyboardEvent(key_code=65, scan_code=0,
                                        modifiers=0,
                                        key_state=ovstream.KeyState.DOWN,
                                        timestamp_us=0),
    )
)

# Server → client text messages (producer calls `ovstream_send_message`):
client.on_message = lambda text: print(f"from producer: {text}")
```

C:

- `ovstream_client_send_input_event(client, &event)` — keyboard / mouse / gamepad / touch event.
- `ovstream_client_send_message(client, message)` — text payload, capped at 16 MiB (the SHM control-channel line limit) and must not contain `\n` or `\r` (reserved as on-wire line terminators). The producer's `ovstream_send_message` is additionally capped at 65535 bytes on WebRTC/native because StreamSDK's data channel is.
- `ovstream_client_send_unicode(client, text)` — IME / composed-text event. Same 16 MiB / no-`\n`/`\r` constraint as `send_message`.
- `ovstream_client_set_message_callback(client, cb, user_data)` — register a server → client text-message handler.

## Multi-reader

Spawn N readers, all using the same stream name; each gets its own copy of the next frame as the ring buffer advances. There's no special multi-client setup on the producer.

## Resilience

- If the producer is killed (clean exit or hard kill), `wait_frame` returns `None` and `is_alive` flips. Reader should detect that and exit cleanly.
- If the reader is killed, the producer's `on_connection(False)` fires shortly after.
- Producer or reader can start first; the other waits via attach-with-retry (Python example does a 5-second deadline loop).

## SHM stream names

Pick any short ASCII string. The same name must be used on both sides. POSIX `shm_open` regions persist until explicitly `shm_unlink`'d — the producer unlinks at clean shutdown, but a hard-killed producer leaves the region behind:

- Linux: `rm /dev/shm/ovstream-<stream_name>` to clean up manually. (The next producer `start()` with the same name also unlinks any stale region before recreating, so this is rarely necessary in practice.)
- Windows: named-section handle is reference-counted by the kernel and closes when the last process exits; no manual cleanup needed.

## Key Types / Functions

| Python | C |
|--------|---|
| `ovstream.Client(ovstream.ClientType.SHM, stream_name=…)` | `ovstream_create_client(OVSTREAM_CLIENT_SHM, &config, &client)` |
| `client.wait_frame(timeout_ms=N)` | `ovstream_client_wait_frame(client, N, &frame)` |
| `frame.as_numpy()` / `frame.as_buffer()` | `frame.data` / `frame.pitch_bytes` / `frame.width` / `frame.height` |
| (auto on `__del__` or `close()`) | `ovstream_client_release_frame(client)` (V1 no-op) |
| `client.is_alive()` | `ovstream_client_is_alive(client, &alive)` |

## Common Pitfalls

- **Producer creates, reader attaches.** Only the producer's `Server(ServerType.SHM)` creates the shared region. Readers attach via `Client(ClientType.SHM)`; they don't call `ovstream.initialize()`.
- **Stream-name mismatch.** Both sides must pass the exact same string. The producer's default name is `ovstream-<pid>` (the producer's own PID), so an external reader has no way to derive it without being told — name explicitly in production.
- **Zero-copy views are valid only until the next slot recycles.** The producer rotates slots asynchronously and may overwrite the slot you just read at any time after `wait_frame` returns. `wait_frame` itself detects mid-read overwrite via a sequence-number recheck and discards the read on a conflict, so the bytes are stable at the moment of return — but copy out any pixels that need to outlive the next produced frame.
- **`release_frame` is a V1 no-op.** Per the wire-protocol spec, V1 has no per-frame reader-side reservation; the slot you just read can be overwritten as soon as the producer rotates. Future protocol versions may make `release_frame` load-bearing, so it stays in the C API for ABI symmetry — but skipping it under V1 is correct.
- **No browser viewer.** SHM is local-only. To stream to a browser, run a WebRTC server alongside the SHM one (the basic_stream example shows multi-transport).
