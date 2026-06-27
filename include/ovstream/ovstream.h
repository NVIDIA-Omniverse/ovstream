// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: LicenseRef-NvidiaProprietary
//
// NVIDIA CORPORATION, its affiliates and licensors retain all intellectual
// property and proprietary rights in and to this material, related
// documentation and any modifications thereto. Any use, reproduction,
// disclosure or distribution of this material and related documentation
// without an express license agreement from NVIDIA CORPORATION or
// its affiliates is strictly prohibited.
#pragma once

#include "ovstream_types.h"

#ifdef _WIN32
#   ifdef OVSTREAM_BUILD
#       define OVSTREAM_API __declspec(dllexport)
#   else
#       define OVSTREAM_API __declspec(dllimport)
#   endif
#else
#   define OVSTREAM_API __attribute__((visibility("default")))
#endif

// Most functions below return `ovstream_result_t`; the `.status` field
// is `OVSTREAM_API_SUCCESS` on success or one of the other
// `OVSTREAM_API_*` codes on failure. `ovstream_get_last_error()` (thread-
// local) returns a human-readable detail string describing the failure.
// The macro `OVSTREAM_OK(r)` is a convenience for `(r).status == SUCCESS`.
// Exceptions: `ovstream_get_version` returns `void`, and
// `ovstream_get_last_error` returns an `ovstream_string_t` view.

#ifdef __cplusplus
extern "C"
{
#endif

//--------------------------------------------------------------
// @defgroup lifecycle Lifecycle
// Library and server lifecycle: initialize / shutdown / version /
// get_last_error, plus the server create / destroy / start / stop
// sequence and the default-config helper. Both library-level and
// server-level setup/teardown live here because they're called in
// the same setup-then-teardown shape.
//
// @defgroup streaming Streaming
// Per-frame and per-message hot-path entry points: video / audio
// frame submission, client-connection probe, message send.
//
// @defgroup callbacks Callbacks
// Register asynchronous notifications fired from server-internal
// threads back to the caller: connect / disconnect, client message,
// input event, unicode text.
//--------------------------------------------------------------

//--------------------------------------------------------------
// @brief Retrieve the runtime semantic version of the SDK.
//
// Useful for sanity-checking that the loaded library matches the
// `OVSTREAM_VERSION_*` macros in `ovstream_types.h` the consumer
// was built against. Each out-pointer may be NULL if the
// corresponding component is not needed.
//
// @param[out] o_major Major version number, or NULL to skip.
// @param[out] o_minor Minor version number, or NULL to skip.
// @param[out] o_patch Patch version number, or NULL to skip.
//
// @ingroup lifecycle
//--------------------------------------------------------------
OVSTREAM_API void ovstream_get_version(uint32_t* o_major,
                                       uint32_t* o_minor,
                                       uint32_t* o_patch);

//--------------------------------------------------------------
// @brief Initialize the SDK.
//
// Ref-counted: multiple calls are allowed, and the library is only
// truly shut down when `ovstream_shutdown` has been called the
// matching number of times. Must be called before
// `ovstream_create_server`.
//
// `config->log_callback` and `config->log_min_severity` are only
// consumed on the very first (ref-count 0 -> 1) initialize call.
// Subsequent calls bump the ref-count but do not replace the log
// callback; to change logging configuration, take the ref-count to
// zero via matching `ovstream_shutdown` calls and re-initialize.
//
// @par Thread-Safety
// `ovstream_initialize` and `ovstream_shutdown` must be serialized
// by the caller. The typical pattern is to call this once from the
// main thread during startup, before spinning up any worker
// threads that go on to call `ovstream_create_server`.
//
// @param config Optional init config (may be NULL). When non-NULL,
//               `log_callback` is registered as the global log
//               callback for the SDK and its dependencies
//               (GStreamer, StreamSDK).
//
// @return
//   - `OVSTREAM_API_SUCCESS` on success. Backend registration is
//     lazy: any backend-load failure surfaces from the first matching
//     `ovstream_create_server` call, not from this function.
//
// @ingroup lifecycle
//--------------------------------------------------------------
OVSTREAM_API ovstream_result_t ovstream_initialize(const ovstream_init_config_t* config);

//--------------------------------------------------------------
// @brief Decrement the SDK's init ref-count.
//
// When the ref-count reaches zero, the log callback is cleared
// and protocol backends are torn down. See `ovstream_initialize`
// for the thread-safety contract.
//
// @return
//   - `OVSTREAM_API_SUCCESS` on success.
//   - `OVSTREAM_API_INVALID_STATE` if called more times than
//     `ovstream_initialize` was called.
//
// @ingroup lifecycle
//--------------------------------------------------------------
OVSTREAM_API ovstream_result_t ovstream_shutdown(void);

//--------------------------------------------------------------
// @brief Create a streaming server instance.
//
// The server is created in the "not started" state; call
// `ovstream_start` to begin accepting clients. The returned
// handle is caller-owned and must be freed with
// `ovstream_destroy_server`.
//
// @par Example
// @code
//   ovstream_server_t* server = NULL;
//   ovstream_result_t r = ovstream_create_server(
//       OVSTREAM_SERVER_WEBRTC, &server);
//   if (!OVSTREAM_OK(r)) { /* handle error */ }
//   // ... ovstream_start, stream frames ...
//   ovstream_destroy_server(server);
// @endcode
//
// @param server_type Protocol to use (RTSP, WebRTC, native, SHM,
//                    or CUDASHM -- see `ovstream_server_type_t`).
// @param out_server  [out] Receives the new server handle. Never
//                    written to on failure.
//
// @return
//   - `OVSTREAM_API_SUCCESS` on success.
//   - `OVSTREAM_API_INVALID_ARGUMENT` if `out_server` is NULL or
//     `server_type` is unknown.
//   - `OVSTREAM_API_ERROR` if the backend's lazy registration failed
//     (e.g. GStreamer or StreamSDK could not be loaded on this host).
//     Call `ovstream_get_last_error()` for the diagnostic.
//
// @ingroup lifecycle
//--------------------------------------------------------------
OVSTREAM_API ovstream_result_t ovstream_create_server(ovstream_server_type_t server_type,
                                                      ovstream_server_t** out_server);

//--------------------------------------------------------------
// @brief Destroy a server handle.
//
// Calls `ovstream_stop` implicitly if the server is still running.
// Passing NULL is a safe no-op (returns SUCCESS). The handle is
// invalid after this call returns; using it is undefined behavior
// (the memory may be reused). Set the caller's pointer to NULL
// after destroy.
//
// As a best-effort safety net within that UB region, the
// implementation may detect a double-destroy and return
// `OVSTREAM_API_INVALID_STATE` if the freed memory has not yet
// been reused; callers MUST NOT depend on this and MUST still
// follow the "NULL your pointer" contract.
//
// @param server The server handle to destroy, or NULL.
//
// @return
//   - `OVSTREAM_API_SUCCESS` on success (including the NULL no-op).
//
// @ingroup lifecycle
//--------------------------------------------------------------
OVSTREAM_API ovstream_result_t ovstream_destroy_server(ovstream_server_t* server);

//--------------------------------------------------------------
// @brief Start the server.
//
// After this returns SUCCESS, the server is listening for clients
// and `ovstream_stream_video` / `ovstream_stream_audio` may be
// called. Zero-valued port fields in `config` are replaced with
// protocol-specific defaults; see `ovstream_config_defaults` for
// a populated baseline.
//
// @par Example
// @code
//   ovstream_server_config_t cfg;
//   ovstream_config_defaults(&cfg);
//   cfg.width  = 1920;
//   cfg.height = 1080;
//   ovstream_start(server, &cfg);
// @endcode
//
// @param server The server handle returned by `ovstream_create_server`.
// @param config Must be non-NULL.
//
// @return
//   - `OVSTREAM_API_SUCCESS` on success.
//   - `OVSTREAM_API_INVALID_ARGUMENT` if `server` or `config` is
//     NULL, or the config selects a `video_input` the chosen
//     backend doesn't support.
//   - `OVSTREAM_API_INVALID_STATE` if the server is already
//     running.
//   - `OVSTREAM_API_NOT_SUPPORTED` for backend/codec combinations
//     that are valid in principle but not implemented (e.g.
//     RTSP + AV1, WebRTC + CUSTOM, SHM + anything other than
//     CUDA or TENSOR).
//   - `OVSTREAM_API_ERROR` for backend-specific startup failures
//     (port already in use, GStreamer pipeline construction
//     failure, etc.).
//
// @ingroup lifecycle
//--------------------------------------------------------------
OVSTREAM_API ovstream_result_t ovstream_start(ovstream_server_t* server,
                                              const ovstream_server_config_t* config);

//--------------------------------------------------------------
// @brief Stop the server.
//
// The server can be re-`ovstream_start`ed with the same handle.
//
// @param server The server handle to stop.
//
// @return
//   - `OVSTREAM_API_SUCCESS` on success.
//   - `OVSTREAM_API_INVALID_ARGUMENT` if `server` is NULL or
//     destroyed.
//   - `OVSTREAM_API_INVALID_STATE` if the server is already
//     stopped.
//
// @ingroup lifecycle
//--------------------------------------------------------------
OVSTREAM_API ovstream_result_t ovstream_stop(ovstream_server_t* server);

//--------------------------------------------------------------
// @brief Submit a video frame to be streamed.
//
// Validates the frame against the server's configured `video_input`,
// resolves any TENSOR-input `DLTensor*` into a raw CUDA descriptor,
// and dispatches to the protocol backend (RTSP / WebRTC / SHM /
// CUDASHM). Every backend stages or copies the frame data into
// server-owned memory before returning, so the caller-supplied
// `frame.buffer` can be reused or freed as soon as this call
// returns. See `ovstream_video_frame_t` for the full lifetime
// contract.
//
// If `frame.sync.wait_event` is set, SHM chains its device-to-host
// memcpy via `cudaStreamWaitEvent` (no host block). If only
// `frame.sync.stream` is set (no event), SHM falls back to
// `cudaStreamSynchronize` on the caller's stream, which does host-
// block; pass an event to keep the call asynchronous. RTSP /
// WebRTC always host-block via `cudaEventSynchronize` (event) or
// `cudaStreamSynchronize` (stream) before submitting downstream.
//
// @par Example
// @code
//   ovstream_video_frame_t frame = {};
//   frame.buffer      = cuda_ptr;
//   frame.width       = 1920;
//   frame.height      = 1080;
//   frame.pitch_bytes = 1920 * 4;
//   ovstream_stream_video(server, &frame);
// @endcode
//
// @par Thread-Safety
// Safe to call concurrently with `ovstream_send_message`,
// `ovstream_stream_audio`, and the callback setters on the same
// server. Not safe to call concurrently with `ovstream_stop` or
// `ovstream_destroy_server` on the same handle.
//
// @param server The started server handle.
// @param frame  Non-NULL frame descriptor; its shape must match
//               the server's configured `video_input` (see
//               `ovstream_video_input_t`).
//
// @return
//   - `OVSTREAM_API_SUCCESS` on success.
//   - `OVSTREAM_API_INVALID_ARGUMENT` if `server`/`frame`/
//     `frame.buffer` is NULL, or the frame's shape doesn't match
//     the configured `video_input` (wrong pitch_bytes vs
//     size_bytes, malformed DLTensor, etc.).
//   - `OVSTREAM_API_INVALID_STATE` if the server has not been
//     started.
//   - `OVSTREAM_API_ERROR` for backend-specific failures (CUDA
//     errors on the SHM memcpy, GStreamer push failures, etc.).
//
// @ingroup streaming
//--------------------------------------------------------------
OVSTREAM_API ovstream_result_t ovstream_stream_video(ovstream_server_t* server,
                                                     const ovstream_video_frame_t* frame);

//--------------------------------------------------------------
// @brief Submit an audio frame to be streamed.
//
// WebRTC and native only; RTSP and SHM return NOT_SUPPORTED. Only
// 16-bit PCM is currently supported; frames with
// `bits_per_sample != 16` are rejected. Same buffer lifetime
// contract as `ovstream_stream_video`: samples are staged into
// server-owned memory before the call returns, so the caller may
// reuse or free `frame.buffer` immediately after return.
//
// @par Thread-Safety
// Same contract as `ovstream_stream_video`.
//
// @param server The started server handle.
// @param frame  Non-NULL frame descriptor.
//
// @return
//   - `OVSTREAM_API_SUCCESS` on success.
//   - `OVSTREAM_API_INVALID_ARGUMENT` if `server` / `frame` /
//     `frame.buffer` is NULL or `bits_per_sample != 16`.
//   - `OVSTREAM_API_INVALID_STATE` if the server has not been
//     started or no client is connected yet.
//   - `OVSTREAM_API_TIMEOUT` if the WebRTC audio stream is not yet
//     connected and doesn't connect within the internal wait window
//     (~200 ms); the frame is dropped.
//   - `OVSTREAM_API_NOT_SUPPORTED` on RTSP and SHM servers.
//   - `OVSTREAM_API_ERROR` if the StreamSDK submit fails.
//
// @ingroup streaming
//--------------------------------------------------------------
OVSTREAM_API ovstream_result_t ovstream_stream_audio(ovstream_server_t* server,
                                                     const ovstream_audio_frame_t* frame);

//--------------------------------------------------------------
// @brief Check whether at least one client is currently connected.
//
// For RTSP servers (which support multiple concurrent clients),
// `*out_connected` is set to true if any client is connected.
//
// @param server        The server handle.
// @param out_connected [out] Receives the connection state. Never
//                      written to on failure.
//
// @return
//   - `OVSTREAM_API_SUCCESS` on success.
//   - `OVSTREAM_API_INVALID_ARGUMENT` if `server` or
//     `out_connected` is NULL.
//
// @ingroup streaming
//--------------------------------------------------------------
OVSTREAM_API ovstream_result_t ovstream_is_client_connected(ovstream_server_t* server,
                                                            bool* out_connected);

//--------------------------------------------------------------
// @brief Register (or clear) the connect/disconnect callback.
//
// Registration only delivers FUTURE transitions -- it does NOT
// synchronously replay the current connection state. To avoid
// missing the initial connect, register the callback before
// calling `ovstream_start`, or query
// `ovstream_is_client_connected` immediately after registering.
// Transitions are delivered from network threads.
//
// Per-backend delivery semantics:
//   - **WebRTC / native / RTSP** fire the callback for every
//     individual client connect/disconnect. RTSP supports multiple
//     concurrent clients, so callers see one fire per client
//     transition; the `connected` arg reflects this client's
//     direction, not the aggregate. Use
//     `ovstream_is_client_connected` to query whether any client is
//     still attached.
//   - **SHM** aggregates to the 0<->1 transition only: the callback
//     fires when the first client attaches and again when the last
//     client detaches; intermediate joins/leaves are not reported.
//
// @par Example
// @code
//   void on_conn(ovstream_server_t* s, bool connected, void* ud) {
//       printf("Client %s\n", connected ? "connected" : "disconnected");
//   }
//   ovstream_set_connection_callback(server, on_conn, NULL);
// @endcode
//
// @par Thread-Safety
// May be called at any point in the server's lifetime. The
// callback itself fires on network threads, so its body is
// responsible for any thread marshaling it needs.
//
// @param server    The server handle.
// @param callback  Callback to invoke on connect/disconnect, or
//                  NULL to clear.
// @param user_data Opaque pointer passed back to every invocation.
//
// @return
//   - `OVSTREAM_API_SUCCESS` on success.
//   - `OVSTREAM_API_INVALID_ARGUMENT` if `server` is NULL.
//
// @ingroup callbacks
//--------------------------------------------------------------
OVSTREAM_API ovstream_result_t ovstream_set_connection_callback(ovstream_server_t* server,
                                                                ovstream_connection_callback_t callback,
                                                                void* user_data);

//--------------------------------------------------------------
// @brief Send a text message to connected clients.
//
// Supported on WebRTC, native, and SHM (which carries the message
// over its local control channel); always returns NOT_SUPPORTED on
// RTSP. WebRTC and native messages are limited to UINT16_MAX
// (65535) bytes due to the underlying StreamSDK data-channel
// limit; longer messages are rejected with INVALID_ARGUMENT. SHM
// caps each message at 16 MiB (the control-channel line limit) and
// rejects payloads containing '\n' or '\r', which are reserved as
// line terminators on the wire; both surface as INVALID_ARGUMENT.
//
// @par Example
// @code
//   const char* text = "hello";
//   ovstream_string_t msg = { text, 5 };
//   ovstream_send_message(server, msg);
// @endcode
//
// @param server  The started server handle.
// @param message UTF-8 message payload. Input-string contract:
//                `message.ptr` is read for exactly `message.length`
//                bytes and is NOT required to be null-terminated.
//                An empty view (`length == 0`, with `ptr` either
//                NULL or non-NULL) is rejected by this server-to-
//                client direction. Note: the client-to-server
//                counterpart `ovstream_client_send_message`
//                does accept empty payloads -- the two directions
//                are intentionally asymmetric (an empty server
//                broadcast has no useful semantics; an empty
//                client ping has).
//
// @return
//   - `OVSTREAM_API_SUCCESS` on success.
//   - `OVSTREAM_API_INVALID_ARGUMENT` if `server` is NULL,
//     `message` is empty, or (WebRTC / native) `message` exceeds
//     65535 bytes.
//   - `OVSTREAM_API_INVALID_STATE` if the server has not been
//     started.
//   - `OVSTREAM_API_NOT_SUPPORTED` on RTSP servers.
//
// @ingroup streaming
//--------------------------------------------------------------
OVSTREAM_API ovstream_result_t ovstream_send_message(ovstream_server_t* server,
                                                     ovstream_string_t message);

//--------------------------------------------------------------
// @brief Register (or clear) the client-to-server message callback.
//
// Fires on WebRTC, native, and SHM (which delivers messages over
// its local control channel); RTSP has no data channel so the
// callback never fires there. See `ovstream_set_connection_callback`
// for thread-safety and registration semantics.
//
// @param server    The server handle.
// @param callback  Callback to invoke on each received message,
//                  or NULL to clear.
// @param user_data Opaque pointer passed back to every invocation.
//
// @return
//   - `OVSTREAM_API_SUCCESS` on success.
//   - `OVSTREAM_API_INVALID_ARGUMENT` if `server` is NULL.
//
// @ingroup callbacks
//--------------------------------------------------------------
OVSTREAM_API ovstream_result_t ovstream_set_message_callback(ovstream_server_t* server,
                                                             ovstream_message_callback_t callback,
                                                             void* user_data);

//--------------------------------------------------------------
// @brief Register (or clear) the client input callback.
//
// Fires on WebRTC, native, and SHM (which delivers input events
// over its local control channel); RTSP has no input channel so
// the callback never fires there. Each event is a tagged union
// over keyboard / mouse / gamepad / touch variants (see
// `ovstream_input_event_t`). Input events are delivered on an
// internal thread; the callback body is responsible for any
// thread marshaling it needs. See
// `ovstream_set_connection_callback` for registration semantics.
//
// @param server    The server handle.
// @param callback  Callback to invoke on each received input
//                  event, or NULL to clear.
// @param user_data Opaque pointer passed back to every invocation.
//
// @return
//   - `OVSTREAM_API_SUCCESS` on success.
//   - `OVSTREAM_API_INVALID_ARGUMENT` if `server` is NULL.
//
// @ingroup callbacks
//--------------------------------------------------------------
OVSTREAM_API ovstream_result_t ovstream_set_input_callback(ovstream_server_t* server,
                                                           ovstream_input_callback_t callback,
                                                           void* user_data);

//--------------------------------------------------------------
// @brief Register (or clear) the client Unicode / IME text callback.
//
// Fires when the client delivers a composed text event (IME,
// on-screen keyboard, emoji picker, paste) that can't be
// represented by a single keycode. Available on WebRTC, native,
// and SHM (which delivers Unicode events over its local control
// channel); RTSP has no input channel so the callback never fires
// there. The payload is raw UTF-8 bytes + length. See
// `ovstream_set_connection_callback` for thread-safety and
// registration semantics.
//
// @param server    The server handle.
// @param callback  Callback to invoke for each received Unicode
//                  event, or NULL to clear.
// @param user_data Opaque pointer passed back to every invocation.
//
// @return
//   - `OVSTREAM_API_SUCCESS` on success.
//   - `OVSTREAM_API_INVALID_ARGUMENT` if `server` is NULL.
//
// @ingroup callbacks
//--------------------------------------------------------------
OVSTREAM_API ovstream_result_t ovstream_set_unicode_callback(ovstream_server_t* server,
                                                             ovstream_unicode_callback_t callback,
                                                             void* user_data);

//--------------------------------------------------------------
// @brief Set (or clear) the server's ICE servers for WebRTC NAT traversal.
//
// May be called BEFORE `ovstream_start` (cached and applied when
// the underlying StreamSDK server is created) or AFTER
// `ovstream_start` (applied immediately via StreamSDK's runtime-
// parameter API). The post-start path is the canonical mechanism
// for refreshing time-limited TURN credentials without stopping
// the stream, which would otherwise drop all connected clients.
//
// Replace-all semantics: each call fully replaces the previous
// ICE server set. Pass `config == NULL` (or `config->server_count
// == 0`) to clear.
//
// URL scheme dispatch: each server entry's URLs are classified by
// scheme. `stun:` / `stuns:` go to StreamSDK's NAT-server table
// (max 4 entries); `turn:` / `turns:` go to the TURN table (max
// 8 entries). Per-entry URL count is capped at 3. Any other
// scheme is rejected with `INVALID_ARGUMENT`. URLs and credential
// strings each have a per-byte length cap inherited from
// StreamSDK (128 bytes for STUN, 170 bytes for TURN).
//
// The underlying ICE transport policy is fixed to "all" (try
// direct paths first, fall back to TURN) -- matching the WebRTC
// default. Force-relay is intentionally not exposed in this
// release.
//
// @par Example
// @code
//   ovstream_webrtc_ice_server_t servers[2] = {};
//   servers[0].urls = OVSTREAM_STRING_LITERAL("stun:stun.l.google.com:19302");
//   servers[1].urls       = OVSTREAM_STRING_LITERAL("turn:turn.example.net:3478");
//   servers[1].username   = OVSTREAM_STRING_LITERAL("user");
//   servers[1].credential = OVSTREAM_STRING_LITERAL("pass");
//   ovstream_webrtc_ice_config_t cfg = { servers, 2 };
//   ovstream_webrtc_set_ice_servers(server, &cfg);
// @endcode
//
// @par Thread-Safety
// Safe to call concurrently with `ovstream_stream_video`,
// `ovstream_stream_audio`, and `ovstream_send_message` on the
// same server. The new ICE set takes effect on the next ICE
// gathering pass; in-flight connections continue with their
// existing candidates.
//
// @param server The server handle.
// @param config Non-NULL to set, or NULL to clear all ICE
//               servers. When non-NULL with `server_count > 0`,
//               `servers` must be non-NULL.
//
// @return
//   - `OVSTREAM_API_SUCCESS` on success.
//   - `OVSTREAM_API_INVALID_ARGUMENT` if `server` is NULL or
//     destroyed, or `config` is malformed (e.g.
//     `server_count > 0` with `servers == NULL`, oversized
//     URL/credential string, unknown URL scheme, empty `urls`
//     view, more than 4 STUN / 8 TURN entries, more than 3 URLs
//     per entry).
//   - `OVSTREAM_API_NOT_SUPPORTED` on RTSP, SHM, and CUDASHM
//     servers (those backends have no concept of ICE).
//
// @ingroup lifecycle
//--------------------------------------------------------------
OVSTREAM_API ovstream_result_t
ovstream_webrtc_set_ice_servers(ovstream_server_t* server,
                                const ovstream_webrtc_ice_config_t* config);

//--------------------------------------------------------------
// @brief Fill `config` with SDK-wide defaults.
//
// 1920x1080 @ 60 FPS, `video_input` = CUDA, `cuda_device` = -1
// (backend default GPU), all port fields left at 0 so
// `ovstream_start` resolves them to protocol-specific values.
// Callers typically override a handful of fields after this call.
//
// @param config Must be non-NULL. Existing contents are overwritten.
//
// @return
//   - `OVSTREAM_API_SUCCESS` on success.
//   - `OVSTREAM_API_INVALID_ARGUMENT` if `config` is NULL.
//
// @ingroup lifecycle
//--------------------------------------------------------------
OVSTREAM_API ovstream_result_t ovstream_config_defaults(ovstream_server_config_t* config);

//--------------------------------------------------------------
// @brief Return the most recent error message on the calling thread.
//
// The returned `ptr` references thread-local storage owned by the
// SDK. It remains valid ONLY until:
//   - the next OVSTREAM call on this thread (the error slot may
//     be rewritten or cleared), OR
//   - the calling thread terminates.
//
// Copy the string (e.g. into a `std::string`) before doing either
// if you need to retain it. Do NOT `free()` the returned pointer.
//
// @par Thread-Safety
// Each thread has its own error slot; safe to call concurrently
// from any thread. Errors written from server-internal threads
// are NOT visible here -- only errors set by the calling thread's
// own OVSTREAM calls are returned.
//
// @return Output-string view. `ptr` is non-NULL and
//         null-terminated; `length` is 0 (and `ptr[0] == '\0'`)
//         after a successful call or when no error has been
//         recorded on this thread.
//
// @ingroup lifecycle
//--------------------------------------------------------------
OVSTREAM_API ovstream_string_t ovstream_get_last_error(void);

#ifdef __cplusplus
}
#endif
