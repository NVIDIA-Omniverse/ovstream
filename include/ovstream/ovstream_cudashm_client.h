// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: LicenseRef-NvidiaProprietary
//
// NVIDIA CORPORATION, its affiliates and licensors retain all intellectual
// property and proprietary rights in and to this material, related
// documentation and any modifications thereto. Any use, reproduction,
// disclosure or distribution of this material and related documentation
// without an express license agreement from NVIDIA CORPORATION or
// its affiliates is strictly prohibited.

// Reader-side library for the OVSTREAM CUDASHM transport. Same-host
// (or same-container-host) consumers attach to a running ovstream
// CUDASHM server via `ovstream_cudashm_client_create` and pull frames
// with `ovstream_cudashm_client_wait_frame`. Pixels are delivered as
// CUDA device pointers into the producer's GPU memory -- no
// device-to-host copy.
//
// Attach steps inside `ovstream_cudashm_client_create`:
//   1. Open the host-shared metadata region by name.
//   2. Validate the region header (magic, version, layout).
//   3. cudaIpcOpenMemHandle each of the N exported IPC handles,
//      producing N consumer-side device pointers.
//   4. Open the wakeup primitive and the control endpoint.
//
// This header is independent of `ovstream.h`: a client process does
// not have to link the full server-side ovstream library, only the
// `ovstream_cudashm_client` shared library that ships alongside it.
// The only types it shares with the server are `ovstream_string_t`
// and the pixel-format constants from `ovstream_types.h`.
//
#pragma once

#include <ovstream/ovstream_types.h>

// Visibility / linkage for the public C symbols below. Three build
// configurations are supported, selected by which preprocessor
// macros are defined (same posture as ovstream_shm_client.h):
//
//   1. Main `ovstream` shared library build (`OVSTREAM_BUILD`):
//      dllexport / default-visibility.
//   2. Slim `ovstream_cudashm_client` static library
//      (`OVSTREAM_CUDASHM_CLIENT_STATIC`): no linkage decoration.
//   3. Consumer linking the main `ovstream` shared library: the
//      symbols are dllimported on Windows / default-visibility on
//      Linux.
#if defined(OVSTREAM_CUDASHM_CLIENT_STATIC)
#   define OVSTREAM_CUDASHM_CLIENT_API
#elif defined(_WIN32)
#   ifdef OVSTREAM_BUILD
#       define OVSTREAM_CUDASHM_CLIENT_API __declspec(dllexport)
#   else
#       define OVSTREAM_CUDASHM_CLIENT_API __declspec(dllimport)
#   endif
#else
#   define OVSTREAM_CUDASHM_CLIENT_API __attribute__((visibility("default")))
#endif

#ifdef __cplusplus
extern "C"
{
#endif

//--------------------------------------------------------------
// @defgroup cudashm_client CUDASHM client
// Consumer-side library for attaching to an OVSTREAM CUDASHM
// producer and pulling GPU-resident frames over CUDA IPC.
//--------------------------------------------------------------

//--------------------------------------------------------------
// @brief Return the most recent error message on the calling thread.
//
// Mirrors the declaration in `ovstream.h`. Lifetime: valid until the
// next ovstream call on this thread.
//
// @ingroup cudashm_client
//--------------------------------------------------------------
OVSTREAM_CUDASHM_CLIENT_API ovstream_string_t ovstream_get_last_error(void);

//--------------------------------------------------------------
// @brief Opaque CUDASHM client handle.
//--------------------------------------------------------------
typedef struct ovstream_cudashm_client_t ovstream_cudashm_client_t;

//--------------------------------------------------------------
// @brief Frame view returned by `ovstream_cudashm_client_wait_frame`.
//
// `device_ptr` is a CUDA device pointer (uintptr_t-typed for ABI
// stability; consumers cast to `void*` for the CUDA Runtime API or
// `CUdeviceptr` for the Driver API) into the producer's GPU memory.
// The producer exports pitched *linear* device memory allocated via
// `cudaMallocPitch`, so the pointer must be treated as linear memory
// only — do not cast to `cudaArray*` or otherwise treat as a texture
// / surface object. The pointer remains valid until the producer
// rotates onto the same slot -- which can happen at any time after
// `wait_frame` returns. Consumers must launch their read kernel and
// either complete it within the producer's ring depth, or copy the
// pixels into their own GPU buffer first.
//
// `pitch_bytes` is the row pitch chosen by `cudaMallocPitch` on the
// producer side (typically 512-byte aligned on modern GPUs); it
// equals the value reported by the server's IPC handle, NOT
// necessarily `width * 4`. `format` is one of the
// `OVSTREAM_SHM_FORMAT_*` constants from `ovstream_types.h`
// (currently always `OVSTREAM_SHM_FORMAT_BGRA8`).
//
// `slot_index` identifies which ring slot the data lives in (useful
// for debugging / profiling; not normally needed by application code).
//--------------------------------------------------------------
typedef struct
{
    uintptr_t   device_ptr;
    uint64_t    sequence;
    uint64_t    capture_timestamp_ns;
    uint32_t    width;
    uint32_t    height;
    uint32_t    pitch_bytes;
    uint32_t    format;
    uint32_t    slot_index;
} ovstream_cudashm_frame_t;

//--------------------------------------------------------------
// @brief Attach to a running ovstream CUDASHM server.
//
// `stream_name` must match the value the server was started with
// (or the auto-default `"ovstream-<pid>"` if the producer left it
// unset). On success the client has imported all N producer IPC
// handles into the calling process's active CUDA context and is
// ready to call `wait_frame`.
//
// The calling thread's current CUDA device must match the producer's
// device, or be able to peer-access it (the import enables lazy peer
// access). The producer's device is its `cuda_device`, or the device
// current on its thread at `ovstream_start` when `cuda_device` was -1;
// on single-GPU hosts both sides default to device 0. A mismatch with
// no peer path surfaces as a `cudaIpcOpenMemHandle` failure whose
// OvstreamError detail names the producer's device; select that device
// (`cudaSetDevice`) and retry, or read it after attach via
// `ovstream_cudashm_client_get_producer_device`.
//
// @param stream_name Input-string view; empty (`length == 0`) is
//                    rejected.
// @param out_client  [out] Receives the new client handle. Never
//                    written to on failure.
//
// @return
//   - `OVSTREAM_API_SUCCESS` on success.
//   - `OVSTREAM_API_INVALID_ARGUMENT` if `stream_name` is empty or
//     `out_client` is NULL.
//   - `OVSTREAM_API_ERROR` if the producer isn't running, the name
//     doesn't match, the protocol version is incompatible, or any
//     `cudaIpcOpenMemHandle` call fails.
//
// @ingroup cudashm_client
//--------------------------------------------------------------
OVSTREAM_CUDASHM_CLIENT_API ovstream_result_t ovstream_cudashm_client_create(
    ovstream_string_t stream_name,
    ovstream_cudashm_client_t** out_client);

//--------------------------------------------------------------
// @brief Detach and free a CUDASHM client handle.
//
// Closes each imported IPC handle (via `cudaIpcCloseMemHandle`),
// unmaps the metadata region, and closes the control channel. The
// server observes the detach via control-channel close. Passing NULL
// is a safe no-op.
//
// @ingroup cudashm_client
//--------------------------------------------------------------
OVSTREAM_CUDASHM_CLIENT_API ovstream_result_t ovstream_cudashm_client_destroy(
    ovstream_cudashm_client_t* client);

//--------------------------------------------------------------
// @brief Wait for the next frame newer than the one previously returned.
//
// Per-client cursor; multiple clients independently observe the
// producer stream.
//
// @par Ownership / Lifetime
// `out_frame->device_ptr` addresses GPU memory in the producer's
// ring; the producer may rotate onto the same slot at any time
// after `wait_frame` returns. `wait_frame` detects mid-publish
// races via the sequence-number recheck and returns only a coherent
// snapshot of the slot's metadata, but the *pixels* may be
// overwritten while the consumer's read kernel runs. Sizing the
// producer's ring (`cudashm.slot_count`) deep enough to cover the
// consumer's worst-case kernel latency is the contract.
//
// @param client     The client handle.
// @param timeout_ms Wait policy: 0 = non-blocking poll; <0 = wait
//                   indefinitely; >0 = wait up to that many ms.
// @param out_frame  [out] Receives the frame view on success.
//
// @return
//   - `OVSTREAM_API_SUCCESS` with `*out_frame` populated.
//   - `OVSTREAM_API_TIMEOUT` if the wait window elapses.
//   - `OVSTREAM_API_INVALID_STATE` if the producer has stopped.
//   - `OVSTREAM_API_INVALID_ARGUMENT` if `client` or `out_frame` is
//     NULL.
//
// @ingroup cudashm_client
//--------------------------------------------------------------
OVSTREAM_CUDASHM_CLIENT_API ovstream_result_t ovstream_cudashm_client_wait_frame(
    ovstream_cudashm_client_t* client,
    int32_t timeout_ms,
    ovstream_cudashm_frame_t* out_frame);

//--------------------------------------------------------------
// @brief Query whether the producer is still alive.
//
// `*out_alive` is true if the producer is running, false once it has
// stopped or crashed.
//
// @ingroup cudashm_client
//--------------------------------------------------------------
OVSTREAM_CUDASHM_CLIENT_API ovstream_result_t ovstream_cudashm_client_is_producer_alive(
    const ovstream_cudashm_client_t* client,
    bool* out_alive);

//--------------------------------------------------------------
// @brief Get the CUDA device ordinal the producer allocated its ring on.
//
// Read from the metadata region the producer stamped at
// `ovstream_start` (its `cuda_device`, or the device current on its
// calling thread when `cuda_device` was -1). The consumer reads that
// GPU memory through CUDA IPC, so its CUDA work must run on this device
// (or a peer-capable one). `cudaIpcOpenMemHandle` already happened in
// `..._create`, so this is for diagnostics and for placing the
// consumer's own read kernels; to pick the device *before* attaching,
// match it out-of-band with the producer's `cuda_device`.
//
// @param client     The client handle.
// @param out_device [out] Receives the producer's CUDA device ordinal.
//
// @return
//   - `OVSTREAM_API_SUCCESS` with `*out_device` populated.
//   - `OVSTREAM_API_INVALID_ARGUMENT` if `client` or `out_device` is NULL.
//
// @ingroup cudashm_client
//--------------------------------------------------------------
OVSTREAM_CUDASHM_CLIENT_API ovstream_result_t ovstream_cudashm_client_get_producer_device(
    const ovstream_cudashm_client_t* client,
    int32_t* out_device);

//--------------------------------------------------------------
// @brief Send an input event to the producer over the control channel.
//
// Same line protocol as the SHM client; the producer receives events
// via the same callback it registered with
// `ovstream_set_input_callback`.
//
// @ingroup cudashm_client
//--------------------------------------------------------------
OVSTREAM_CUDASHM_CLIENT_API ovstream_result_t ovstream_cudashm_client_send_input_event(
    ovstream_cudashm_client_t* client,
    const ovstream_input_event_t* event);

//--------------------------------------------------------------
// @brief Send a client-to-server text message.
//
// @ingroup cudashm_client
//--------------------------------------------------------------
OVSTREAM_CUDASHM_CLIENT_API ovstream_result_t ovstream_cudashm_client_send_message(
    ovstream_cudashm_client_t* client,
    ovstream_string_t message);

//--------------------------------------------------------------
// @brief Send a client-to-server Unicode / IME text event.
//
// @ingroup cudashm_client
//--------------------------------------------------------------
OVSTREAM_CUDASHM_CLIENT_API ovstream_result_t ovstream_cudashm_client_send_unicode(
    ovstream_cudashm_client_t* client,
    ovstream_string_t text);

//--------------------------------------------------------------
// @brief Register (or clear) the server-to-client message callback.
//
// @ingroup cudashm_client
//--------------------------------------------------------------
OVSTREAM_CUDASHM_CLIENT_API ovstream_result_t ovstream_cudashm_client_set_message_callback(
    ovstream_cudashm_client_t* client,
    void (*callback)(ovstream_string_t message, void* user_data),
    void* user_data);

#ifdef __cplusplus
} // extern "C"
#endif
