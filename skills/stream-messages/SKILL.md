---
name: stream-messages
description: Stream control messages on WebRTC/native — the config handshake, client-driven resize, clipboard sync, cursor forwarding, and exchanging app messages with the browser client. Use when user asks about client resize requests, clipboard, cursors, the config handshake, app messages, why on_message no longer sees certain JSON, or why a message sent with send_message never reaches the browser client.
---

# Stream Control Messages

## Overview

On WebRTC/native, clients exchange JSON *message envelopes* with the server over the custom-message data channel:

```json
{"messageType": "json", "messageRecipient": "<recipient>", "data": "<inner JSON as a string>"}
```

The wire format matches the kit-livestream extensions and the `@nvidia/ov-web-rtc` browser client, so a stock browser client works unmodified. Inbound routing:

| Envelope | Handled by | Delivered as |
|---|---|---|
| `messageRecipient: "stream"` | The SDK (always consumed) | Typed callbacks (`on_resize_request`, `on_clipboard`) + automatic handshake/cursor handling |
| `messageRecipient: "app"` (or its pre-handshake alias `"kit"`) | The SDK (unwrapped) | `on_app_message` with the inner `data` payload; falls back to `on_message` (full envelope) when `on_app_message` is not set |
| Everything else (non-JSON, non-envelope JSON, other recipients) | Your application | `on_message`, byte-identical — the intended channel for native/bespoke clients |

Stream control messages are WebRTC/native only: `set_cursor` / `send_clipboard` return `NOT_SUPPORTED` on RTSP / SHM / CUDASHM, and the callbacks register everywhere but only fire on WebRTC/native. Register all callbacks **before** `start()`.

## The config handshake

The browser client opens with a `config` request; the SDK answers automatically with the server's capabilities:

- `supports_localized_text_input`: always true (unicode/IME text events flow via `on_unicode`).
- `supports_large_messages`: always true.
- `allow_dynamic_resize`: true only when the stream was started with `webrtc.resizable` AND a resize-request callback is registered — both halves must be in place before the client connects, or the client is told resize is unavailable. A half-configured setup logs a warning naming the missing half.
- `cursor_type`: the current cursor name (seeds late-connecting clients).

## Client-driven resize

The SDK never resizes on its own — resize is producer-driven:

1. Start the stream with `webrtc.resizable` (`webrtc_resizable=True` in Python); `config.width` / `height` become the maximum resolution.
2. Register `on_resize_request` before `start()`.
3. When the client sends a resize message, the callback delivers the requested `(width, height)`, already validated (clamped to the maximum, alignment rules applied).
4. Re-render at the requested size; the per-frame dimensions you submit apply the resize. Allocate your framebuffer once at the maximum and render into a sub-region — this is required, not just convenient: resizable mode disables StreamSDK's frame copying, so the buffer must be a persistent allocation and per-frame reallocation is not supported (see the buffer lifetime contract in `ovstream_types.h`).

```python
render_size = [cfg.width, cfg.height]

def on_resize(width, height):
    render_size[0] = width
    render_size[1] = height

server.on_resize_request = on_resize
```

```cpp
void onResizeRequest(ovstream_server_t* server, uint32_t width, uint32_t height, void* userData)
{
    // Store for the render loop; the next frame's dimensions apply it.
}
...
ovstream_set_resize_request_callback(server, onResizeRequest, nullptr);
```

> **Source:** `examples/c/starfield_stream/main.cu` and `examples/python/warp_stream/main.py` demonstrate the full flow, paired with the "Fit to window" toggle in `examples/webrtc_client/index.html`.
>
> **Source:** `examples/python/app_stream/app_stream/app.py` snippet `apply-resize` shows the debounced, clamped resize applied to a renderer at runtime; see the `resizable-streams` skill.

## Clipboard sync

Both directions, chunked automatically to fit the 64 KB per-message transport limit:

```python
server.on_clipboard = lambda text: apply_clipboard(text)  # client -> server, reassembled
server.send_clipboard("copied in the app")                # server -> client
```

```cpp
ovstream_set_clipboard_callback(server, onClipboard, nullptr);
ovstream_send_clipboard(server, ovstream_string_t{ text, textLen });
```

`send_clipboard` raises/returns `INVALID_STATE` when no client is connected. Inbound transfers that restart, arrive out of order, or exceed 16 MiB reset the reassembly state with a warning.

## Cursor forwarding

`set_cursor` informs connected clients of the application's current cursor shape so a web client can mirror it:

```python
server.set_cursor("Crosshair")
```

- The name is an open string. Stock browser-client names: `Arrow` (initial state), `IBeam`, `Hand`, `Crosshair`, `VerticalResize`, `HorizontalResize`; custom names pass through unchanged.
- Setting the current name again is a no-op (deduplicated); late-connecting clients receive the current name in the handshake; the name resets to `Arrow` on `stop()`.

## App messages

Messages the client addresses to the application (`messageRecipient: "app"`, or `"kit"` before the handshake completes) arrive on `on_app_message` with the envelope stripped — the callback receives the inner `data` string directly. Chunked app messages arrive as individual parts; the chunk metadata (`id` / `part` / `numParts`) lives inside the payload itself, so each part is self-describing and reassembly is up to the application.

With no `on_app_message` set, app envelopes fall back to `on_message` in full.

Server to client, `send_app_message` wraps the payload in the same envelope so a stock browser client delivers it to its application message handler (`onCustomEvent` in `@nvidia/ov-web-rtc`). The payload must be JSON text: the envelope declares it as JSON and the browser client parses it before delivery, so a non-JSON string is accepted by the SDK but rejected by the client. A bare `send_message` payload is **not** wrapped: the browser client drops it as an unknown recipient, so `send_message` is the raw channel for native/bespoke clients only. Outbound app payloads are not chunked by the core; the payload and the wrapped envelope must each fit the 64 KB transport limit, so split larger payloads into self-describing parts the way inbound chunked app messages are. `ovstream_utils.messaging.AppMessenger` does this splitting and the inbound reassembly (see the `app-messaging` skill).

```python
server.on_app_message = lambda payload: handle(json.loads(payload))               # client -> server, unwrapped
server.send_app_message(json.dumps({"event_type": "state", "payload": state}))    # server -> client, wrapped
```

```cpp
ovstream_set_app_message_callback(server, onAppMessage, nullptr);
ovstream_send_app_message(server, ovstream_string_t{ payload, payloadLen });
```

`send_app_message` raises/returns `INVALID_STATE` when the server is not started or no client is connected (so a message sent before the browser connects is reported, not silently dropped) and `INVALID_ARGUMENT` when the wrapped envelope exceeds the transport limit.

For the application-level convention the browser client speaks inside the payload (`event_type`, correlation `id`, automatic splitting and reassembly of large payloads, per-event handlers), use `ovstream_utils.messaging.AppMessenger` on top of these two calls — see the `app-messaging` skill.

## Threading

All of these callbacks fire on StreamSDK internal threads — keep bodies short or marshal to your own thread (see the `callbacks-and-input` skill). `set_cursor`, `send_clipboard`, and `send_app_message` are safe to call from any thread; concurrent `send_clipboard` calls serialize so chunk sequences never interleave.

## Per-backend support

| Capability | WebRTC / native | RTSP | SHM / CUDASHM |
|---|---|---|---|
| Config handshake, resize requests, clipboard, cursor, app messages | Yes | No (`NOT_SUPPORTED` / callbacks never fire) | No (`NOT_SUPPORTED` / callbacks never fire; the raw message channel is unchanged) |

QoS hints (`on_qos` / `ovstream_set_qos_callback`) are related but are **not** stream messages — they are StreamSDK network feedback delivered directly from the backend (see the `server-creation` skill for the pairing with `webrtc.resizable`).

## Common Pitfalls

- **Registering the resize callback after `start()`.** A client that handshakes before registration is told resize is unavailable and won't send resize requests until it re-handshakes.
- **Expecting stream envelopes on `on_message`.** On WebRTC/native the SDK consumes them; `on_message` receives only traffic the protocol layer does not own.
- **Sending to the browser client with `send_message`.** The browser client only surfaces app-recipient envelopes; a bare payload is dropped as an unknown recipient. Use `send_app_message`.
- **Native/bespoke clients** (`ovstream_client.h`) receive server-to-client cursor / clipboard / config-response envelopes raw on their message callback and should ignore unknown envelopes; raw bytes remain the intended client-to-server channel for them.
