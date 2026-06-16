// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: LicenseRef-NvidiaProprietary
//
// NVIDIA CORPORATION, its affiliates and licensors retain all intellectual
// property and proprietary rights in and to this material, related
// documentation and any modifications thereto. Any use, reproduction,
// disclosure or distribution of this material and related documentation
// without an express license agreement from NVIDIA CORPORATION or
// its affiliates is strictly prohibited.

// Reader-side library for the OVSTREAM SHM transport. Same-machine
// consumers (Electron N-API addons, native test harnesses, Python
// scripts via ctypes) attach to a running ovstream SHM server via
// `ovstream_shm_client_create` and pull frames with
// `ovstream_shm_client_wait_frame`.
//
// Pixels are delivered as zero-copy pointers into the shared region;
// the consumer is responsible for uploading them to its own GPU /
// rendering surface (e.g. WebGL `texImage2D`) before the next
// `wait_frame` call rotates the producer onto the same slot.
//
// This header is independent of `ovstream.h`: a client process does
// not have to link the full server-side ovstream library, only the
// `ovstream_shm_client` shared library that ships alongside it. The
// only types it shares with the server are the `ovstream_string_t`
// and pixel-format constants from `ovstream_types.h`.
//
#pragma once

#include <ovstream/ovstream_types.h>

// Visibility / linkage for the public C symbols below. Three build
// configurations are supported, selected by which preprocessor
// macros are defined:
//
//   1. Main `ovstream` shared library build (defines `OVSTREAM_BUILD`):
//      symbols are dllexported / default-visibility, so they show up
//      in the produced .dll / .so alongside `ovstream_*` server APIs.
//
//   2. Consumer linking the slim `ovstream_shm_client` static library
//      (defines `OVSTREAM_SHM_CLIENT_STATIC`): no linkage decoration,
//      since the symbols come from a .lib / .a archive.
//
//   3. Consumer linking the main `ovstream` shared library: the
//      symbols are dllimported on Windows / default-visibility on
//      Linux. This is the same posture as the server-side
//      `ovstream_*` functions in `ovstream.h`.
#if defined(OVSTREAM_SHM_CLIENT_STATIC)
#   define OVSTREAM_SHM_CLIENT_API
#elif defined(_WIN32)
#   ifdef OVSTREAM_BUILD
#       define OVSTREAM_SHM_CLIENT_API __declspec(dllexport)
#   else
#       define OVSTREAM_SHM_CLIENT_API __declspec(dllimport)
#   endif
#else
#   define OVSTREAM_SHM_CLIENT_API __attribute__((visibility("default")))
#endif

#ifdef __cplusplus
extern "C"
{
#endif

//--------------------------------------------------------------
// @defgroup shm_client SHM client
// Consumer-side library for attaching to an SHM ovstream producer
// and pulling frames + control messages from shared memory. Lives
// in its own group because it's a separate consumer-side surface
// from the server-side `ovstream_*` API in `ovstream.h`.
//--------------------------------------------------------------

//--------------------------------------------------------------
// @brief Return the most recent error message on the calling thread.
//
// Mirrors the declaration in `ovstream.h` so consumers of the
// slim `ovstream_shm_client` static library (which intentionally
// don't include `ovstream.h`) can still retrieve diagnostics
// referenced in the `@return` blocks below.
//
// Output-string contract: `ptr` is non-NULL and null-terminated,
// `length` is 0 (and `ptr[0] == '\0'`) when no error is set on
// this thread. Valid until the next ovstream call on this thread
// or until the thread terminates. Do NOT `free()` the pointer.
//
// Consumers who include BOTH `ovstream.h` AND this header will
// see two declarations of the same function with potentially
// different linkage attributes (`dllimport` vs the slim
// linkage); they are compatible at the symbol level and most
// compilers tolerate this with at most a warning. The recommended
// usage is to include exactly one of the two headers per
// translation unit.
//
// @ingroup shm_client
//--------------------------------------------------------------
OVSTREAM_SHM_CLIENT_API ovstream_string_t ovstream_get_last_error(void);

//--------------------------------------------------------------
// @brief Opaque SHM client handle.
//
// Created by `ovstream_shm_client_create`, destroyed by
// `ovstream_shm_client_destroy`.
//--------------------------------------------------------------
typedef struct ovstream_shm_client_t ovstream_shm_client_t;

//--------------------------------------------------------------
// @brief Frame view returned by `ovstream_shm_client_wait_frame`.
//
// `data` points into the shared-memory region; see `wait_frame`'s
// docstring for the precise lifetime rules. In short: the
// producer may overwrite this slot at any time after `wait_frame`
// returns; copy out the bytes before any operation that might
// outrun a single frame interval. `format` is one of the
// `OVSTREAM_SHM_FORMAT_*` constants from `ovstream_types.h`
// (currently always `OVSTREAM_SHM_FORMAT_BGRA8`).
//--------------------------------------------------------------
typedef struct
{
    const void* data;
    uint64_t    sequence;
    uint64_t    capture_timestamp_ns;
    uint32_t    width;
    uint32_t    height;
    uint32_t    pitch_bytes;
    uint32_t    format;
} ovstream_shm_frame_t;

//--------------------------------------------------------------
// @brief Attach to a running ovstream SHM server.
//
// `stream_name` must match the value the server was started with
// (or the auto-default `"ovstream-<pid>"` if the producer left it
// unset). On success the client is attached and the server's
// connection callback fires.
//
// @par Example
// @code
//   const char* name = "my-stream";
//   ovstream_string_t stream_name = { name, strlen(name) };
//   ovstream_shm_client_t* client = NULL;
//   ovstream_shm_client_create(stream_name, &client);
//   // ... wait_frame loop ...
//   ovstream_shm_client_destroy(client);
// @endcode
//
// @param stream_name Input-string view; empty (`length == 0`) is
//                    rejected.
// @param out_client  [out] Receives the new client handle. Never
//                    written to on failure.
//
// @return
//   - `OVSTREAM_API_SUCCESS` on success.
//   - `OVSTREAM_API_INVALID_ARGUMENT` if `stream_name` is empty
//     or `out_client` is NULL.
//   - `OVSTREAM_API_ERROR` if the producer isn't running, the
//     name doesn't match, or the shared region was created by an
//     incompatible protocol version. See `ovstream_get_last_error`.
//
// @ingroup shm_client
//--------------------------------------------------------------
OVSTREAM_SHM_CLIENT_API ovstream_result_t ovstream_shm_client_create(
    ovstream_string_t stream_name,
    ovstream_shm_client_t** out_client);

//--------------------------------------------------------------
// @brief Detach and free a SHM client handle.
//
// Unmaps the shared region and closes the control channel. The
// server observes the detach via control-channel close and fires
// its connection callback if this was the last attached client.
// Passing NULL is a safe no-op (returns SUCCESS). The handle is
// invalid after this call returns; set the caller's pointer to
// NULL.
//
// @param client The client handle to destroy, or NULL.
//
// @return
//   - `OVSTREAM_API_SUCCESS` on success (including the NULL no-op).
//
// @ingroup shm_client
//--------------------------------------------------------------
OVSTREAM_SHM_CLIENT_API ovstream_result_t ovstream_shm_client_destroy(ovstream_shm_client_t* client);

//--------------------------------------------------------------
// @brief Wait for the next frame newer than the one previously returned.
//
// Per-client cursor; multiple clients independently observe the
// producer stream.
//
// @par Example
// @code
//   for (;;) {
//       ovstream_shm_frame_t f;
//       ovstream_result_t r = ovstream_shm_client_wait_frame(
//           client, 500, &f);
//       if (OVSTREAM_OK(r)) {
//           // ... read f.data (read-only view; copy before next call) ...
//       }
//   }
// @endcode
//
// @par Ownership / Lifetime
// `out_frame->data` is a read-only pointer into the shared region.
// The producer rotates slots asynchronously and may overwrite this
// slot at any time after `wait_frame` returns. The function itself
// detects mid-read overwrite via a sequence-number recheck and
// discards the read on a conflict, so the bytes are stable at the
// moment of return -- but copy out any bytes that need to outlive
// the next produced frame.
//
// @param client     The client handle.
// @param timeout_ms Wait policy: 0 = non-blocking poll; <0 = wait
//                   indefinitely; >0 = wait up to that many
//                   milliseconds.
// @param out_frame  [out] Receives the frame view on success.
//                   Untouched on failure.
//
// @return
//   - `OVSTREAM_API_SUCCESS` with `*out_frame` populated when a
//     new frame is available.
//   - `OVSTREAM_API_TIMEOUT` when the wait window elapses without
//     a new frame.
//   - `OVSTREAM_API_INVALID_STATE` when the producer has stopped
//     (also surfaces via
//     `ovstream_shm_client_is_producer_alive`).
//   - `OVSTREAM_API_INVALID_ARGUMENT` if `client` or `out_frame`
//     is NULL.
//   - `OVSTREAM_API_ERROR` for unexpected read failures.
//
// @ingroup shm_client
//--------------------------------------------------------------
OVSTREAM_SHM_CLIENT_API ovstream_result_t ovstream_shm_client_wait_frame(
    ovstream_shm_client_t* client,
    int32_t timeout_ms,
    ovstream_shm_frame_t* out_frame);

//--------------------------------------------------------------
// @brief Release the last frame returned by `wait_frame` (no-op in V1).
//
// Kept for ABI symmetry with future protocol versions that may
// want explicit reader-side bookkeeping. In V1 the protocol holds
// no per-frame consumer-side reservation (see the `wait_frame`
// ownership block). Calling it is harmless; not calling it is
// correct.
//
// @param client The client handle.
//
// @return
//   - `OVSTREAM_API_SUCCESS` always (no-op).
//   - `OVSTREAM_API_INVALID_ARGUMENT` if `client` is NULL.
//
// @ingroup shm_client
//--------------------------------------------------------------
OVSTREAM_SHM_CLIENT_API ovstream_result_t ovstream_shm_client_release_frame(ovstream_shm_client_t* client);

//--------------------------------------------------------------
// @brief Query whether the producer is still alive and serving frames.
//
// `*out_alive` is true if the producer is still running, false
// once it has called `ovstream_stop` or exited. Cheap (no I/O);
// safe to call from a polling loop.
//
// @param client    The client handle.
// @param out_alive [out] Receives the liveness state.
//
// @return
//   - `OVSTREAM_API_SUCCESS` on success.
//   - `OVSTREAM_API_INVALID_ARGUMENT` if `client` or `out_alive`
//     is NULL.
//
// @ingroup shm_client
//--------------------------------------------------------------
OVSTREAM_SHM_CLIENT_API ovstream_result_t ovstream_shm_client_is_producer_alive(
    const ovstream_shm_client_t* client,
    bool* out_alive);

//--------------------------------------------------------------
// @brief Send an input event to the producer over the control channel.
//
// The producer receives it via the same callback it registered
// with `ovstream_set_input_callback` for any other transport, so
// producer code does not change between WebRTC, native, and SHM.
// Mouse / keyboard / gamepad events are encoded line-by-line on
// the control channel; 1 kHz+ event rates remain comfortably
// within the channel's throughput on every supported platform.
//
// @par Thread-Safety
// Safe to call concurrently from any thread; writes are
// serialized on a per-client mutex.
//
// @param client The client handle.
// @param event  Non-NULL input event.
//
// @return
//   - `OVSTREAM_API_SUCCESS` on success.
//   - `OVSTREAM_API_INVALID_ARGUMENT` if `client` or `event` is
//     NULL.
//   - `OVSTREAM_API_ERROR` if the event type is unrecognized or the
//     control-channel write fails.
//
// @ingroup shm_client
//--------------------------------------------------------------
OVSTREAM_SHM_CLIENT_API ovstream_result_t ovstream_shm_client_send_input_event(
    ovstream_shm_client_t* client,
    const ovstream_input_event_t* event);

//--------------------------------------------------------------
// @brief Send a client-to-server text message.
//
// The producer receives it via the callback registered with
// `ovstream_set_message_callback`. The control-channel transport
// caps each line at 16 MiB and reserves `'\n'` / `'\r'` as line
// terminators, so payloads containing either are rejected; V1 does
// not support escaping (base64-encode the payload first if it may
// contain those bytes). The 16 MiB ceiling is generous compared to
// the WebRTC / native transports' 65535-byte cap but is not
// unlimited.
//
// @param client  The client handle.
// @param message Input-string view. A NULL `ptr` with zero
//                `length` is allowed and sends an empty message.
//                Note: the server-to-client counterpart
//                `ovstream_send_message` rejects empty payloads --
//                the two directions are intentionally asymmetric.
//
// @return
//   - `OVSTREAM_API_SUCCESS` on success.
//   - `OVSTREAM_API_INVALID_ARGUMENT` if `client` is NULL or
//     `message.ptr` is NULL with non-zero `length`.
//   - `OVSTREAM_API_INVALID_STATE` if the client is not attached.
//   - `OVSTREAM_API_ERROR` if `message` contains `'\n'` / `'\r'`
//     (reserved wire-format terminators) or the control-channel
//     write fails. Use `ovstream_get_last_error` to distinguish.
//
// @ingroup shm_client
//--------------------------------------------------------------
OVSTREAM_SHM_CLIENT_API ovstream_result_t ovstream_shm_client_send_message(
    ovstream_shm_client_t* client,
    ovstream_string_t message);

//--------------------------------------------------------------
// @brief Send a client-to-server Unicode / IME composed-text event.
//
// The producer receives it via the callback registered with
// `ovstream_set_unicode_callback`. Same payload constraints as
// `ovstream_shm_client_send_message`.
//
// @param client The client handle.
// @param text   UTF-8 payload; same input-string contract as
//               `_send_message`.
//
// @return
//   - `OVSTREAM_API_SUCCESS` on success.
//   - `OVSTREAM_API_INVALID_ARGUMENT` if `client` is NULL or
//     `text.ptr` is NULL with non-zero `length`.
//   - `OVSTREAM_API_INVALID_STATE` if the client is not attached.
//   - `OVSTREAM_API_ERROR` if `text` contains `'\n'` / `'\r'`
//     (reserved wire-format terminators) or the control-channel
//     write fails. Use `ovstream_get_last_error` to distinguish.
//
// @ingroup shm_client
//--------------------------------------------------------------
OVSTREAM_SHM_CLIENT_API ovstream_result_t ovstream_shm_client_send_unicode(
    ovstream_shm_client_t* client,
    ovstream_string_t text);

//--------------------------------------------------------------
// @brief Register (or clear) the server-to-client message callback.
//
// Fires on an internal reader thread when the producer calls
// `ovstream_send_message`. The `message` view's `ptr` is
// null-terminated and valid only for the duration of the
// callback; copy out the bytes if they need to outlive it. Pass
// NULL `callback` to unregister.
//
// @param client    The client handle.
// @param callback  Callback to invoke on each received message,
//                  or NULL to clear.
// @param user_data Opaque pointer passed back to every invocation.
//
// @return
//   - `OVSTREAM_API_SUCCESS` on success.
//   - `OVSTREAM_API_INVALID_ARGUMENT` if `client` is NULL.
//
// @ingroup shm_client
//--------------------------------------------------------------
OVSTREAM_SHM_CLIENT_API ovstream_result_t ovstream_shm_client_set_message_callback(
    ovstream_shm_client_t* client,
    void (*callback)(ovstream_string_t message, void* user_data),
    void* user_data);

#ifdef __cplusplus
} // extern "C"
#endif
