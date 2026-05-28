<!--
SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: LicenseRef-NvidiaProprietary

NVIDIA CORPORATION, its affiliates and licensors retain all intellectual
property and proprietary rights in and to this material, related
documentation and any modifications thereto. Any use, reproduction,
disclosure or distribution of this material and related documentation
without an express license agreement from NVIDIA CORPORATION or
its affiliates is strictly prohibited.
-->
---
name: server-creation
description: Creating and configuring an ovstream server instance. Use when user asks to create a server, configure ports, set up a streaming endpoint, or pick between server types.
---

# Server Creation

## Overview

A `Server` (Python) / `ovstream_server_t*` (C) is the central object representing one streaming endpoint. You create it for a specific transport (`WEBRTC`, `RTSP`, `NATIVE`, `SHM`), configure it, start it, stream frames into it, and finally stop + destroy it.

Multiple servers can coexist in one process — typical pattern is one WebRTC server for interactive clients plus one RTSP server for industry-standard tools, fed from the same CUDA buffer.

## Python

> **Source:** `examples/python/basic_stream/main.py` snippet `create-server`

The simplest one-protocol version:

```python
with ovstream.Server(ovstream.ServerType.WEBRTC) as server:
    cfg = ovstream.ServerConfig(width=1920, height=1080)
    server.start(cfg)
    # ... stream ...
```

`ServerConfig` is a dataclass with sensible defaults (1920×1080, 60 FPS, BGRA8 CUDA, port 0 = "use protocol default"). Override only what you care about:

```python
cfg = ovstream.ServerConfig(
    width=1280,
    height=720,
    webrtc_signal_port=50000,    # WebRTC / Native: signaling port
    stream_port=9000,            # RTSP: stream port
    shm_stream_name="my-stream", # SHM: identifier
)
```

## C

> **Source:** `examples/c/basic_stream/main.cu` snippet `create-server`
>
> Followed by: `examples/c/basic_stream/main.cu` snippet `configure-server`
>
> Followed by: `examples/c/basic_stream/main.cu` snippet `start-server`

`ovstream_config_defaults(&cfg)` populates the struct with 1920×1080 @ 60 FPS and port fields at 0. Override fields after that call.

Per-protocol fields live in named sub-structs on `ovstream_server_config_t`:

- `cfg.webrtc.signal_port` — WebRTC / Native signaling port.
- `cfg.stream_port` — RTSP stream port.
- `cfg.shm.stream_name` — SHM identifier (string view).

## Default port behavior

If you leave a port field at 0, `ovstream_start` resolves it to the protocol default:

| Protocol | Default port |
|----------|--------------|
| WebRTC   | signal 49100, stream 47998 |
| Native   | signal 49100, stream 47999 |
| RTSP     | stream 8554 |
| SHM      | n/a (no port; uses `stream_name`) |

Multiple servers on the same protocol must be assigned **explicit unique ports** — there is no auto-increment. Two WebRTC servers that both default to `49100` will collide; the second `start()` returns an error. Set distinct `webrtc_signal_port` / `stream_port` per server.

## Key Types / Functions

| Python | C |
|--------|---|
| `ovstream.Server(server_type)` | `ovstream_create_server(server_type, &server)` |
| `ovstream.ServerType.{WEBRTC, RTSP, NATIVE, SHM}` | `OVSTREAM_SERVER_{WEBRTC, RTSP, NATIVE, SHM}` |
| `ovstream.ServerConfig(...)` | `ovstream_server_config_t` + `ovstream_config_defaults(&cfg)` |
| `server.start(cfg)` | `ovstream_start(server, &cfg)` |
| `server.stop()` | `ovstream_stop(server)` |
| `server.close()` (or `with` block) | `ovstream_destroy_server(server)` |

## Common Pitfalls

- The server is created in a "not started" state — `create_server` doesn't bind a socket. Network listeners come up at `start` time. Register callbacks **before** `start` if you care about catching the initial connect transition.
- `ovstream_destroy_server` calls `stop` implicitly if you haven't already. The Python context manager does the same.
- Re-`start`-ing a stopped server is supported. Re-using a destroyed handle is undefined.
- `ServerConfig.width` and `.height` are baked in at `start` time. The active resolution is fixed until you stop and re-start. Client-driven dynamic resize is not currently supported.
- For protocol picking guidance, see the `protocol-selection` skill.
