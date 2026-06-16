---
name: server-creation
description: Creating and configuring an ovstream server instance. Use when user asks to create a server, configure ports, set up a streaming endpoint, or pick between server types.
---

# Server Creation

## Overview

A `Server` (Python) / `ovstream_server_t*` (C) is the central object representing one streaming endpoint. You create it for a specific transport (`WEBRTC`, `RTSP`, `NATIVE`, `SHM`, `CUDASHM`), configure it, start it, stream frames into it, and finally stop + destroy it.

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
# WebRTC/native producers that render in their own CUDA context (Warp,
# ovrtx, most renderers) must hand ovstream that context as an int;
# plain cudaMalloc producers can leave cuda_context at 0.
cuda_ctx = int(wp.get_device("cuda:0").context)  # example for a Warp producer

cfg = ovstream.ServerConfig(
    width=1280,
    height=720,
    cuda_device=0,                   # GPU the frames live on (-1 = backend default)
    cuda_context=cuda_ctx,           # WebRTC/native: producer's CUcontext; required for own-context producers (Warp/ovrtx)
    webrtc_signal_port=50000,        # WebRTC / Native: signaling port
    stream_port=9000,                # RTSP: stream port
    shm_stream_name="my-stream",     # SHM: identifier (host-resident)
    cudashm_stream_name="my-stream", # CUDASHM: identifier (GPU-resident; OK to share with shm)
    cudashm_slot_count=4,            # CUDASHM: ring depth, default 4, clamped [2, 8]
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
- `cfg.shm.stream_name` / `cfg.shm.slot_count` — SHM identifier and ring depth (host-resident).
- `cfg.cudashm.stream_name` / `cfg.cudashm.slot_count` — CUDASHM identifier and ring depth (GPU-resident; ring lands on `cuda_device` when set, else on whichever CUDA device the calling thread is on at `start` time).
- `cfg.cuda_device` / `cfg.cuda_context` — encoder GPU for raw-CUDA input (top-level, not in a sub-struct). `cuda_device = -1` (default) uses the backend default. On a multi-GPU host that default is usually the display GPU, not the GPU the producer renders on; a mismatch yields a connected client with no decodable video. Set `cuda_device` to the ordinal your frame buffers live on; every CUDA-input backend honors it. **For WebRTC/native also set `cuda_context` to the producer's context** (e.g. `int(wp.get_device("cuda:0").context)`) — required whenever the producer renders in its own CUDA context (Warp, ovrtx, most renderers), or StreamSDK fails the encode with `CUDA error invalid argument`; `cuda_context = 0` is only safe for primary-context buffers like plain `cudaMalloc`. RTSP applies `cuda_device` to its `nvh264enc` encoder; SHM/CUDASHM place their copy stream and ring buffers on it (defaulting to the calling thread's current device when unset).

## Default port behavior

If you leave a port field at 0, `ovstream_start` resolves it to the protocol default:

| Protocol | Default port |
|----------|--------------|
| WebRTC   | signal 49100, stream 47998 |
| Native   | signal 49100, stream 47999 |
| RTSP     | stream 8554 |
| SHM      | n/a (no port; uses `stream_name`) |
| CUDASHM  | n/a (no port; uses `stream_name`) |

Multiple servers on the same protocol must be assigned **explicit unique ports** — there is no auto-increment. Two WebRTC servers that both default to `49100` will collide; the second `start()` returns an error. Set distinct `webrtc_signal_port` / `stream_port` per server. SHM and CUDASHM differentiate by `stream_name` instead — and the two backends use distinct OS-level path prefixes internally, so a parallel `shm` + `cudashm` pair off the same producer with the same `stream_name` is fine.

## WebRTC NAT traversal (ICE servers)

WebRTC / native servers reaching browsers on a different network usually need STUN (for NAT discovery) and TURN (for relay when direct paths are blocked). Configure them via a single setter that's separate from `ServerConfig`:

```python
server.set_webrtc_ice_servers([
    ovstream.WebRTCIceServer(urls="stun:stun.l.google.com:19302"),
    ovstream.WebRTCIceServer(
        urls="turn:turn.example.net:3478",
        username="alice",
        credential="secret",
    ),
])
```

```c
ovstream_webrtc_ice_server_t entries[2] = {};
entries[0].urls = OVSTREAM_STRING_LITERAL("stun:stun.l.google.com:19302");
entries[1].urls       = OVSTREAM_STRING_LITERAL("turn:turn.example.net:3478");
entries[1].username   = OVSTREAM_STRING_LITERAL("alice");
entries[1].credential = OVSTREAM_STRING_LITERAL("secret");
ovstream_webrtc_ice_config_t cfg = { entries, 2 };
ovstream_webrtc_set_ice_servers(server, &cfg);
```

Key points:
- **Call before OR after `start`.** Pre-start calls are cached and applied when the server starts; post-start calls take effect immediately via StreamSDK's runtime-parameter API. The post-start path is the canonical way to **refresh time-limited TURN tokens** without dropping connected clients.
- **Replace-all semantics.** Each call fully replaces the prior set. Pass `None` (Python) / `NULL` (C) to clear.
- **STUN vs TURN is inferred from the URL scheme** (`stun:` / `stuns:` vs `turn:` / `turns:`). One entry can carry a comma-separated mix of both.
- Returns `NOT_SUPPORTED` on RTSP / SHM / CUDASHM — those backends have no ICE concept.

## Key Types / Functions

| Python | C |
|--------|---|
| `ovstream.Server(server_type)` | `ovstream_create_server(server_type, &server)` |
| `ovstream.ServerType.{WEBRTC, RTSP, NATIVE, SHM, CUDASHM}` | `OVSTREAM_SERVER_{WEBRTC, RTSP, NATIVE, SHM, CUDASHM}` |
| `ovstream.ServerConfig(...)` | `ovstream_server_config_t` + `ovstream_config_defaults(&cfg)` |
| `server.start(cfg)` | `ovstream_start(server, &cfg)` |
| `server.stop()` | `ovstream_stop(server)` |
| `server.close()` (or `with` block) | `ovstream_destroy_server(server)` |
| `server.set_webrtc_ice_servers([WebRTCIceServer(...)])` | `ovstream_webrtc_set_ice_servers(server, &cfg)` |

## Common Pitfalls

- The server is created in a "not started" state — `create_server` doesn't bind a socket. Network listeners come up at `start` time. Register callbacks **before** `start` if you care about catching the initial connect transition.
- `ovstream_destroy_server` calls `stop` implicitly if you haven't already. The Python context manager does the same.
- Re-`start`-ing a stopped server is supported. Re-using a destroyed handle is undefined.
- `ServerConfig.width` and `.height` are baked in at `start` time. The active resolution is fixed until you stop and re-start. Client-driven dynamic resize is not currently supported.
- For protocol picking guidance, see the `protocol-selection` skill.
