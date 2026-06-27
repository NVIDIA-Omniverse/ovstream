---
name: native-consumers
description: Writing a native (StreamSDK) reader/consumer that attaches to an ovstream NATIVE server over the network. Use when user asks about consuming a native stream, writing a StreamSDK client viewer, decoding frames client-side via NvStreamingMedia, or streaming between two different machines.
---

# Native Consumers

## Overview

The NATIVE transport carries encoded video over the network (NVIDIA StreamSDK / NVSS). A consumer connects to a running native server by IP + port, receives the encoded stream, and decodes it client-side via StreamSDK's NvStreamingMedia inbound-video API (which uses NVDEC on NVIDIA GPUs) into **host** BGRA8 frames. On each `wait_frame` the consumer gets a zero-copy host view of the decoded pixels.

Unlike SHM and CUDASHM, the native consumer needs neither the same host nor the same GPU as the producer — only an NVIDIA GPU + driver on the consumer machine for the NvStreamingMedia decode. **Producer and consumer can run on two different machines.** That cross-machine capability is the trade for the encode + decode latency the local transports avoid.

The unified `ovstream.Client` / `ovstream_create_client` is backend-agnostic — the transport is chosen by `ClientType`. This skill covers `ClientType.NATIVE`; the same `Client` also speaks `ClientType.SHM` (host-resident, same machine, see the `shm-consumers` skill) and `ClientType.CUDASHM` (GPU-resident, same host, see the `cudashm-consumers` skill). For the full transport trade-off, see `protocol-selection`.

## Python: `ovstream.Client(ovstream.ClientType.NATIVE)`

The minimal pattern:

```python
with ovstream.Client(ovstream.ClientType.NATIVE, server_ip="10.0.0.5") as client:
    while client.is_alive():
        frame = client.wait_frame(timeout_ms=500)
        if frame is None:
            continue
        # frame.as_numpy() returns a host BGRA8 view of the decoded pixels
        pixels = frame.as_numpy()
        # ... process ...
```

The config is connection parameters, not a stream name: `server_ip` (required), `signal_port` (default `49100`), `stream_port` (default `47999`), and `cuda_device` (default `-1` = device 0, the GPU the client decodes / converts on). Ports are validated to `0..65535` (a `ValueError` is raised otherwise).

`wait_frame(timeout_ms=...)` blocks up to the timeout, returning `None` on timeout or when no new frame has arrived. `is_alive()` flips to `False` when the StreamSDK connection drops. The returned `ovstream.Frame` has `frame.source == ovstream.ClientType.NATIVE`, and the pixels are host-resident (`frame.data` / `frame.as_numpy()` / `frame.as_buffer()`), `frame.pitch_bytes` is the row stride, and `frame.sequence` is a monotonic counter. The same reverse-channel `send_message` / `send_input_event` / `send_unicode` / `on_message` API is available — the producer's regular callbacks fire for native events too.

For a visual reader using OpenCV with live mouse / keyboard forwarding, see [`examples/python/native_client/main.py`](../../examples/python/native_client/main.py).

## C / C++: `<ovstream/ovstream_client.h>`

The C API surface (`type` is `OVSTREAM_CLIENT_NATIVE`, with `config.native.server_ip` set, and optionally `signal_port` / `stream_port` / `cuda_device`):

- `ovstream_create_client(type, &config, &client)` — connect to a server by IP + port. Initializes the StreamSDK session and the NvStreamingMedia decoder.
- `ovstream_client_wait_frame(client, timeoutMs, &frame)` — block for a decoded frame; populates `frame.data` with the host BGRA8 pixels.
- `ovstream_client_is_alive(client, &alive)` — watchdog; reflects whether the StreamSDK connection is up.
- `ovstream_client_get_producer_device(client, &device)` — the client's decode / convert device (`native.cuda_device`, or 0).
- `ovstream_destroy_client(client)` — disconnect the StreamSDK session and tear down the decoder.
- `ovstream_client_send_input_event(client, &event)` — see [Reverse channel](#reverse-channel-driving-the-producer-from-the-consumer).
- `ovstream_client_send_message(client, message)` — see [Reverse channel](#reverse-channel-driving-the-producer-from-the-consumer).
- `ovstream_client_send_unicode(client, text)` — see [Reverse channel](#reverse-channel-driving-the-producer-from-the-consumer).
- `ovstream_client_set_message_callback(client, cb, user_data)` — see [Reverse channel](#reverse-channel-driving-the-producer-from-the-consumer).

The returned `ovstream_frame_t` carries `source == OVSTREAM_CLIENT_NATIVE` and the decoded pixels in `frame.data` (host-resident, like SHM). Useful when writing a headless C consumer, a desktop viewer, or any non-Python client of a native server. For a minimal headless example, see [`examples/c/native_client/main.cpp`](../../examples/c/native_client/main.cpp).

## Retry on connect

`nvstConnectToServer` fails **synchronously** when the server isn't reachable yet, so a consumer started before its producer fails immediately rather than waiting. The examples wrap `ovstream_create_client` in a short retry loop (a deadline of ~10 seconds) so the producer / consumer launch order is forgiving. Mirror that pattern in production: catch the create failure, sleep briefly, and retry until a deadline.

## Reverse channel: driving the producer from the consumer

Native isn't a one-way pipe. Alongside the video stream, the consumer can push events to the producer over the StreamSDK input / message channels. The producer's regular `on_message` / `on_input` / `on_unicode` callbacks fire for these the same way they do for WebRTC / SHM / CUDASHM — producer code is transport-agnostic.

Python:

```python
client = ovstream.Client(ovstream.ClientType.NATIVE, server_ip="10.0.0.5")
client.send_message("hello from the native client")
client.send_unicode("é")  # IME / composed-text event
client.send_input_event(
    ovstream.InputEvent(
        type=ovstream.InputEventType.KEYBOARD,
        keyboard=ovstream.KeyboardEvent(key_code=65, scan_code=0,
                                        modifiers=0,
                                        key_state=ovstream.KeyState.DOWN),
    )
)
client.on_message = lambda text: print(f"from producer: {text}")
```

C:

- `ovstream_client_send_input_event(client, &event)` — keyboard / mouse event. **Gamepad is not forwarded on the native path** (keyboard / mouse / unicode are).
- `ovstream_client_send_message(client, message)` — text payload. Native caps each payload at 65535 bytes (the StreamSDK custom-message limit).
- `ovstream_client_send_unicode(client, text)` — IME / composed-text event. Same 65535-byte cap.
- `ovstream_client_set_message_callback(client, cb, user_data)` — register a server → client text-message handler.

## Resilience

- If the connection drops (producer exits or the network breaks), `wait_frame` returns a timeout / invalid-state result and `is_alive` flips. The consumer should detect that and exit cleanly.
- Producer or consumer can start first; the consumer waits via the retry-on-connect loop above.

## Key Types / Functions

| Python | C |
|--------|---|
| `ovstream.Client(ovstream.ClientType.NATIVE, server_ip=…, signal_port=…, stream_port=…, cuda_device=…)` | `ovstream_create_client(OVSTREAM_CLIENT_NATIVE, &config, &client)` with `config.native.{server_ip, signal_port, stream_port, cuda_device}` |
| `client.wait_frame(timeout_ms=N)` | `ovstream_client_wait_frame(client, N, &frame)` |
| `frame.as_numpy()` / `frame.as_buffer()` | `frame.data` / `frame.pitch_bytes` / `frame.width` / `frame.height` / `frame.sequence` |
| `client.is_alive()` | `ovstream_client_is_alive(client, &alive)` |
| `client.get_producer_device()` | `ovstream_client_get_producer_device(client, &device)` |
| `client.close()` / context manager exit | `ovstream_destroy_client(client)` |

## Common Pitfalls

- **Consumer needs an NVIDIA GPU + driver.** The decode runs through StreamSDK's NvStreamingMedia, which uses NVDEC on NVIDIA GPUs, so a machine with no NVIDIA GPU can't be a native consumer. This is the cost of lifting the same-host / same-GPU constraint of SHM / CUDASHM.
- **Connect is synchronous and fails fast.** Start the producer first, or use a retry-on-connect loop (the examples do). A consumer started before its producer otherwise fails immediately.
- **Ports must match the server.** `signal_port` (default `49100`) and `stream_port` (default `47999`) must match what the producer was started with. Both must be reachable across the network / firewall.
- **No gamepad input.** Keyboard, mouse, and unicode forward to the producer; gamepad does not on the native path.
- **Message / unicode payloads cap at 65535 bytes.** The StreamSDK custom-message limit. Chunk larger payloads at the application layer.
- **No browser viewer.** Native requires a StreamSDK client (the `native_client` examples, or your own). To reach a browser, run a WebRTC server alongside the native one.
- **Link only `ovstream::ovstream_client`.** A native consumer links the `ovstream::ovstream_client` target (never the server-side `ovstream` library), includes `<ovstream/ovstream_client.h>`, and has a runtime dependency on the bundled `NvStreamingMedia` + `NVCodec` libraries for the decode plus `StreamClientShared` for the StreamSDK session.
