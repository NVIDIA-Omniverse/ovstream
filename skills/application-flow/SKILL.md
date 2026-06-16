---
name: application-flow
description: High-level overview of a typical ovstream application lifecycle. Use when user asks how to structure an ovstream program, what the main steps are, or how the pieces fit together.
---

# Application Flow

## Overview

Every ovstream application follows the same core lifecycle, whether in Python or C:

```
1. Initialize SDK              → initialize / ovstream_initialize
2. Create server               → Server(ServerType.X) / ovstream_create_server
3. Register callbacks          → on_connection / on_message / on_input / on_unicode  (on_connection on all transports; on_message / on_input / on_unicode on WebRTC, native, SHM, CUDASHM)
4. Configure                   → ServerConfig(...) / ovstream_config_defaults + overrides
4a. (Optional, WebRTC/native)  → server.set_webrtc_ice_servers([...]) for STUN/TURN NAT traversal
5. Start                       → server.start(cfg) / ovstream_start
6. Stream loop:
   a. Produce frame (CUDA buffer or pre-encoded bitstream)
   b. Submit frame             → server.stream_video / ovstream_stream_video
   c. (Optional)                 server.set_webrtc_ice_servers([...]) again to refresh time-limited TURN credentials live
7. Stop                        → server.stop / ovstream_stop
8. Destroy server              → server.close / ovstream_destroy_server
9. Shutdown SDK                → shutdown / ovstream_shutdown
```

`initialize` / `shutdown` are ref-counted — call them once at process start/end (or matching pairs around a streaming subsystem). Creating multiple servers between one `initialize` and `shutdown` is fine.

## Python

> **Source:** `examples/python/basic_stream/main.py` snippet `initialize-sdk`
>
> Followed by: `examples/python/basic_stream/main.py` snippet `create-server`
>
> Followed by: `examples/python/basic_stream/main.py` snippet `stream-loop`
>
> Followed by: `examples/python/basic_stream/main.py` snippet `cleanup`

The `ovstream.Server` class is a context manager; if you don't need `initialize`/`shutdown` to span multiple servers, the simplest pattern is:

```python
ovstream.initialize()
with ovstream.Server(ovstream.ServerType.WEBRTC) as server:
    server.start(ovstream.ServerConfig(width=1920, height=1080))
    # ... stream frames ...
ovstream.shutdown()
```

## C

> **Source:** `examples/c/basic_stream/main.cu` snippet `initialize-sdk`
>
> Followed by: `examples/c/basic_stream/main.cu` snippet `create-server`
>
> Followed by: `examples/c/basic_stream/main.cu` snippet `register-callbacks`
>
> Followed by: `examples/c/basic_stream/main.cu` snippet `configure-server`
>
> Followed by: `examples/c/basic_stream/main.cu` snippet `start-server`
>
> Followed by: `examples/c/basic_stream/main.cu` snippet `stream-loop`
>
> Followed by: `examples/c/basic_stream/main.cu` snippet `cleanup`

## Composing with another library (the headline pattern)

The point of ovstream's small surface area is that you compose it with whatever produces CUDA frames. The canonical example is [`examples/python/ovrtx_stream`](../../examples/python/ovrtx_stream/) — ovrtx renders a USD scene, ovstream publishes the result over WebRTC. The two libraries don't know about each other; the app glues them together via the CUDA buffer that flows from one to the other.

If you're integrating ovstream into your own renderer, start from `ovrtx_stream/main.py` as a reference, then strip the ovrtx-specific pieces and replace them with your own frame producer.

## Key Differences: Python vs C

| Aspect | Python | C |
|--------|--------|---|
| Server lifetime | Context manager (`with Server(...) as s`) or explicit `close()` | `ovstream_destroy_server(server)` |
| Errors | `OvstreamError` exceptions | `ovstream_result_t` returns + `ovstream_get_last_error()` |
| Callback wiring | Assign to `server.on_connection`, `.on_message`, `.on_input` | `ovstream_set_connection_callback`, `ovstream_set_message_callback`, `ovstream_set_input_callback` |
| Config | `ServerConfig(width=..., height=..., ...)` dataclass | `ovstream_server_config_t` struct + `ovstream_config_defaults()` |

## Common Pitfalls

- **Callback timing.** Callbacks fire on internal network/SDK threads. Either keep callback bodies short and lock-free, or marshal to your main thread yourself. See `callbacks-and-input` skill.
- **Buffer lifetime in streaming.** `stream_video` stages the frame data into server-owned memory before returning, so the caller-supplied buffer is free to reuse or release immediately on return. Reusing a long-lived buffer across frames is still the common pattern because it avoids per-frame allocation; per-frame `std::vector`s or stack buffers are also legal. Producers that want to skip the staging copy and overlap their next render with the encoder's read should pass a CUDA event via `frame.sync`.
- **RTSP has no input/message channel.** Registering `on_message` / `on_input` on an RTSP server is silent — the registration succeeds but the callbacks never fire. WebRTC, native, SHM, and CUDASHM all carry a reverse channel; SHM and CUDASHM do so via a local control socket / named pipe alongside their pixel transports.
- **Don't forget the ref-counted shutdown.** Every `initialize` must be paired with a `shutdown`. Calling `initialize` twice and `shutdown` once leaks the backend lifetime.
- See `error-handling` skill for robust error checking patterns in both languages.
