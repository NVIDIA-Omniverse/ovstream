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
name: error-handling
description: Error checking patterns for both C and Python plus logging. Use when user asks about error handling, debugging ovstream failures, registering a log callback, or troubleshooting.
---

# Error Handling

## Overview

In Python, ovstream raises `ovstream.OvstreamError` with descriptive messages on failure. The exception carries the C-side status code on `.status` (an `ovstream.ApiStatus` enum) so callers can pattern-match on expected failures.

In C, every API call returns `ovstream_result_t`. Inspect the `.status` field for `OVSTREAM_API_SUCCESS` (or use the `OVSTREAM_OK(r)` convenience macro), and on failure call `ovstream_get_last_error()` (thread-local) for the human-readable detail string. Status codes: `OVSTREAM_API_SUCCESS`, `_ERROR`, `_TIMEOUT`, `_INVALID_ARGUMENT`, `_INVALID_STATE`, `_NOT_SUPPORTED`.

A separate logging mechanism — the `log_callback` callback registered at `initialize` time — captures non-fatal warnings, info, and verbose chatter from the SDK and its dependencies (StreamSDK, GStreamer).

## Python

```python
try:
    ovstream.initialize(log_min_severity=ovstream.LogLevel.WARNING)
    with ovstream.Server(ovstream.ServerType.WEBRTC) as server:
        server.start(ovstream.ServerConfig(width=1920, height=1080))
        # ... stream ...
except ovstream.OvstreamError as e:
    if e.status == ovstream.ApiStatus.NOT_SUPPORTED:
        pass  # e.g. send_message on an RTSP server, or audio on RTSP/SHM
    else:
        print(f"ovstream error [{e.status.name}]: {e}", file=sys.stderr)
```

> **Source:** `examples/python/basic_stream/main.py` snippet `initialize-sdk`

`stream_video` can raise on a per-frame basis (e.g. invalid frame, server not started). Most of the time this is benign (just no client connected yet, and backends usually accept early frames silently). Wrap the call in `try/except OvstreamError: pass` if you're streaming a polling/idle pattern and want to swallow transient setup-time failures.

## C

> **Source:** `examples/c/basic_stream/main.cu` snippet `initialize-sdk`

The pattern in basic_stream uses `ovstream_result_t` returns + `OVSTREAM_OK(r)` + `ovstream_get_last_error()`:

```cpp
if (!OVSTREAM_OK(ovstream_start(server, &cfg)))
{
    fprintf(stderr, "ovstream_start failed: %s\n", ovstream_get_last_error().ptr);
    ovstream_destroy_server(server);
    ovstream_shutdown();
    return 1;
}
```

If you need to branch on the specific status:

```cpp
ovstream_result_t r = ovstream_stream_video(server, &frame);
switch (r.status)
{
    case OVSTREAM_API_SUCCESS:                                         break;
    case OVSTREAM_API_INVALID_STATE: /* server not started yet, skip */ break;
    default:
        fprintf(stderr, "stream_video failed: %s\n", ovstream_get_last_error().ptr);
        return 1;
}
```

`ovstream_get_last_error()` returns an `ovstream_string_t` view into thread-local storage. The pointer is valid until the next ovstream call on this thread (which may overwrite it). Copy the bytes if you need to retain the string.

A helper template like this is convenient for repeated check-and-print:

```cpp
static bool check(ovstream_result_t r, std::string_view operation) {
    if (!OVSTREAM_OK(r)) {
        auto err = ovstream_get_last_error();
        std::cerr << "ovstream " << operation << " failed: "
                  << std::string_view(err.ptr, err.length) << "\n";
        return true;
    }
    return false;
}
```

## Log callback

Register a log callback at `initialize` time to receive non-error messages:

> **Source:** `examples/c/basic_stream/main.cu` snippet `log-callback`

The callback fires for SDK-internal messages **plus** anything GStreamer / StreamSDK log. Use `log_min_severity` to filter — `OVSTREAM_LOG_VERBOSE` is firehose-level, `OVSTREAM_LOG_WARNING` is the recommended default for production, and `OVSTREAM_LOG_DEFAULT` (= 0, the value a `{0}`-initialized config produces) is also remapped to WARNING by the SDK so zero-init is quiet by default. `OVSTREAM_LOG_NONE` suppresses every line including errors.

In Python, pass `log_fn=` to `ovstream.initialize`:

```python
ovstream.initialize(
    log_fn=lambda level, channel, message, timestamp: print(f"[{level.name}][{channel}] {message}"),
    log_min_severity=ovstream.LogLevel.WARNING,
)
```

The Python `log_fn` is invoked from internal SDK threads — same threading caveat as the connect/message callbacks.

## Log callback semantics

The log callback is only installed on the very first `initialize` of an init/shutdown lifecycle (the 0→1 ref-count transition). Subsequent `initialize` calls while the ref-count is non-zero **ignore** their `log_fn` argument because the C side does not support replacing the callback while the library is initialized.

To change logging configuration, take the ref-count to zero with matching `shutdown` calls, then re-`initialize`.

## Key Types / Functions

| Python | C |
|--------|---|
| `ovstream.OvstreamError` (`.status` is an `ApiStatus`) | `ovstream_result_t` + `OVSTREAM_OK(r)` + `ovstream_get_last_error()` |
| `ovstream.ApiStatus.{SUCCESS, ERROR, TIMEOUT, INVALID_ARGUMENT, INVALID_STATE, NOT_SUPPORTED}` | `OVSTREAM_API_{SUCCESS, ERROR, TIMEOUT, INVALID_ARGUMENT, INVALID_STATE, NOT_SUPPORTED}` |
| `ovstream.LogLevel.{DEFAULT, VERBOSE, INFO, WARNING, ERROR, NONE}` | `OVSTREAM_LOG_{DEFAULT, VERBOSE, INFO, WARNING, ERROR, NONE}` |
| `ovstream.initialize(log_fn=..., log_min_severity=...)` | `ovstream_init_config_t { .log_callback = ..., .log_min_severity = ... }` |

## Common Pitfalls

- **`ovstream_get_last_error` is per-thread.** Don't read it from a thread other than the one that made the failing call.
- **Don't free the error string.** It's owned by the SDK's thread-local storage.
- **A non-SUCCESS return from `stream_video` is often benign** — frames pushed before a client connects are typically accepted silently; if a failure does surface, log at verbose and continue. Save `ERROR`-level handling for `initialize`, `create_server`, `start`.
- **Python destructor errors are swallowed.** `Server.__del__` catches and discards any exception from the implicit `close()` so GC never raises an unraisable-exception warning and the init/shutdown ref-count stays paired. The trade-off is that you won't see a destroy-time failure if you skip explicit cleanup. Always prefer explicit `server.close()` or a `with` block when you need to surface teardown errors.
- **StreamSDK is conservative about ERROR level.** A lot of what StreamSDK logs as ERROR is recoverable; ovstream remaps these to WARN to reduce noise. If you see a torrent of warnings during a normal shutdown, that's expected.
