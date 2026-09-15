---
name: app-messaging
description: Application-level messaging with the browser client via ovstream_utils.messaging — event types, correlation ids, automatic splitting and reassembly of large payloads, and per-event handlers on top of the raw app-message envelope. Use when user asks how to send or receive application events/JSON to the browser client, how large messages are chunked, what event_type/id/part/numParts mean, or how to port omni.kit.livestream.messaging usage to ovstream.
---

# Application Messaging

## Overview

ovstream's core moves application payloads as opaque strings: `Server.send_app_message(text)` wraps `text` in the app envelope the browser client routes to its application handler, and `Server.on_app_message` delivers inbound app envelopes unwrapped (see the `stream-messages` skill). It says nothing about what is inside the string and does not split large payloads.

`ovstream_utils.messaging` is the optional layer above that. It implements the application message convention established between the `@nvidia/ov-web-rtc` client library and the `omni.kit.livestream.messaging` Kit extension — a convention ovstream follows for stock-browser-client interoperability, not one it defines or enforces:

```json
{"event_type": "<name>", "id": "<uuid>", "payload": { ... }}
```

and, when a message does not fit one transport message, one part per message with a chunk of the JSON-serialised payload:

```json
{"event_type": "<name>", "id": "<uuid>", "part": 1, "numParts": 3, "payload": "<chunk>"}
```

It is Python only, pure JSON, and lives in `ovstream_utils` rather than the core on purpose: the core stays envelope-agnostic so native and bespoke clients remain first-class, and a convention owned outside ovstream can evolve without touching the C ABI. The module is written against the client library bundled with `examples/webrtc_client`; if the convention changes there, this module changes with it.

## Usage

```python
import ovstream
from ovstream_utils.messaging import AppMessenger

with ovstream.Server(ovstream.ServerType.WEBRTC) as server:
    messenger = AppMessenger(server)              # installs server.on_app_message
    messenger.on("viewport:select", lambda payload: select(payload["prim"]))
    server.start(config)
    ...
    message_id = messenger.send("scene:changed", {"stage": stage_path})
```

- `AppMessenger(server)` replaces any existing `server.on_app_message`. Construct it before `start()` like every other callback registration, and call `close()` (or use it as a context manager) to uninstall.
- `on(event_type, handler)` registers `handler(payload)`; one handler per event type, later registrations replace earlier ones. `off(event_type)` removes it.
- `send(event_type, payload)` returns the message `id`. `payload` may be any JSON-serialisable value; a dict is what the browser client expects.
- Handlers run on the SDK callback thread. Keep them short or hand off to your own thread (see the `callbacks-and-input` skill).

> **Source:** `examples/python/app_stream/app_stream/streaming.py` snippets `register-control-channel` (one `AppMessenger` per WebRTC/native server, registered before `start()`) and `cursor-and-state` (a periodic `app_stream:state` broadcast); `app.py` `_on_set_command` applies inbound `app_stream:set` on the main thread.

## Large payloads

Sizes are measured in wire bytes after both JSON escaping passes, so escape-heavy text is budgeted correctly. Messages under one part (16 KiB by default, `part_size=` to change) go out as a single message with `payload` as the value itself. Larger payloads are serialised, split into parts within the budget, and sent contiguously; each part carries the same `id` and its `part` / `numParts`. Inbound parts are buffered per `id`, tolerate any arrival order, and are dispatched once as the decoded payload. A transfer that grows past `reassembly_cap` (16 MiB by default) is dropped, and when several incomplete transfers together exceed it the oldest is dropped first.

**Browser-side reassembly depends on the client library version.** `@nvidia/ov-web-rtc` 6.4.0 and later (the version bundled with `examples/webrtc_client`) reassembles app-recipient parts per `id` and delivers the decoded payload to `onCustomEvent` once; parts must arrive in order, which the messenger's contiguous sends guarantee. Older client libraries deliver each part to `onCustomEvent` individually, each carrying `id` / `part` / `numParts`, and leave reassembly to the browser application. If you must support such clients and do not reassemble in the browser, construct the messenger with `part_size=None`: `send` then raises `ValueError` for a payload that does not fit one transport message instead of splitting it.

In the other direction the client library splits large outbound messages only after the config handshake has reported `supports_large_messages`, which ovstream always does; parts are slices of the JSON-serialised payload, at most 16 KiB minus 256 characters each, and the messenger reassembles them regardless of arrival order.

Payloads must be JSON-serialisable and are written as strict JSON: `send` raises `ValueError` for NaN or infinity, which the browser client's `JSON.parse` would reject.

## Unrouted and malformed messages

- Messages whose `event_type` has no handler go to `on_unhandled(event_type, payload)` if supplied, otherwise they are logged at debug level and dropped.
- Non-JSON messages, messages without a string `event_type`, and parts with inconsistent `id` / `part` / `numParts` are logged as warnings (logger `ovstream_utils.messaging`) and dropped. Nothing raises on the callback thread; handler exceptions are logged.
- Inbound traffic that is not an app envelope never reaches the messenger; it stays on `server.on_message` as before.

## Errors from `send`

`send` propagates the core's `OvstreamError`: `INVALID_STATE` when the server is not started or no client is connected, `NOT_SUPPORTED` on RTSP / SHM / CUDASHM servers. `ValueError` for an empty event type.

## Relationship to omni.kit.livestream.messaging

The wire shape is the same canonical flat envelope the Kit extension emits, so a browser client built against the Kit extension works unchanged. The API is not a port: there is no carb event bus, no `@observe` decorators, no legacy nested envelope, and handlers receive the decoded payload rather than an event object.

## Common Pitfalls

- **Constructing the messenger after `start()`.** Messages received before the messenger exists went to whatever `on_app_message` / `on_message` was in place at the time.
- **Using `server.send_app_message` and the messenger on the same server.** Both work, but only messenger-sent payloads carry `event_type` / `id`, and only messenger-sent large payloads are split.
- **Expecting a reply channel.** The messenger does not correlate requests and responses; if you need that, put the request `id` in your response payload yourself.
