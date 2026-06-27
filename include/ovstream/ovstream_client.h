// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: LicenseRef-NvidiaProprietary
//
// NVIDIA CORPORATION, its affiliates and licensors retain all intellectual
// property and proprietary rights in and to this material, related
// documentation and any modifications thereto. Any use, reproduction,
// disclosure or distribution of this material and related documentation
// without an express license agreement from NVIDIA CORPORATION or
// its affiliates is strictly prohibited.

// Unified consumer-side client for the OVSTREAM transports. A consumer
// process attaches to a running ovstream server with
// `ovstream_create_client(type, config, &client)` and pulls frames with
// `ovstream_client_wait_frame`, regardless of which transport carries the
// pixels:
//
//   OVSTREAM_CLIENT_SHM     - same-machine, raw BGRA8 in a host shared-memory
//                             ring. `frame.data` is a zero-copy host pointer.
//   OVSTREAM_CLIENT_CUDASHM - same-host, raw BGRA8 kept GPU-resident over CUDA
//                             IPC. `frame.device_ptr` is a CUDA device pointer.
//   OVSTREAM_CLIENT_NATIVE  - network (StreamSDK / NVSS), encoded video decoded
//                             client-side to BGRA8. `frame.data` is a host
//                             pointer. Lifts the same-host / same-GPU
//                             constraint, at the cost of encode+decode latency
//                             and an NVIDIA GPU on the consumer (the client
//                             decodes via StreamSDK's NvStreamingMedia; see
//                             below).
//
// This is the single client surface; there is no per-transport client API.
// The lifecycle and control surface (create / wait_frame / is_alive /
// send_input_event / send_message / send_unicode / message callback / destroy)
// is identical across transports. The only transport-specific thing is the
// frame payload, carried by the tagged `ovstream_frame_t` returned from
// `wait_frame`.
//
// Dependency footprint: the `ovstream_client` library always links the CUDA
// runtime and StreamSDK (the latter for the NATIVE backend). At runtime SHM
// uses neither, CUDASHM uses CUDA only, and NATIVE uses both -- decoding via
// NvStreamingMedia (NVDEC on NVIDIA GPUs) and delivering BGRA8 directly, so the
// NATIVE transport requires an NVIDIA GPU + CUDA on the consumer.
//
#pragma once

#include <ovstream/ovstream_types.h>

// Visibility / linkage for the public C symbols below. Three build
// configurations are supported, selected by which preprocessor macros are
// defined (same posture as the server-side `ovstream_*` API in `ovstream.h`):
//
//   1. Main `ovstream` shared library build (`OVSTREAM_BUILD`): symbols are
//      dllexported / default-visibility.
//   2. Consumer compiling the `ovstream_client` sources into a static library
//      (`OVSTREAM_CLIENT_STATIC`): no linkage decoration.
//   3. Consumer linking the main `ovstream` shared library: symbols are
//      dllimported on Windows / default-visibility on Linux.
#if defined(OVSTREAM_CLIENT_STATIC)
#   define OVSTREAM_CLIENT_API
#elif defined(_WIN32)
#   ifdef OVSTREAM_BUILD
#       define OVSTREAM_CLIENT_API __declspec(dllexport)
#   else
#       define OVSTREAM_CLIENT_API __declspec(dllimport)
#   endif
#else
#   define OVSTREAM_CLIENT_API __attribute__((visibility("default")))
#endif

#ifdef __cplusplus
extern "C"
{
#endif

//--------------------------------------------------------------
// @defgroup client Unified client
// Consumer-side library for attaching to an ovstream producer and pulling
// frames + control messages, across the SHM, CUDASHM, and native (StreamSDK)
// transports.
//--------------------------------------------------------------

//--------------------------------------------------------------
// @brief Return the most recent error message on the calling thread.
//
// Mirrors the declaration in `ovstream.h` so consumers linking
// `ovstream_client` as a static library (which intentionally don't include
// `ovstream.h`) can still retrieve diagnostics referenced in the `@return`
// blocks below.
//
// Output-string contract: `ptr` is non-NULL and null-terminated, `length` is
// 0 (and `ptr[0] == '\0'`) when no error is set on this thread. Valid until
// the next ovstream call on this thread or until the thread terminates. Do NOT
// `free()` the pointer.
//
// @ingroup client
//--------------------------------------------------------------
OVSTREAM_CLIENT_API ovstream_string_t ovstream_get_last_error(void);

//--------------------------------------------------------------
// @brief Which transport a client attaches to.
//
// Passed to `ovstream_create_client`; selects which sub-struct of
// `ovstream_client_config_t` is consulted, and which member of the returned
// `ovstream_frame_t` carries the pixels.
//--------------------------------------------------------------
typedef enum
{
    OVSTREAM_CLIENT_SHM     = 0,
    OVSTREAM_CLIENT_CUDASHM = 1,
    OVSTREAM_CLIENT_NATIVE  = 2,
} ovstream_client_type_t;

//--------------------------------------------------------------
// @brief Opaque client handle.
//
// Created by `ovstream_create_client`, destroyed by `ovstream_destroy_client`.
//--------------------------------------------------------------
typedef struct ovstream_client_t ovstream_client_t;

//--------------------------------------------------------------
// @brief Client configuration passed to `ovstream_create_client`.
//
// Only the sub-struct matching the `type` argument is consulted; the rest are
// ignored:
//   OVSTREAM_CLIENT_SHM     -> `shm`
//   OVSTREAM_CLIENT_CUDASHM -> `cudashm`
//   OVSTREAM_CLIENT_NATIVE  -> `native`
//
// The matching sub-struct is required: SHM/CUDASHM need a non-empty
// `stream_name` and NATIVE a non-empty `server_ip`; a NULL config or an empty
// required field is rejected with `OVSTREAM_API_INVALID_ARGUMENT`.
//--------------------------------------------------------------
typedef struct
{
    // SHM (ignored unless type == OVSTREAM_CLIENT_SHM).
    struct
    {
        // Must match the value the server was started with (or the
        // `"ovstream-<pid>"` auto-default the producer logged at startup if it
        // left the name unset). Input-string contract; an empty view is
        // rejected.
        ovstream_string_t stream_name;
    } shm;

    // CUDASHM (ignored unless type == OVSTREAM_CLIENT_CUDASHM).
    struct
    {
        // As above (non-empty). The calling thread's current CUDA device must
        // match the producer's device or be peer-capable; see
        // `ovstream_client_get_producer_device`.
        ovstream_string_t stream_name;
    } cudashm;

    // NATIVE (ignored unless type == OVSTREAM_CLIENT_NATIVE).
    //
    // The native client connects to an ovstream NATIVE (StreamSDK / NVSS)
    // server, receives encoded video, and decodes it client-side via
    // StreamSDK's NvStreamingMedia (which uses NVDEC on NVIDIA GPUs) into host
    // BGRA8 frames. Requires an NVIDIA GPU + CUDA on the consumer.
    struct
    {
        // Server address (IPv4 dotted-quad or hostname). Input-string contract;
        // empty view is rejected.
        ovstream_string_t server_ip;
        // Media transport (streaming) port. 0 uses the native default (47999),
        // matching the server's `ovstream_server_config_t::stream_port` default.
        uint16_t          stream_port;
        // Signaling (TCP) port. 0 uses the default (49100), matching the
        // server's `webrtc.signal_port` default.
        uint16_t          signal_port;
        // CUDA device ordinal the client decodes / converts on. Negative uses
        // the default device (0). On multi-GPU consumers, set the device whose
        // memory the caller will read the decoded frame from.
        int32_t           cuda_device;
    } native;
} ovstream_client_config_t;

//--------------------------------------------------------------
// @brief Frame view returned by `ovstream_client_wait_frame`.
//
// Which payload member is populated depends on `source`:
//   SHM     - `data` is a read-only host pointer into the shared region;
//             `device_ptr` is 0, `slot_index` is 0.
//   NATIVE  - `data` is a read-only host pointer into a client-owned decoded
//             frame buffer; `device_ptr` is 0, `slot_index` is 0.
//   CUDASHM - `device_ptr` is a CUDA device pointer (cast to `void*` for the
//             CUDA Runtime API, `CUdeviceptr` for the Driver API) into the
//             producer's GPU ring; `data` is NULL. `slot_index` identifies the
//             ring slot.
//
// In all cases `format` is one of the `OVSTREAM_PIXEL_FORMAT_*` constants from
// `ovstream_types.h` (currently always `OVSTREAM_PIXEL_FORMAT_BGRA8`), and
// `pitch_bytes` is the row pitch (may exceed `width * 4`).
//
// @par Ownership / Lifetime
// The payload is a view into a producer- or decoder-owned slot, never a copy.
// The owner reclaims that slot asynchronously -- on its own schedule, not when
// you next call `wait_frame` -- so copy out anything that must persist.
//   - SHM: the producer may overwrite this shared-memory slot at any time after
//     `wait_frame` returns; copy out bytes that must outlive the next frame.
//   - CUDASHM: the producer may rotate onto this slot's GPU memory at any time;
//     finish the read kernel within the producer's ring depth or copy first.
//   - NATIVE: `data` aliases a slot in the client's decode ring; the StreamSDK
//     decoder thread recycles it after it publishes the ring's depth of newer
//     frames, independent of `wait_frame`. Copy out bytes that must persist.
//--------------------------------------------------------------
typedef struct
{
    ovstream_client_type_t source;               // Which transport produced this frame.
    const void*            data;                 // Host pixels (SHM, NATIVE); NULL for CUDASHM.
    uintptr_t              device_ptr;           // CUDA device pointer (CUDASHM); 0 otherwise.
    uint64_t               sequence;             // Monotonic per-client frame counter.
    uint64_t               capture_timestamp_ns; // Producer-side capture timestamp.
    uint32_t               width;
    uint32_t               height;
    uint32_t               pitch_bytes;          // Row pitch in bytes (>= width * 4).
    uint32_t               format;               // OVSTREAM_PIXEL_FORMAT_* (currently BGRA8).
    uint32_t               slot_index;           // Ring slot (CUDASHM); 0 otherwise.
} ovstream_frame_t;

//--------------------------------------------------------------
// @brief Attach to a running ovstream server.
//
// @par Example
// @code
//   // Native client connecting to a remote server.
//   ovstream_client_config_t config = {};
//   config.native.server_ip = OVSTREAM_STRING_LITERAL("10.0.0.5");
//   ovstream_client_t* client = NULL;
//   if (!OVSTREAM_OK(ovstream_create_client(OVSTREAM_CLIENT_NATIVE, &config, &client)))
//       fprintf(stderr, "%s\n", ovstream_get_last_error().ptr);
//   // ... wait_frame loop ...
//   ovstream_destroy_client(client);
// @endcode
//
// @param type       Which transport to attach to.
// @param config     Per-type configuration. Required (non-NULL) for every
//                   transport: SHM/CUDASHM need a non-empty `stream_name`,
//                   NATIVE a non-empty `native.server_ip`.
// @param out_client [out] Receives the new client handle. Never written on
//                   failure.
//
// @return
//   - `OVSTREAM_API_SUCCESS` on success.
//   - `OVSTREAM_API_INVALID_ARGUMENT` if `out_client` is NULL, `type` is
//     out of range, or a required config field is missing.
//   - `OVSTREAM_API_ERROR` if attach / connect fails (producer not running,
//     name mismatch, incompatible protocol, CUDA IPC failure, connection
//     refused, decoder init failure, ...). See `ovstream_get_last_error`.
//
// @ingroup client
//--------------------------------------------------------------
OVSTREAM_CLIENT_API ovstream_result_t ovstream_create_client(
    ovstream_client_type_t type,
    const ovstream_client_config_t* config,
    ovstream_client_t** out_client);

//--------------------------------------------------------------
// @brief Detach and free a client handle.
//
// Releases all transport resources (unmaps shared memory / closes CUDA IPC
// handles / disconnects the StreamSDK session and tears down the decoder) and
// closes the control channel. Passing NULL is a safe no-op (returns SUCCESS).
// The handle is invalid after this call; set the caller's pointer to NULL.
//
// @ingroup client
//--------------------------------------------------------------
OVSTREAM_CLIENT_API ovstream_result_t ovstream_destroy_client(ovstream_client_t* client);

//--------------------------------------------------------------
// @brief Wait for the next frame newer than the one previously returned.
//
// Per-client cursor. See `ovstream_frame_t` for the per-transport payload and
// lifetime rules.
//
// @param client     The client handle.
// @param timeout_ms Wait policy: 0 = non-blocking poll; <0 = wait
//                   indefinitely; >0 = wait up to that many milliseconds.
// @param out_frame  [out] Receives the frame view on success. Untouched on
//                   failure.
//
// @return
//   - `OVSTREAM_API_SUCCESS` with `*out_frame` populated when a new frame is
//     available.
//   - `OVSTREAM_API_TIMEOUT` when the wait window elapses without a new frame.
//   - `OVSTREAM_API_INVALID_STATE` when the producer has stopped (SHM/CUDASHM)
//     or the connection has dropped (NATIVE); also surfaces via
//     `ovstream_client_is_alive`.
//   - `OVSTREAM_API_INVALID_ARGUMENT` if `client` or `out_frame` is NULL.
//   - `OVSTREAM_API_ERROR` for unexpected read / decode failures.
//
// @ingroup client
//--------------------------------------------------------------
OVSTREAM_CLIENT_API ovstream_result_t ovstream_client_wait_frame(
    ovstream_client_t* client,
    int32_t timeout_ms,
    ovstream_frame_t* out_frame);

//--------------------------------------------------------------
// @brief Release the last frame returned by `wait_frame` (no-op today).
//
// Kept for ABI symmetry with future protocol versions that may want explicit
// reader-side bookkeeping. Calling it is harmless; not calling it is correct.
//
// @return
//   - `OVSTREAM_API_SUCCESS` always (no-op).
//   - `OVSTREAM_API_INVALID_ARGUMENT` if `client` is NULL.
//
// @ingroup client
//--------------------------------------------------------------
OVSTREAM_CLIENT_API ovstream_result_t ovstream_client_release_frame(ovstream_client_t* client);

//--------------------------------------------------------------
// @brief Query whether the stream is still live.
//
// `*out_alive` is true while frames can still arrive: for SHM/CUDASHM, while
// the producer is running; for NATIVE, while the StreamSDK connection is up.
// Cheap (no I/O); safe to call from a polling loop.
//
// @return
//   - `OVSTREAM_API_SUCCESS` on success.
//   - `OVSTREAM_API_INVALID_ARGUMENT` if `client` or `out_alive` is NULL.
//
// @ingroup client
//--------------------------------------------------------------
OVSTREAM_CLIENT_API ovstream_result_t ovstream_client_is_alive(
    const ovstream_client_t* client,
    bool* out_alive);

//--------------------------------------------------------------
// @brief Get the CUDA device the frame pixels live on (CUDASHM / NATIVE).
//
// For CUDASHM this is the producer's ring device (the consumer's read kernels
// must run on it or a peer-capable device). For NATIVE this is the client's
// decode/convert device (`native.cuda_device`, or 0). For SHM it is not
// applicable.
//
// @return
//   - `OVSTREAM_API_SUCCESS` with `*out_device` populated (CUDASHM / NATIVE).
//   - `OVSTREAM_API_NOT_SUPPORTED` for SHM (host memory, no device).
//   - `OVSTREAM_API_INVALID_ARGUMENT` if `client` or `out_device` is NULL.
//
// @ingroup client
//--------------------------------------------------------------
OVSTREAM_CLIENT_API ovstream_result_t ovstream_client_get_producer_device(
    const ovstream_client_t* client,
    int32_t* out_device);

//--------------------------------------------------------------
// @brief Send an input event to the server.
//
// The server receives it via the same callback it registered with
// `ovstream_set_input_callback` for any other transport, so producer code does
// not change between transports. SHM/CUDASHM encode events on the control
// channel; NATIVE pushes them through the StreamSDK input stream.
//
// @par Thread-Safety
// Safe to call concurrently from any thread.
//
// @return
//   - `OVSTREAM_API_SUCCESS` on success.
//   - `OVSTREAM_API_INVALID_ARGUMENT` if `client` or `event` is NULL.
//   - `OVSTREAM_API_ERROR` if the event type is unrecognized or the send fails.
//
// @ingroup client
//--------------------------------------------------------------
OVSTREAM_CLIENT_API ovstream_result_t ovstream_client_send_input_event(
    ovstream_client_t* client,
    const ovstream_input_event_t* event);

//--------------------------------------------------------------
// @brief Send a client-to-server text message.
//
// The server receives it via the callback registered with
// `ovstream_set_message_callback`. SHM/CUDASHM cap each line at 16 MiB and
// reserve `'\n'` / `'\r'` as terminators (base64-encode payloads that may
// contain them); NATIVE caps payloads at the StreamSDK custom-message limit
// (65535 bytes).
//
// @param message Input-string view. A NULL `ptr` with zero `length` sends an
//                empty message.
//
// @return
//   - `OVSTREAM_API_SUCCESS` on success.
//   - `OVSTREAM_API_INVALID_ARGUMENT` if `client` is NULL or `message.ptr` is
//     NULL with non-zero `length`.
//   - `OVSTREAM_API_INVALID_STATE` if the client is not attached / connected.
//   - `OVSTREAM_API_ERROR` if the payload violates the transport's constraints
//     or the send fails. See `ovstream_get_last_error`.
//
// @ingroup client
//--------------------------------------------------------------
OVSTREAM_CLIENT_API ovstream_result_t ovstream_client_send_message(
    ovstream_client_t* client,
    ovstream_string_t message);

//--------------------------------------------------------------
// @brief Send a client-to-server Unicode / IME composed-text event.
//
// The server receives it via the callback registered with
// `ovstream_set_unicode_callback`. Same payload constraints as
// `ovstream_client_send_message`.
//
// @ingroup client
//--------------------------------------------------------------
OVSTREAM_CLIENT_API ovstream_result_t ovstream_client_send_unicode(
    ovstream_client_t* client,
    ovstream_string_t text);

//--------------------------------------------------------------
// @brief Register (or clear) the server-to-client message callback.
//
// Fires on an internal reader thread when the server calls
// `ovstream_send_message`. The `message` view's `ptr` is null-terminated and
// valid only for the duration of the callback; copy out the bytes if they need
// to outlive it. Pass NULL `callback` to unregister.
//
// @return
//   - `OVSTREAM_API_SUCCESS` on success.
//   - `OVSTREAM_API_INVALID_ARGUMENT` if `client` is NULL.
//
// @ingroup client
//--------------------------------------------------------------
OVSTREAM_CLIENT_API ovstream_result_t ovstream_client_set_message_callback(
    ovstream_client_t* client,
    void (*callback)(ovstream_string_t message, void* user_data),
    void* user_data);

#ifdef __cplusplus
} // extern "C"
#endif
