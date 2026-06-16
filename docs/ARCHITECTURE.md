# ovstream SDK

**Scope:** what the SDK looks like from 30,000 feet, and why the
pieces fit together the way they do. The public API contract lives in
the headers (`include/ovstream/ovstream.h`,
`include/ovstream/ovstream_types.h`, and the slim consumer-side
`include/ovstream/ovstream_shm_client.h`) and the Python docstrings
(`python/ovstream/`). **Those are the source of truth** — when
this document and the headers disagree, the headers win. Release
history lives in `CHANGELOG.md`.

This document is intentionally light on code snippets. If you want to
see the shape of an API, open the header; if you want a working
example or a first-run walkthrough, see `examples/` and
`skills/`. The headers themselves are doxygen-style-commented and
serve as the C API reference; Python docstrings on the installed wheel
serve as the Python API reference.

## Contents

- **[Overview](#overview)**
  - [Context](#context)
  - [Design principles](#design-principles)
  - [Layering](#layering)
  - [Lazy backend registration](#lazy-backend-registration)
  - [Ref-counted lifecycle](#ref-counted-lifecycle)
  - [Error handling](#error-handling)
  - [Strings (`ovstream_string_t`)](#strings-ovstream_string_t)
  - [Backend specifics](#backend-specifics)
    - [RTSP](#rtsp-backend)
    - [WebRTC / native](#webrtc--native-backend)
    - [SHM (shared memory)](#shm-shared-memory-backend)
    - [CUDASHM (CUDA shared memory)](#cudashm-cuda-shared-memory-backend)
  - [Common gotchas](#common-gotchas)
    - [CUDA synchronization on `stream_video`](#cuda-synchronization-on-stream_video)
    - [BGRA channel order, NOT RGBA](#bgra-channel-order-not-rgba)
    - [Register callbacks BEFORE `ovstream_start`](#register-callbacks-before-ovstream_start)
    - [Log callback wiring](#log-callback-wiring)
  - [Distribution](#distribution)
  - [Testing](#testing)
  - [What's not in scope](#whats-not-in-scope-currently)
- **[SHM transport wire protocol](#shm-transport-wire-protocol)**
  - [Naming and discovery](#1-naming-and-discovery)
  - [Shared-memory region layout](#2-shared-memory-region-layout)
  - [Producer write algorithm](#3-producer-write-algorithm)
  - [Consumer read algorithm](#4-consumer-read-algorithm)
  - [Control channel protocol](#5-control-channel-protocol)
  - [Versioning](#6-versioning)
  - [Consumer-facing API](#7-consumer-facing-api)
- **[Frame pacing utility (`ovstream_utils`)](#frame-pacing-utility-ovstream_utils)**
  - [Three surfaces, one algorithm](#three-surfaces-one-algorithm)
  - [API reference](#api-reference)
  - [Tick algorithm in detail](#tick-algorithm-in-detail)
  - [Stats: rolling vs cumulative](#stats-rolling-vs-cumulative)
  - [Threading model](#threading-model)
  - [Memory model](#memory-model)
  - [Errors](#errors)
  - [What `ovstream_utils` deliberately is not](#what-ovstream_utils-deliberately-is-not)

---

# Overview

## Context

ovstream packages the same streaming core that powers NVIDIA
Omniverse's app-streaming features as a standalone C library, so
products like ovrtx, SRTX, and Warp-based pipelines can stream
frames without pulling in the larger Omniverse Kit framework.

## Design principles

- **As much as needed, as little as possible.** Small public
  surface; features land when a real consumer asks for them.
- **Follow ovrtx conventions** where reasonable: opaque handles,
  thread-local error strings, explicit `initialize` / `shutdown`
  lifecycle, no framework-imposed lifecycle.
- **One `ovstream_stream_video`** with a unified frame descriptor —
  the server's configured `video_input` tells the implementation
  whether to expect raw CUDA (`pitch_bytes > 0`) or a pre-encoded
  bitstream (`size_bytes > 0`). New frame fields can be added without
  breaking the call shape.
- **One library ships all protocols.** Protocol is selected at
  runtime via `ovstream_create_server(OVSTREAM_SERVER_{RTSP, WEBRTC,
  NATIVE, SHM}, …)`; no separate WebRTC-only or RTSP-only build.
- **No Kit or Carbonite dependency.** The SDK builds and runs
  standalone; only StreamSDK (for WebRTC/native) and GStreamer (for
  RTSP) are pulled in as runtime dependencies, and both are bundled
  inside the distribution.

## Layering

```text
  Consumer code (Python, C/C++, CMake)
  │
  ▼
  Public C API  ─ include/ovstream/*.h
  │
  ▼
  C API layer   ─ handle management, validation, error-string plumbing
  │
  ▼
  Internal abstract server interface
  │   Plain C++ virtual class. No Carbonite, no reference counting.
  │
  ├──▶ RTSP backend     ─ GStreamer + gst-rtsp-server + bundled gstnvenc
  ├──▶ WebRTC backend   ─ StreamSDK (NativeWebRTC + Default backends)
  ├──▶ SHM backend      ─ named shared memory (local-process IPC)
  └──▶ CUDASHM backend  ─ server-owned CUDA buffer ring exported via
                          cudaIpcMemHandle (same-host GPU-resident IPC)
```

Every public call enters the C API layer. It validates arguments,
resolves the opaque handle to the internal server interface, and
forwards. RTSP, WebRTC, SHM, and CUDASHM backends all implement the
same internal interface — clean seam for swapping in other protocols
later.

### Python bindings

`python/ovstream/` is a thin ctypes wrapper on top of the same C
API. It does not reach into the internals; it is purely a consumer.

- `__init__.py` — library-scope entry points (`initialize`,
  `shutdown`, `get_version`) + re-exports.
- `_bindings.py` — ctypes `argtypes`/`restype` declarations and
  library discovery (`OVSTREAM_LIB_PATH` env var, pip-installed
  layout, dev tree layout).
- `_types.py` — dataclasses (`ServerConfig`, `VideoFrame`,
  `AudioFrame`, `InputEvent` / `KeyboardEvent` / `MouseEvent` /
  `GamepadEvent`) and enums (`ServerType`, `VideoInput`, `LogLevel`).
- `_server.py` — the `Server` class (context-manager friendly).

The wheel is self-contained: the native libraries (`ovstream.dll` or
`libovstream.so`, plus GStreamer and StreamSDK) are bundled
alongside the Python package, so `pip install ovstream` works with
no `PATH` / `LD_LIBRARY_PATH` tweaks.

## Lazy backend registration

`ovstream_initialize` is cheap: it registers two lazy factory
functions (one for RTSP, one for WebRTC/native) and returns. The
actual GStreamer or StreamSDK init does not happen until the first
`ovstream_create_server` for that backend.

Benefits:

- A WebRTC-only session pays zero GStreamer / gst-rtsp-server cost.
- An RTSP-only session pays zero StreamSDK cost.
- Startup time is dominated by whichever backend you actually use.

Concurrent first-`create_server` callers are serialized at two
levels:

- `ovstream_initialize` runs the lazy-factory registration once. The
  first thread wins a compare-exchange on a per-backend
  `BackendState` (`NotStarted → InProgress → Initialized | Failed`)
  state machine and runs the registration; later threads spin-yield
  on the state until the winner publishes `Initialized` or `Failed`.
- The factory registry is a separate mutex-guarded slot table. The
  mutex is held across writes (`registerServerType`, `clearRegistry`)
  and across the factory-pointer read in `createServer`, but
  released before the factory itself is invoked — the lazy wrappers
  re-enter `registerServerType` from inside their own first-call
  body, so holding the lock across invocation would deadlock.

## Ref-counted lifecycle

`ovstream_initialize` / `ovstream_shutdown` are ref-counted. A second
`initialize` call increments the refcount and returns; the matching
shutdown decrements it. Only the final shutdown tears backends down
and clears the log callback. This mirrors Carbonite's typical
module-lifecycle pattern and lets multiple independent subsystems
"belt-and-braces" initialize the SDK without treading on each other.

**Thread-safety:** `initialize` and `shutdown` must be serialized by
the caller. The typical pattern is to call `initialize` once from the
main thread before spinning up any workers.

**Log callback semantics:** only the very first (refcount 0 → 1)
`initialize` call installs the log callback. Subsequent
`initialize` calls with a different `log_callback` are ignored; change the
callback by draining the refcount to zero first. This keeps the C
side's raw callback pointer valid for the entire install-to-clear
window — matters for Python bindings where the trampoline is a
CFUNCTYPE that must remain Python-referenced the whole time.

## Error handling

Functions that fail set a **thread-local** error message retrievable via
`ovstream_get_last_error()`, which returns an `ovstream_string_t` view
(null-terminated, length-bearing). Thread-local means multi-threaded
callers don't race over a shared error slot, and the string is valid
until the next SDK call on the same thread (copy it out if you need
to retain it). The convention matches ovrtx.

## Strings (`ovstream_string_t`)

Every string parameter and string-bearing callback argument in the
SDK uses `ovstream_string_t`, a `{const char* ptr; size_t length;}`
view that is structurally identical to ovrtx's `ovx_string_t`. The
contract on `length` is direction-dependent and intentionally
asymmetric:

- **Inputs (caller -> SDK):** the SDK reads exactly `length` bytes
  and never reads at `ptr[length]`. Bytes are NOT required to be
  null-terminated. `ptr == NULL` is only valid when `length == 0`.
- **Outputs (SDK -> caller, including callbacks):** the SDK
  guarantees `ptr` is non-NULL and null-terminated; `length`
  excludes the terminator. Callers can pass `ptr` to any C string
  API or copy `length` bytes — no extra `strlen` pass needed.

Helper macros in `ovstream_types.h`: `OVSTREAM_STRING_LITERAL("…")`
builds an input view from a string literal at compile time. Callers
that need to test for emptiness inspect the fields directly:
`v.ptr == NULL || v.length == 0`.

The struct is intentionally analogous to ovrtx's `ovx_string_t` and
is expected to consolidate into a shared `ovx`-style utility header
once the broader OV libraries effort agrees on the input
null-termination contract. Until then, this is the ovstream-side
definition.

## Backend specifics

### RTSP backend

- Built on GStreamer + gst-rtsp-server.
- For raw CUDA input, the SDK constructs a pipeline around the
  bundled `gstnvenc` plugin that encodes the incoming CUDA BGRA8
  frame via NVENC, then wraps the result for `rtph264pay` /
  `rtph265pay`.
- For pre-encoded input (`video_input = H264` or `H265`), the pipeline
  is pure passthrough: the caller's bitstream goes straight into
  `rtph*pay`.
- `config.cuda_device` selects the encoder GPU, applied as the
  `nvh264enc cuda-device-id` property; `-1` uses the element's
  registered device.
- `OVSTREAM_VIDEO_INPUT_CUSTOM` lets the caller supply a verbatim
  GStreamer pipeline string via `config.rtsp.pipeline`. It is
  pre-encoded / host-buffer only; raw-CUDA through a custom pipeline
  is not currently supported (would require exposing a CUDA appsrc
  handle, which is out of scope today).
- **Library pinning at shutdown.** GStreamer plugins are loaded on
  demand and leave worker threads running even after `gst_deinit`
  would have been called. The SDK pins all loaded GLib/GStreamer
  shared libraries (`GET_MODULE_HANDLE_EX_FLAG_PIN` on Windows,
  `RTLD_NODELETE` on Linux) so the OS loader will not unload them
  when the SDK shared library itself goes away. Net effect: a small
  intentional leak in exchange for avoiding a hard crash on process
  shutdown. `gst_deinit()` is intentionally NOT called (upstream
  documentation says it's "normally not needed" and can corrupt the
  plugin registry).

### WebRTC / native backend

- Built on NVIDIA StreamSDK — the same library that powers GeForce
  NOW. Two server backends are exposed:
  - `OVSTREAM_SERVER_WEBRTC` (browser-compatible).
  - `OVSTREAM_SERVER_NATIVE` (lower-latency proprietary flavor, native StreamSDK client only).
- Raw CUDA input is encoded by StreamSDK. Pre-encoded input is
  passed through via `H264` / `H265` / `AV1` surface formats.
- `config.cuda_device` / `config.cuda_context` are passed to StreamSDK
  as the encoder's device + context. `-1` lets StreamSDK pick its
  default adapter, which on a multi-GPU host is typically the display
  GPU — set `cuda_device` to the GPU the producer's frames live on, or
  the encoder reads an inaccessible pointer and the client connects but
  never decodes. A one-time check on the first frame logs an error if
  the buffer's device doesn't match.
- `cuda_context` is **required** alongside `cuda_device` when the
  producer renders into its own CUDA context (Warp, ovrtx, most
  renderers): StreamSDK copies the input through that context, and with
  `cuda_context = 0` it falls back to the device's primary context,
  which cannot read a buffer owned by a different context — the copy
  fails with `CUDA error invalid argument: Copying device buffer to
  VideoFrameResourceCuda`. `0` is safe only when the frames already live
  in the primary context (plain `cudaMalloc`). The device-match check
  above does not catch this — the device matches; the context doesn't.
- StreamSDK's ERROR level is conservative — many things it reports
  as errors are recoverable conditions, so the SDK remaps them to
  WARN before delivering to the user's log callback.
- STUN / TURN credentials are supplied via
  `ovstream_webrtc_set_ice_servers` (C) /
  `Server.set_webrtc_ice_servers` (Python). Replace-all semantics;
  may be called BEFORE `ovstream_start` (cached, applied at startup)
  or AFTER `ovstream_start` (applied immediately via StreamSDK's
  runtime-parameter API — the canonical path for refreshing
  time-limited TURN tokens without restarting the stream). Each
  entry's URLs are split on `,` and classified by scheme: `stun:` /
  `stuns:` route to StreamSDK's NAT-server table (up to 4 entries),
  `turn:` / `turns:` route to the TURN table (up to 8 entries),
  with at most 3 URLs per entry. Transport policy is fixed to
  "all" (try direct paths first, fall back to TURN) — matches the
  WebRTC default. The setter returns `NOT_SUPPORTED` on RTSP / SHM /
  CUDASHM backends, which have no ICE concept.

### SHM (shared memory) backend

- No encoding, no networking. Raw BGRA8 CUDA frames are published
  verbatim into a named shared-memory segment that local consumers
  on the same machine can map and read. Backed by `shm_open` +
  `mmap` on Linux and named file-mapping objects on Windows; the
  segment name is configured via `config.shm.stream_name`.
- The intended use case is zero-overhead local IPC — another process
  on the same host consuming frames without paying a codec or
  network transport tax. Off-machine consumers should use RTSP or
  WebRTC.
- `config.cuda_device` selects the GPU the device-to-host copy stream
  runs on (created on that device, with the calling thread's current
  device saved and restored around it); `-1` uses the current device.
- Accepts raw CUDA (`OVSTREAM_VIDEO_INPUT_CUDA`) and DLPack tensor
  (`OVSTREAM_VIDEO_INPUT_TENSOR`) input. `OVSTREAM_VIDEO_INPUT_CUSTOM`
  and the pre-encoded codecs (`H264`, `H265`, `AV1`) are rejected for
  SHM — there's no encoder in the pipeline to decode them through.
- Bidirectional control channel alongside the pixel ring: `send_message`
  and the message / input / unicode user callbacks all work on SHM,
  via a sibling Unix-domain-socket / named-pipe endpoint. `stream_audio`
  is not supported (returns `NOT_SUPPORTED`).
- Wire format (segment layout, frame header, ring-buffer semantics,
  control-channel protocol) is documented in the
  [SHM transport wire protocol](#shm-transport-wire-protocol) section
  below. See `skills/shm-consumers/SKILL.md` for a reader walkthrough.

### CUDASHM (CUDA shared memory) backend

- Sibling of the SHM backend that keeps frames GPU-resident. The
  server allocates a ring of CUDA buffers (one per slot) with
  `cudaMallocPitch`, exports a `cudaIpcMemHandle_t` for each via
  `cudaIpcGetMemHandle`, and publishes the handle table through a
  small host-shared metadata region (~600 bytes for the default ring
  depth of 4). On each `ovstream_stream_video` call the server does
  a D2D `cudaMemcpy2DAsync` from the producer's CUDA pointer into
  the next ring slot, then publishes the slot index via the same
  atomic-sequence protocol as SHM. Same-host (or
  same-container-host) consumers attach with
  `ovstream_cudashm_client_create`, import every IPC handle once,
  and read pixels directly from the imported device pointer when
  `wait_frame` returns -- no device-to-host copy on either side.
- The intended use case is "render-output → simulation kernel
  zero-copy" workloads: the consumer's CUDA kernel reads the slot
  pointer directly with no encoder, no network, and no host
  staging. Off-machine consumers should use RTSP or WebRTC.
- **Producer device selection.** Set `config.cuda_device` to allocate
  the ring (and run the D2D copy stream) on a specific GPU; the server
  switches to it and restores the calling thread's previous current
  device before returning. When `cuda_device` is `-1` the ring is
  allocated on the calling thread's current device at `ovstream_start`
  time (the producer calls `cudaSetDevice(N)` first, same posture as
  the SHM backend) and the server does not call `cudaSetDevice` itself
  — doing so would force eager initialization of the primary context
  and interfere with StreamSDK's setup if a WebRTC / native server
  starts in the same process afterwards. The selected device ordinal is
  stamped into `RegionHeader::gpuDeviceOrdinal`; the consumer reads it
  back via `ovstream_cudashm_client_get_producer_device` and must do its
  CUDA work on that device (or a peer-capable one), since
  `cudaIpcOpenMemHandle` imports the producer's memory.
- Accepts the same input modes as SHM (`OVSTREAM_VIDEO_INPUT_CUDA`,
  `OVSTREAM_VIDEO_INPUT_TENSOR`); pre-encoded codecs are rejected.
- Bidirectional control channel and `send_message` behave identically
  to SHM (separate UDS / named pipe endpoint prefixed with `cuda-`
  so the two backends coexist with the same `stream_name`).
  `stream_audio` is not supported.
- Lifetime contract: the consumer is responsible for completing its
  read kernel before the producer's ring wraps onto the same slot.
  `cudashm.slot_count` (default 4, range [2, 8]) is the tuning knob;
  each slot costs `width * height * 4` bytes of GPU memory.
- Consumer-side entry points: C / C++ consumers link the slim
  `ovstream_cudashm_client` shared library and include
  `<ovstream/ovstream_cudashm_client.h>` —
  `ovstream_cudashm_client_create`, `_wait_frame`,
  `_is_producer_alive`, `_send_input_event`, `_send_message`,
  `_destroy`. Python consumers use `ovstream.CudashmClient`
  (defined in `ovstream/_cudashm_client.py`), which wraps the same
  C API and surfaces `CudashmFrame` with the imported device
  pointer. See `skills/cudashm-consumers/SKILL.md` for a reader
  walkthrough.

### Mapping user `log_min_severity` to dependencies

The user-facing `log_min_severity` is also propagated to the underlying
backends at their first lazy-init:

- GStreamer: `gst_debug_set_default_threshold(…)` is set to the
  equivalent `GstDebugLevel`.
- StreamSDK: `nvstInitializeLogger(…)` is called with the equivalent
  `NvstLogLevel`.

This way `LogLevel.VERBOSE` via `ovstream.initialize(log_min_severity=…)`
actually produces verbose dependency output, not just verbose
SDK-wrapper output.

## Common gotchas

Four things that bite production integrations more often than the rest of this document. The full contracts live in `ovstream_types.h` / `ovstream.h` and the per-skill files under `skills/`; this section is the consolidated reading order for someone integrating ovstream for the first time.

### CUDA synchronization on `stream_video`

Producers that submit GPU work for the frame they're about to push should use `ovstream_video_frame_t::sync` rather than fencing the whole device with `cudaDeviceSynchronize` before each `ovstream_stream_video` call:

- `frame.sync.wait_event` — the most precise signal. ovstream waits on this `cudaEvent_t` (cast to `uintptr_t`) before reading the buffer. For SHM and CUDASHM the wait chains as `cudaStreamWaitEvent` on the internal ingest stream, so the producer's stream is not host-blocked.
- `frame.sync.stream` — fallback when an event isn't available. On SHM/CUDASHM this falls back to `cudaStreamSynchronize` on the caller's stream (host-blocking); on RTSP/WebRTC the encoder always host-blocks anyway, so the field's role there is "what stream to sync against."
- Both zero (struct value-initialized) — the SDK trusts the caller to have already synchronized.
- `wait_event` takes precedence when both fields are non-zero.

Leaving `sync` zero-init and relying on global device sync works correctness-wise but wastes the SDK's ability to chain encoder ingest off the producer's GPU work without a CPU stall.

### BGRA channel order, NOT RGBA

All raw-CUDA video paths are BGRA8 — bytes per pixel, in this order: B, G, R, A. RGBA producers ship blue-tinted video and never see an error because the buffer is byte-correct, just channel-swapped. If your renderer outputs RGBA, swizzle producer-side before the `stream_video` call. Consumers of `OVSTREAM_VIDEO_INPUT_TENSOR` via DLPack are also responsible for delivering BGRA8-shaped tensors.

Pitch is the second alignment surface. For raw CUDA, `pitch_bytes` should be at least `width * 4`, and is conventionally aligned to 64 bytes for DMA efficiency (the SHM/CUDASHM ring rounds its `maxPitchBytes` to 64 for the same reason). Even widths are required by the NVENC encoder used in WebRTC/native and the `gstnvenc`-fronted RTSP backend; odd widths fail the start handshake rather than silently rounding.

### Register callbacks BEFORE `ovstream_start`

`ovstream_set_connection_callback`, `_message_callback`, `_input_callback`, and `_unicode_callback` can be called at any time, but they only deliver **future** transitions. Registering after `ovstream_start` returns means an in-flight first-connect or first-message event can fire before the callback is set and be lost — the SDK does not replay the initial state on registration.

Safe patterns:

- Register all callbacks BEFORE calling `ovstream_start`. The connection callback in particular wants to be in place before the network listener accepts the first client.
- If a callback must be set after start, call `ovstream_is_client_connected` immediately after registering to recover the initial connection state synchronously.

### Log callback wiring

The log callback is registered through `ovstream_init_config_t::log_callback` passed to `ovstream_initialize`, alongside the `log_min_severity` field on the same struct. Two non-obvious rules:

- Only the very first `ovstream_initialize` call (the refcount 0 → 1 transition) installs the callback. Subsequent `initialize` calls increment the refcount and ignore their `log_callback` field. To swap the callback, drain the refcount to zero with matching `shutdown` calls, then re-initialize.
- The callback fires for SDK-internal messages AND for everything GStreamer / StreamSDK log. Use `log_min_severity` to filter: `OVSTREAM_LOG_VERBOSE` is firehose-level, `OVSTREAM_LOG_WARNING` is the recommended production default. `OVSTREAM_LOG_DEFAULT` (the zero-init value) is remapped to WARNING, so a `{0}`-initialized config is quiet by default.

Python equivalent: `ovstream.initialize(log_fn=..., log_min_severity=...)` — same refcount-0→1 install rule.

## Distribution

- **Public PyPI** (`pypi.org`) and **DevZone PyPI** (`pypi.nvidia.com`)
  — the platform-tagged Python wheel. `pip install ovstream` from
  either index.
- **GitHub Releases** at
  `github.com/NVIDIA-Omniverse/ovstream/releases/tag/v<version>` —
  the per-platform `ovstream@<version>.<platform>.zip` archives. The
  CMake `ovstream_fetch()` helper downloads from here.

See `README.md` for the install walkthrough (Python via
`pip install ovstream`, C via CMake `find_package(ovstream)` or the
`ovstream_fetch()` helper at `examples/c/cmake/ovstream.cmake`).

## Testing

Lifecycle, config defaults, frame validation, messaging, input,
error handling, and WebRTC/RTSP client integration are exercised
by end-to-end C++ (doctest) and Python (pytest) test suites that
run in CI on every release against the real native library. The
test sources themselves are not part of the distributed package.

## What's not in scope (currently)

- Client-driven stream resize (the server streams at whatever
  resolution was passed to `ovstream_start`; client-requested
  viewport changes are ignored).
- Clipboard sync.
- Pre-encoded AV1 for RTSP (WebRTC/native only).
- Audio codecs other than PCM16.
- Raw-CUDA input through a custom RTSP pipeline.

See `CHANGELOG.md` for what has shipped.


# SHM transport wire protocol

**Version:** 1
**Status:** Pre-release

This section specifies the on-disk / on-wire surface of the ovstream
SHM (shared-memory) transport. It is the contract downstream consumers
(Electron N-API addons, alternative reader implementations, future
ovstream versions) commit to.

The protocol has two parts: a **shared-memory region** for frame pixels,
and a **control channel** for client lifecycle.

---

## 1. Naming and discovery

Both the shared-memory region and the control endpoint are named after a single user-supplied UTF-8 `streamName` (max 63 bytes). Empty `streamName` is rejected by the SDK; the producer's default of `"ovstream-<pid>"` is applied at the public API layer before the SHM backend ever sees an empty name.

### POSIX

| Resource | Name |
|---|---|
| Shared memory | `/ovstream-<streamName>` (passed to `shm_open`) |
| Wakeup primitive | `/ovstream-<streamName>-wake` (passed to `sem_open`) |
| Control endpoint | `${TMPDIR:-/tmp}/ovstream-<streamName>.sock` (Unix domain socket, `SOCK_STREAM`) |

### Windows

| Resource | Name |
|---|---|
| Shared memory | `Local\ovstream-<streamName>` (passed to `CreateFileMappingW` / `OpenFileMappingA`) |
| Wakeup primitive | `Local\ovstream-<streamName>-wake` (passed to `CreateEventA`, manual-reset event) |
| Control endpoint | `\\.\pipe\ovstream-<streamName>` (named pipe, byte mode, duplex) |

Producer-on-crash recovery: when a previous producer crashed without unlinking, a fresh `start()` first unlinks/destroys the stale region, semaphore, and socket file before recreating them. On Windows, named-pipe instances are reference-counted by the kernel; the new producer always creates with `FILE_FLAG_FIRST_PIPE_INSTANCE` and fails fast if the prior instance is still alive.

---

## 2. Shared-memory region layout

All multi-byte fields are little-endian. Every supported platform is LE; consumers may safely treat fields as native-endian on x86_64 / aarch64.

The region is a single contiguous mapping with the following structure:

```text
+----------------------------------------------------+ offset 0
|                  RegionHeader                      | 128 bytes (cache-line aligned, padded)
+----------------------------------------------------+ offset = slotsOffset
|                   Slot[0]                          | slotStride bytes
+----------------------------------------------------+
|                   Slot[1]                          | slotStride bytes
+----------------------------------------------------+
|                     ...                            |
+----------------------------------------------------+
|              Slot[slotCount - 1]                   | slotStride bytes
+----------------------------------------------------+ end of region
```

### 2.1 RegionHeader (128 bytes)

| Offset | Size | Field | Description |
|---:|---:|---|---|
| 0 | 4 | `magic` | `0x4D53564F` (`'OVSM'` LE). Reject if mismatched. |
| 4 | 4 | `protocolVersion` | Currently `2`. Reject if mismatched. |
| 8 | 4 | `pixelFormat` | Currently `1` = `OVSTREAM_SHM_FORMAT_BGRA8`. Other values reserved. |
| 12 | 4 | `slotCount` | Ring depth; `2..8`. |
| 16 | 4 | `maxWidth` | Width capacity (px). |
| 20 | 4 | `maxHeight` | Height capacity (px). |
| 24 | 4 | `maxPitchBytes` | Slot row-pitch capacity (bytes, ≥ `4 * maxWidth`, padded to 64). |
| 28 | 4 | `slotStride` | Distance between slot bases (bytes). |
| 32 | 8 | `slotsOffset` | Offset from region base to slot 0 (bytes). |
| 40 | 8 | `latestSequence` | Little-endian u64, written with release / read with acquire. Monotonic; `0` = no frame yet. |
| 48 | 4 | `latestSlot` | Little-endian u32, written with release / read with acquire. Index of slot holding `latestSequence`. |
| 52 | 4 | `producerAlive` | Little-endian u32, written with release / read with acquire. `0` = stopped/crashed, `1` = running. |
| 56 | 4 | `producerPid` | Little-endian u32. Producer's OS process ID, written **before** the release-store of `producerAlive`. Consumers probe this PID (POSIX `kill(pid, 0)`, Windows `OpenProcess`+`GetExitCodeProcess`) to detect a hard-killed producer whose `producerAlive` is stuck at `1` because no destructor ran. New in V2. |
| 60 | 60 | `reserved[60]` | Must be zero. May be repurposed in future protocol versions. |

The `latestSequence`, `latestSlot`, and `producerAlive` fields are written by the producer with release ordering and read by consumers with acquire ordering. The reader algorithm in §4 explains the interlock. `producerPid` is a plain u32 — it's written once at start, before the release-store of `producerAlive`, so any reader that acquire-loads `producerAlive == 1` is guaranteed to also observe the matching `producerPid`.

### 2.2 SlotHeader + pixels (each slot)

Each slot occupies `slotStride` bytes laid out as:

```text
+----------------------------------------------+ offset 0
|                 SlotHeader                   | 64 bytes (cache-line aligned)
+----------------------------------------------+ offset 64
|         BGRA8 pixels (rowwise)               | pitchBytes * height bytes
+----------------------------------------------+
|              padding                         | up to slotStride
+----------------------------------------------+ slotStride
```

**SlotHeader (64 bytes):**

| Offset | Size | Field | Description |
|---:|---:|---|---|
| 0 | 8 | `sequence` | Little-endian u64, written with release / read with acquire. Matches `RegionHeader::latestSequence` at write completion. `0` = empty / in-flight. |
| 8 | 8 | `captureTimestampNs` | Producer-side capture timestamp (ns, monotonic clock). May be `0` if the producer didn't supply one. |
| 16 | 4 | `width` | Pixel width in this frame (≤ `maxWidth`). |
| 20 | 4 | `height` | Pixel height in this frame (≤ `maxHeight`). |
| 24 | 4 | `pitchBytes` | Row pitch in bytes (≤ `maxPitchBytes`). |
| 28 | 4 | `reserved` | Zero in V1. |
| 32 | 32 | `pad[32]` | Pad to 64 bytes. |

Slot pixels are BGRA8 (`B`, `G`, `R`, `A` bytes per pixel, in that order). The consumer's WebGL upload can use `gl.BGRA_EXT` directly on most platforms, or a one-line fragment-shader swizzle to remap to RGBA.

Per-slot dimensions support opportunistic resize: if a frame arrives at different dimensions than the previous frame, the new dimensions are stored in the slot header. Consumers should reallocate / resize their GPU textures when they observe a dimension change.

---

## 3. Producer write algorithm

Per `stream_video()` call (single producer, lock-free for readers):

1. Pick `nextSlot = (lastSlot + 1) % slotCount`.
2. Mark slot in-flight: `slot.sequence.store(0, release)`.
3. Copy pixel data into slot's pixel area (the reference implementation uses `cudaMemcpy2DAsync` device→host followed by `cudaStreamSynchronize`).
4. Write slot header fields: `width`, `height`, `pitchBytes`, `captureTimestampNs`.
5. Publish: `slot.sequence.store(newSeq, release)`.
6. Update region header: `latestSlot.store(nextSlot, release)`, then `latestSequence.store(newSeq, release)`.
7. Signal the wakeup primitive so blocked readers wake.

**Why `latestSlot` is stored before `latestSequence`:** the consumer's read algorithm in §4 snapshots `latestSequence` (step 4) and uses it as the freshness signal. Stores are release-ordered, so a reader observing the new `newSeq` is guaranteed to also see the matching `nextSlot` written in step 6 — the slot pointer is published *before* the sequence number that activates it. If the order were reversed, a reader could observe a fresh `newSeq` while still seeing the *previous* slot index, then read stale pixels from that slot and validate them against the old sequence (a false positive). The wakeup primitive in step 7 fires after both stores are visible, so blocked readers always wake to a consistent (slot, sequence) pair.

`newSeq` is monotonically increasing; the first frame of a server lifetime is sequence `1`.

---

## 4. Consumer read algorithm

Per `wait_frame(timeoutMs)`:

1. Maintain a per-client `lastObservedSequence` (initialized to `0`).
2. Read `header.latestSequence` (acquire); if `> lastObservedSequence`, proceed to step 4.
3. Otherwise wait on the wakeup primitive (with `timeoutMs`). On wake, loop to step 2. If the producer is no longer alive — either `header.producerAlive.load(acquire) == 0` (clean shutdown) or, with the flag still `1`, a `processExists(header.producerPid)` probe returns false (hard kill) — return failure. The PID probe runs only in this cold path, never on the hot observe-a-fresh-frame path, so frame-rate cost is zero.
4. Snapshot `seq = header.latestSequence` (acquire) and `slotIdx = header.latestSlot` (acquire).
5. Read `slot[slotIdx].sequence` (acquire) → if it doesn't equal `seq`, the producer wrapped around mid-publish. Loop to step 2.
6. Read slot header fields and pixel data.
7. Re-read `slot[slotIdx].sequence` (acquire). If it has changed since step 5, the producer overwrote this slot during the read. Loop to step 2.
8. Set `lastObservedSequence = seq` and return the frame view.

**Slow-reader semantics:** if the producer rotates faster than the reader consumes, frames are silently dropped — the reader observes a `lastObservedSequence` jump from N to (e.g.) N+5. This is correct behavior for an interactive 60 Hz stream where "always show the latest" is the goal.

---

## 5. Control channel protocol

The control channel is line-delimited UTF-8 (terminator: `\n`, optional preceding `\r` ignored). **No JSON.** It carries client lifecycle only — frame data never traverses this channel.

Two line-length tiers apply:

- **Protocol-verb lines** (`ATTACH`, `ATTACH_OK`, `ATTACH_REJECTED`, `DETACH`, `STOPPED`, `INPUT_KEY`, `INPUT_MOUSE`, `INPUT_GAMEPAD`) are ≤ 256 bytes. Receivers can read into a fixed 256-byte buffer.
- **Data-bearing lines** (`MESSAGE`, `UNICODE`) have no public size cap. The reference implementation grows its line buffer up to a 16 MiB defense-in-depth ceiling per line; third-party readers should size their buffers accordingly (a small fixed window won't suffice once the producer sends a large `MESSAGE`).

### 5.1 Client → Server

| Line | Meaning |
|---|---|
| `ATTACH protocolVersion=<u32>` | Client requests attachment. Sent once after connecting. Subsequent ATTACH lines on the same connection are ignored. |
| `DETACH` | Clean detach. The server treats subsequent connection close as already-detached. |
| `INPUT_KEY keyCode=<u32> scanCode=<u32> modifiers=<u32> state=<u32> tsUs=<u64>` | Keyboard event. `state` is `0`=up / `1`=down; `modifiers` is an opaque `uint16` bitmask (Shift / Ctrl / Alt / Meta — the exact bit assignment is producer-side and not part of the wire protocol). Dispatched to the producer's input callback as an `OVSTREAM_INPUT_KEYBOARD` event. |
| `INPUT_MOUSE type=<u32> modifiers=<u32> x=<i32> y=<i32> data=<i32> data2=<i32> btnState=<u32> tsUs=<u64> scrollX=<float> scrollY=<float>` | Mouse event. `type` selects `move` / `button` / `wheel`; field interpretation mirrors `ovstream_mouse_event_t`. Floats use `%.9g` so single-precision values round-trip losslessly. |
| `INPUT_GAMEPAD control=<u32> position=<i32> gamepadId=<u32> tsUs=<u64>` | Gamepad axis / button event. `control` indexes `ovstream_gamepad_control_t`; `position` is the per-control value. |
| `MESSAGE <utf8>` | Free-form client→server text message. Producer receives it via the callback registered with `ovstream_set_message_callback`. |
| `UNICODE <utf8>` | IME / composed-text event. Producer receives it via the callback registered with `ovstream_set_unicode_callback`. |

Connection close (FIN / EOF / pipe break) is also treated as detach.

### 5.2 Server → Client

| Line | Meaning |
|---|---|
| `ATTACH_OK protocolVersion=<u32> slotCount=<u32>` | Successful attach. Reports the negotiated protocol version and the region's ring depth. |
| `ATTACH_REJECTED reason=<text>` | Attach refused; server closes the connection. (Not currently emitted by the V1 server but reserved for future use.) |
| `MESSAGE <utf8>` | Free-form server→client text message produced by `ovstream_send_message`. Broadcast to every attached client. Delivered to the client via the callback registered with `ovstream_shm_client_set_message_callback`. |
| `STOPPED` | Reserved. The V1 server does **not** emit this line — clients detect producer stop via the `producerAlive` atomic in the region header (which the producer clears before tearing down the control endpoint) plus the wakeup signal that fires alongside it. The line stays reserved so a future protocol revision can opt into it without ABI churn. |

Forward-compatibility: unknown lines from either side are ignored. Adding new line types in a future minor protocol revision is non-breaking as long as existing lines retain their format.

**Payload constraint:** V1 does not escape `\n` or `\r` inside `MESSAGE` / `UNICODE` payloads. Senders that need to carry literal newlines must encode them first (e.g. base64 or `\\n` substitution at the application layer). Both the server and the client reject outgoing payloads containing `\n` / `\r` with an error rather than silently truncating.

**`INPUT_*` field formats:** all integer fields are base-10 ASCII (`%u` / `%d` / `%llu`). Floats use `%.9g` and accept any C-locale-parseable representation on the receive side (`strtof`). Receivers parse left-to-right and reject the whole line on any malformed key, missing value, or out-of-range enum.

### 5.3 Connection lifecycle

The server tracks attached client count atomically. The transition `0 → 1` (first attach) and `N → 0` (last detach) drive the user-facing connection callback set via `ovstream_set_connection_callback`. Intermediate transitions are not surfaced.

---

## 6. Versioning

The `protocolVersion` field in both the region header and the `ATTACH`/`ATTACH_OK` handshake gates compatibility. A client that reads a region whose `protocolVersion` doesn't match its own constant **must refuse to attach** and report an error. The same applies to a server receiving an `ATTACH protocolVersion=N` where N differs from its own — though the current server is lenient (it sends `ATTACH_OK` and lets the client decide).

Adding fields to `RegionHeader::reserved` or the slot pad is a **breaking change**: bump `protocolVersion`. Adding new control-channel lines is **non-breaking** as long as existing lines are unchanged: the `INPUT_KEY` / `INPUT_MOUSE` / `INPUT_GAMEPAD` / `MESSAGE` / `UNICODE` lines were added under V1 after the initial shipping of `ATTACH` / `DETACH` / `ATTACH_OK`, with no `protocolVersion` bump.

### 6.1 Version history

| Version | Change |
|---:|---|
| 1 | Initial format. |
| 2 | Added `producerPid` (4 bytes) to `RegionHeader`, consuming 4 bytes of the trailing reserved padding. Enables consumer-side hard-kill detection via a read-only process-existence probe; see §4 step 3 and `core/process.h::processExists`. No SlotHeader or control-channel changes. |

---

## 7. Consumer-facing API

For consumers attaching to a SHM stream, the SDK ships two thin
wrappers over the wire protocol specified above:

| Language | Entry point |
|---|---|
| C | `include/ovstream/ovstream_shm_client.h` (see its Doxygen blocks for the per-function contract) |
| Python | `ovstream.ShmClient` from the `ovstream` PyPI package |

Both are convenience layers, not separate sources of truth — the
bytes-on-the-wire contract here is the actual ABI. Writing a
third-party client in any language is supported as long as it
follows this document.


# Frame pacing utility (ovstream_utils)

> `ovstream_utils` is auxiliary functionality bundled with `ovstream` for the convenience of streaming examples and apps that need accurate frame pacing. **It is not required to use `ovstream`** -- apps can use ovstream's API surface entirely without ever touching `ovstream_utils`. The pacing utility is provided here because correct frame pacing is non-trivial and rolling it from scratch in every example produces inconsistent (and often broken) cadence. If demand grows beyond the streaming use case, `ovstream_utils.Loop` may eventually be promoted to a standalone library; for now it lives here to keep ovstream's public API focused on streaming.


How this utility is shaped, why, and what each public entry point
does. For usage examples, see
[`examples/python/basic_stream/main.py`](../examples/python/basic_stream/main.py)
and [`examples/c/starfield_stream/main.cu`](../examples/c/starfield_stream/main.cu);
both use `ovstream_utils.Loop` for pacing.

---

## Three surfaces, one algorithm

ovstream_utils exposes three public surfaces (the Loop API; future utilities will follow the same pattern):

```text
+------------------------------+   +---------------------------------+   +------------------+
| <ovstream_utils/loop.h>      |   | <ovstream_utils/loop.hpp>       |   | ovstream_utils   |
| C ABI                        |   | header-only C++                 |   | Python           |
| (canonical)                  |   | (additive)                      |   | (ctypes)         |
+--------------+---------------+   +---------------+-----------------+   +--------+---------+
               |                                   |                              |
               v                                   v                              v
   ovstream_utils.dll /                  ovstream_utils::Loop             ctypes -> .dll/.so
   libovstream_utils.so                  (stack RAII)
               ^                                   ^
               |                                   |
               +-----------------+-----------------+
                                 |
                                 | (single source of truth: the C++ class)
                                 |
                       +---------v-----------------+
                       | ovstream_utils::Loop in   |
                       | <ovstream_utils/loop.hpp> |
                       +---------------------------+
```

The C ABI is the canonical surface. The C++ header is purely
additive, and Python is a peer consumer of the C ABI.

### Why this split?

- **C ABI as canonical** matches the OV Libraries SRD V1
  requirements: a small, stable C contract that any ABI-oriented
  language consumer can use, plus a Python ctypes wrapper for the
  primary stakeholder. This is the contract the API committee
  reviews and the wheel ships against.
- **C++ as additive** because native C++ consumers benefit
  significantly from RAII + stack allocation + named member access,
  and because the algorithm itself is naturally expressed as a small
  C++ class. Forcing those consumers through `create`/`destroy` and
  opaque handles would buy nothing.
- **Header-only C++** because `ovstream_utils::Loop` has no implementation
  state outside its members — there's nothing to compile separately.
  Header-only also means consumers who use only the `.hpp` surface
  don't need to link anything at all.

The two surfaces share **identical** data types via C++ `using`
aliases:

```cpp
class Loop
{
    using Config = ::ovstream_utils_loop_config_t;
    using Stats  = ::ovstream_utils_stats_t;
    using Tick   = ::ovstream_utils_tick_t;
    // ...
};
```

so they cannot drift. The C ABI implementation is a thin wrapper
around an instance of `ovstream_utils::Loop`:

```cpp
struct ovstream_utils_loop
{
    ovstream_utils::Loop impl;
};
```

That's the entire boundary between the canonical contract and its
internal implementation.

---

## API reference

Each entry shows the signature on every surface plus a short
description. The C ABI is the canonical contract; the C++ class and
the Python wrapper expose the same algorithm with surface-appropriate
ergonomics.

### Library-scope

`ovstream_utils` deliberately does not expose its own version. The
Loop algorithm ships and is versioned as part of ovstream's wheel;
use `ovstream.get_version()` if a consumer needs to know what
they're running against. Reintroducing a per-utility version would
re-create the "independent library" framing that the namespace was
designed to avoid.

#### `now_ns`

Read the Loop monotonic clock in nanoseconds. Same clock used to
populate `Tick::startTimeNs`, so values from this function and
values inside `Tick` are directly comparable. Backed by
`std::chrono::steady_clock`. Monotonic by definition; not a
wall-clock — use the OS API directly if you need real-world time.

| Surface | Signature |
|---|---|
| C | `uint64_t ovstream_utils_now_ns(void)` |
| C++ | `uint64_t ovstream_utils::now_ns() noexcept` |
| Python | `ovstream_utils.now_ns() -> int` |

### Per-loop

#### `config_defaults`

Fill a config struct with all-zero defaults (uncapped pacing, no
fixed-step bookkeeping). Equivalent to value-initialising the struct.

| Surface | Signature |
|---|---|
| C | `void ovstream_utils_loop_config_defaults(ovstream_utils_loop_config_t* o_config)` |
| C++ | `ovstream_utils::Loop::Config{}` (no helper needed; default constructor zero-initialises) |
| Python | `ovstream_utils.LoopConfig()` (default-constructed) |

C-side: passing `NULL` is a defensive no-op.

#### `Loop::Loop` / `ovstream_utils_loop_create`

Create one paced loop and capture its initial clock baseline. The
first `tick()` after creation returns zero `dt` and no fixed-step
work to perform.

| Surface | Signature |
|---|---|
| C | `void ovstream_utils_loop_create(const ovstream_utils_loop_config_t* a_config, ovstream_utils_loop_t** o_loop)` |
| C++ | `explicit ovstream_utils::Loop::Loop(const Config& a_config = Config{}) noexcept` |
| Python | `ovstream_utils.Loop(config: LoopConfig | None = None)` |

C-side: passing `NULL` for `a_config` produces a zero-initialised
config (uncapped, no fixed step). Allocation failure aborts the
process — there is no fallibility surface (see "Errors" below).

#### `Loop::reset` / `ovstream_utils_loop_reset`

Re-baseline the loop state while keeping the current configuration.
Clears the accumulator, the frame index, and the rolling stats; the
next `tick()` returns `frameIndex == 0`, `deltaTimeSeconds == 0.0`,
and `fixedStepCount == 0`.

| Surface | Signature |
|---|---|
| C | `void ovstream_utils_loop_reset(ovstream_utils_loop_t* a_loop)` |
| C++ | `void ovstream_utils::Loop::reset() noexcept` |
| Python | `Loop.reset()` |

C-side: passing `NULL` for `a_loop` is a safe no-op.

#### `Loop::reconfigure` / `ovstream_utils_loop_reconfigure`

Install a new configuration and re-baseline state in one call.
Carrying accumulator state across a rate change would interpret
old-rate elapsed time at the new rate, so both the cached config-
derived scalars and the live state (accumulator, frame index,
deadline anchor, rolling stats) are reset together. Callers wanting
stats continuity across a config change should snapshot
`Tick::stats` themselves before calling.

| Surface | Signature |
|---|---|
| C | `void ovstream_utils_loop_reconfigure(ovstream_utils_loop_t* a_loop, const ovstream_utils_loop_config_t* a_config)` |
| C++ | `void ovstream_utils::Loop::reconfigure(const Config& a_config) noexcept` |
| Python | `Loop.reconfigure(config: LoopConfig)` |

C-side: passing `NULL` for either `a_loop` or `a_config` is a safe
no-op. To re-baseline without changing the configuration, call
`reset` instead.

#### `Loop::tick` / `ovstream_utils_loop_tick`

Advance one tick. When `fpsTarget > 0`, blocks until the next frame
deadline; then takes a measurement, updates the fixed-step
accumulator, and returns the per-tick budget.

| Surface | Signature |
|---|---|
| C | `void ovstream_utils_loop_tick(ovstream_utils_loop_t* a_loop, ovstream_utils_tick_t* o_tick)` |
| C++ | `Tick ovstream_utils::Loop::tick() noexcept` |
| Python | `Loop.tick() -> Tick` |

C-side: passing `NULL` for either `a_loop` or `o_tick` is a safe
no-op (`o_tick` is left untouched in that case).

Returns / writes:

| Field | Meaning |
|---|---|
| `deltaTimeSeconds` | Variable/render dt; raw measured wall-clock between ticks. |
| `fixedTimeSeconds` | `1/fixedStepHz`, or `0.0` when fixed steps disabled. |
| `fixedTimeAlpha` | `[0.0, 1.0)`; fraction of a fixed step in the accumulator. |
| `fixedStepCount` | Fixed-step updates owed this tick, capped at `fixedStepMax`. |
| `startTimeNs` | Monotonic ns at tick start. |
| `frameIndex` | Zero-based frame counter since the last reset. |
| `stats.fpsCurrent` | Frames in the last 1 s window. |
| `stats.fpsAverage` | Mean FPS since the last reset. |
| `stats.frameTotal` | Total ticks since the last reset. |
| `stats.fixedTotal` | Total fixed-step updates since the last reset. |

First tick after creation/reset returns `deltaTimeSeconds = 0`,
`fixedStepCount = 0`, and `fixedTimeAlpha = 0`.

#### `Loop::~Loop` / `ovstream_utils_loop_destroy` / `Loop.close`

Release the underlying handle. The C++ destructor is automatic; the
Python `Loop` exposes both an explicit `close()` and the context-
manager `__exit__`. C consumers must call `ovstream_utils_loop_destroy`.

| Surface | Signature |
|---|---|
| C | `void ovstream_utils_loop_destroy(ovstream_utils_loop_t* a_loop)` |
| C++ | implicit (RAII) |
| Python | `Loop.close()` / `with`-block exit |

C-side: passing `NULL` is a documented no-op. Double-destroy is
undefined behavior.

### Data types

#### `ovstream_utils_loop_config_t` / `ovstream_utils::Loop::Config` / `ovstream_utils.LoopConfig`

```c
typedef struct
{
    uint32_t fpsTarget;     // 0 = uncapped (no wait in tick)
    uint32_t fixedStepHz;   // 0 = disabled (no fixed step in tick)
    uint32_t fixedStepMax;  // 0 = library default of 8
} ovstream_utils_loop_config_t;
```

In C++ the same struct is exposed via `using Config = ::ovstream_utils_loop_config_t`.
The Python `LoopConfig` dataclass uses snake_case field names with
the same defaults.

#### `ovstream_utils_stats_t` / `ovstream_utils::Loop::Stats` / `ovstream_utils.Stats`

```c
typedef struct
{
    uint32_t fpsCurrent;    // frames in last 1 s
    uint32_t fpsAverage;    // mean fps since reset
    uint64_t frameTotal;    // total frames since reset
    uint64_t fixedTotal;    // total fixed steps since reset
} ovstream_utils_stats_t;
```

#### `ovstream_utils_tick_t` / `ovstream_utils::Loop::Tick` / `ovstream_utils.Tick`

```c
typedef struct
{
    float    deltaTimeSeconds;
    float    fixedTimeSeconds;
    float    fixedTimeAlpha;
    uint32_t fixedStepCount;
    uint64_t startTimeNs;
    uint64_t frameIndex;
    ovstream_utils_stats_t stats;
} ovstream_utils_tick_t;
```

C++ exposes the same struct; the Python `Tick` dataclass mirrors the
fields with snake_case and a nested `Stats`.

---

## Tick algorithm in detail

`tick()` runs in two phases: **wait**, then **measure**.

### Wait phase

The wait phase is what makes the Loop a frame *pacer* and not just a
clock. It runs only when:

1. The caller asked for pacing (`fpsTarget > 0`), and
2. We're not on the very first tick after construction or reset.

The deadline is computed from the *previous deadline*, not from
"now". This is the load-bearing trick that prevents long-term drift:

```text
deadlineNs = lastWaitDeadlineNs + targetFrameNs   (or lastTickStartTimeNs + targetFrameNs on the first paced tick)
```

If `now >= deadline` (we missed the slot), we re-anchor on `now` and
fall through to measurement immediately. **No artificial frame drop**
— this is a deliberate divergence from stricter pacers, because
adding a real-frame drop on top of a small overrun amplifies the
problem rather than fixing it.

If `now < deadline`, we sleep most of the slack and spin the last
500 µs:

```cpp
if (deadlineNs > nowBeforeSleep + kSpinThresholdNs)
{
    sleep_for(deadlineNs - nowBeforeSleep - kSpinThresholdNs);
}
while (now_ns() < deadlineNs)
{
    std::this_thread::yield();
}
```

The spin threshold trades CPU for accuracy: 500 µs is well above the
worst-case timer-resolution we observe on Windows without
`timeBeginPeriod(1)` (≈1 ms), and well below typical Linux/macOS
wakeup jitter. The spin yields rather than busy-spinning so we don't
starve other threads on the same core.

### Measurement phase

```cpp
const uint64_t startTimeNs = now_ns();
```

This is the timestamp the caller sees in `Tick::startTimeNs` and the
anchor for the next tick's deadline.

The variable / render dt is the raw measured wall-clock interval
since the previous tick:

```text
variableDeltaNs = startTimeNs - lastTickStartTimeNs
```

No internal clamp. Consumers that have to track real time — audio
sources, network keepalives, animation driving wall-clock-locked
timelines — need the unclamped value. Callers that need a stable
integrator under stalls (physics, deterministic simulation) should
drive that work off `fixedStepHz` + `fixedStepCount` instead, which
gives them a fixed timestep and a documented `fixedStepMax` cap to
break the spiral of death.

The fixed-step accumulator runs on the *raw* measured delta so a
caller's stall genuinely owes the work it would have owed at the
true fixed rate:

```text
accumulatorNs += rawDeltaNs
steps = accumulatorNs / fixedStepNs        # integer division
if steps > fixedStepMax:
    steps = fixedStepMax
    accumulatorNs = 0                      # spiral-of-death guard
else:
    accumulatorNs -= steps * fixedStepNs
```

The cap is the spiral-of-death guard. Without it, a 5-second
breakpoint at a 1000 Hz fixed step would owe 5,000 fixed updates on
the next tick — almost certainly worse than the original stall.

`fixedTimeAlpha` is what's *left* in the accumulator after the
integer division, expressed as a fraction of one fixed step:

```text
fixedTimeAlpha = accumulatorNs / fixedStepNs   # in [0.0, 1.0)
```

This is the interpolation parameter from Glenn Fiedler's "Fix Your
Timestep" pattern; render code lerps between the last two fixed
states using it.

---

## Stats: rolling vs cumulative

Two separate measures, both updated on every tick:

- **`fpsCurrent`** — frames whose `startTimeNs` falls in the trailing
  1-second window. Backed by a 512-entry ring buffer (covers ~17 s at
  30 FPS, ~2.1 s at 240 FPS). This is the right thing to watch in a
  stats overlay.
- **`fpsAverage`** — cumulative average since the last reset:
  `frameTotal / (now - resetMonoNs)`. This is the right thing to
  report in a benchmark summary.

`fixedTotal` is just the running sum of `fixedStepCount` values
returned from each tick.

---

## Threading model

ovstream_utils does not own a thread. There is no scheduler, no worker pool,
no callback dispatch. The only place the Loop yields control to the OS
is inside the `tick()` wait phase (when `fpsTarget > 0`), via
`std::this_thread::sleep_for` and `std::this_thread::yield`.

A single `ovstream_utils::Loop` / `ovstream_utils_loop_t*` is not thread-safe.
Concurrent `tick` / `reconfigure` / `reset` on the same instance is
undefined behavior. Internal locking is intentionally absent because
it would either be useless (calling `tick()` concurrently from two
threads makes no sense — only one of them can possibly drive the
cadence) or misleading (it would make some race-y patterns silently
work while subtler ones still corrupted state).

Distinct loops are fully independent. Two threads each ticking their
own loop is safe and tested.

---

## Memory model

- `ovstream_utils_loop_t*` is heap-allocated (`new`) inside
  `ovstream_utils_loop_create`. Allocation failure aborts the process; there
  is no try-allocate variant.
- `ovstream_utils::Loop` is value type, intended to live on the stack or as a
  member of another class. It allocates nothing on the heap (the
  ring buffer is a `std::array<uint64_t, 512>` member).
- The Python `Loop` holds the C handle in `_handle` and releases it
  in `close()` / `__exit__` / `__del__`.

`sizeof(ovstream_utils::Loop)` is roughly 4.2 KB on a 64-bit target — the
ring buffer dominates. If that's too much for embedded use, the cap
constant lives in one place (`ovstream_utils::Loop::kRecentFrameTimesCapacity`)
and can be tuned at build time via a fork patch.

---

## Errors

ovstream_utils has no runtime fallibility surface. There is no
`getLastError`, no `bool`-returning entry points, and no exception
type the C ABI can raise. The Python wrapper exposes
`ovstream_utils.OvstreamUtilsError` for state-machine misuse such as calling
`tick()` on a closed `Loop`, but that is a Python-side guard, not a
library error channel.

Caller bugs (NULL pointers outside documented exceptions, double-
destroy, calls on a freed handle, concurrent access on the same
loop) are undefined behavior. Allocation failure inside
`ovstream_utils_loop_create` aborts the process.

---

## What ovstream_utils deliberately is not

- **Not a scheduler.** Single loop, polled by the caller. The OV
  Libraries want application developers to have explicit control
  of the entire execution loop/pattern for the application.
- **Not a profiler.** `Stats` is for runtime feedback (overlay,
  health checks); use Tracy / NSight for hot-path analysis.
- **Not a wall-clock service.** `now_ns()` is monotonic only.
- **Not a `Run(callback)` library.** Conflicts with OV Libraries.
- **Not multi-threaded.** Single-threaded by design. Distinct loops
  on distinct threads are fine; concurrent access on the same loop
  is UB.

These choices keep the surface tiny and the implementation honest.
Adding any of these would push the library into a different design
space; the goal is a smaller library that does one thing well.
