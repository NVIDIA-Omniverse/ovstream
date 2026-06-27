---
name: callbacks-and-input
description: Wiring connection / message / input callbacks for WebRTC, native, SHM, and CUDASHM streams. Use when user asks about receiving input events, handling messages, detecting client connect/disconnect, or sending messages back.
---

# Callbacks and Input

## Overview

WebRTC, native, SHM, and CUDASHM servers all have **reverse channels** that surface as four callbacks:

| Callback        | Fires when                                      | Available on |
|-----------------|-------------------------------------------------|--------------|
| `on_connection` | A client connects or disconnects                | All transports |
| `on_message`    | The client sends a text/binary message          | WebRTC, native, SHM, CUDASHM |
| `on_input`      | The client emits a keyboard / mouse / gamepad / touch event | WebRTC, native, SHM, CUDASHM |
| `on_unicode`    | The client emits a composed text event (IME, on-screen kbd, paste, emoji) | WebRTC, native, SHM, CUDASHM |

RTSP has no input or message channel — only the connection callback fires. SHM and CUDASHM both carry the reverse channel over a local control socket / named pipe (sibling to the pixel transport).

You can also push outbound text/binary to connected clients via `send_message` / `ovstream_send_message`.

## Threading contract

Callbacks fire on internal SDK threads (network thread, GStreamer bus thread, etc.). Either:

- Keep callback bodies short and lock-free (e.g. assign to an atomic, push onto a thread-safe queue).
- Marshal to your main thread yourself if the work is heavy.

`ovstream_stream_*` is documented thread-safe with respect to the callback setters and the other `stream_*` / `send_message` calls on the same server (see the `@par Thread-Safety` note on each function), so calling it from inside a callback is legal. It is *not* safe to call concurrently with `ovstream_stop` / `ovstream_destroy_server` on the same handle, so don't issue those from a callback either.

## Register before start

Register callbacks **before** `start()` if you care about catching the very first connection transition. Registration after start is supported but only delivers future events, not the current state. If you do register after start, `server.is_client_connected` (a Python `@property`, no parens) / `ovstream_is_client_connected()` can fill in the gap.

## Python

```python
server.on_connection = lambda connected: print(f"Client {'connected' if connected else 'disconnected'}")
server.on_message    = lambda msg: server.send_message(msg)  # echo
server.on_input      = lambda event: handle_input(event)
server.on_unicode    = lambda text: handle_text(text)
```

> **Source:** `examples/python/basic_stream/main.py` snippet `create-server`

Input events arrive as `InputEvent` instances with a `type` field (`KEYBOARD`, `MOUSE`, `GAMEPAD`, `TOUCH`) and a discriminated `keyboard` / `mouse` / `gamepad` / `touch` payload. Pattern-match on `.type` then read the right sub-struct. A `TouchEvent` carries a `points` list of `TouchPoint`s (normalized `x`/`y` in `[0,1]`, a `TouchPhase`, and — for the low-level flavor — per-contact radius and timestamp); `low_level` distinguishes the two StreamSDK touch flavors.

## C

> **Source:** `examples/c/basic_stream/main.cu` snippet `register-callbacks`

The corresponding callback signatures and `userData` pass-through pointer are:

> **Source:** `examples/c/basic_stream/main.cu` snippet `on-connection-callback`
>
> Followed by: `examples/c/basic_stream/main.cu` snippet `on-message-callback`
>
> Followed by: `examples/c/basic_stream/main.cu` snippet `on-input-callback`

`userData` is opaque — pass any context pointer (in basic_stream, a `std::string` label).

## Sending messages back

```python
server.send_message("hello")     # Python
```

```cpp
ovstream_string_t msg;            // C
msg.ptr = "hello"; msg.length = 5;
if (!OVSTREAM_OK(ovstream_send_message(server, msg))) {
    // ovstream_get_last_error() returns a thread-local message.
}
```

UTF-8 only. WebRTC and native messages are limited to `UINT16_MAX` (65535) bytes due to the underlying StreamSDK data-channel limit; longer messages are rejected with `INVALID_ARGUMENT`. SHM and CUDASHM have no such limit — messages of any size are forwarded over the local control channel (capped only at the 16 MiB defense-in-depth ceiling on each individual line). RTSP returns `NOT_SUPPORTED`. For larger payloads on WebRTC/native (e.g. clipboard transfers), chunk at the application layer; ovstream does not provide a built-in chunking helper.

## String contract

Output strings from callbacks (`channel`, `message`, error strings) are null-terminated views: `ptr[length] == '\0'`, but `length` excludes the terminator. You can pass `ptr` directly to `printf("%s", ...)` or `memcpy(dst, ptr, length)` — no extra `strlen` needed.

Input strings (the message you pass to `send_message`) are length-bounded and need not be null-terminated. An empty view (`length == 0`) is rejected.

## Common Pitfalls

- **Callback after destroy.** Once you call `destroy_server` / `server.close()`, no further callbacks will fire — but a callback currently in flight from a network thread may still be running. The destroy call waits for it to drain.
- **Registering on RTSP is silent.** It succeeds (the API doesn't error), but `on_message` / `on_input` simply won't fire because RTSP has no inbound channel. WebRTC, native, SHM, and CUDASHM all deliver these callbacks normally.
- **`userData` lifetime.** If you pass a pointer into a callback registration, that pointer must outlive the server (or you must clear the callback with `NULL` before the pointer dies). The basic_stream example uses `std::unique_ptr<std::string>` to ensure the label string outlives the server.
- **Gamepad events.** SHM and CUDASHM deliver gamepad events end-to-end (consumers send them via `Client.send_input_event` on a `ClientType.SHM` / `ClientType.CUDASHM` client). `ClientType.NATIVE` clients forward keyboard / mouse / unicode input the same way (via `send_input_event` / `send_unicode`), but **not gamepad**. On WebRTC / native the infrastructure exists on the server side but the StreamSDK clients do not currently forward gamepad input, so gamepad button presses in a browser or native client won't produce `on_input` callbacks today. Don't design around the WebRTC path without verifying it works for your specific client.
