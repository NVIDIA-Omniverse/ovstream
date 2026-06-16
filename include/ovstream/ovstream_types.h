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

#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>

//--------------------------------------------------------------
// @brief Semantic version of the SDK at compile time.
//
// The runtime version is reported by `ovstream_get_version()`;
// mismatched compile-time vs. runtime versions indicate a
// header/library skew.
//--------------------------------------------------------------
#define OVSTREAM_VERSION_MAJOR 0
#define OVSTREAM_VERSION_MINOR 4
#define OVSTREAM_VERSION_PATCH 1

//--------------------------------------------------------------
// @brief Mark a public entry point, type, or field as deprecated.
//
// The `msg` argument is a string literal explaining what to use
// instead; compilers that surface deprecation messages will
// print it at every use site.
//
// Usage:
//     OVSTREAM_DEPRECATED("Use ovstream_new_thing() instead.")
//     bool ovstream_old_thing(void);
//
// On compilers that don't recognize the attribute the macro
// expands to nothing -- deprecated symbols still compile and
// link, they just don't produce a warning.
//--------------------------------------------------------------
#if defined(_MSC_VER)
#   define OVSTREAM_DEPRECATED(msg) __declspec(deprecated(msg))
#elif defined(__GNUC__) || defined(__clang__)
#   define OVSTREAM_DEPRECATED(msg) __attribute__((deprecated(msg)))
#else
#   define OVSTREAM_DEPRECATED(msg)
#endif

#ifdef __cplusplus
extern "C"
{
#endif

//--------------------------------------------------------------
// @brief Opaque server handle.
//
// Created by `ovstream_create_server`, destroyed by
// `ovstream_destroy_server`. All other APIs take a pointer to
// this type.
//--------------------------------------------------------------
typedef struct ovstream_server_t ovstream_server_t;

//--------------------------------------------------------------
// @brief UTF-8 string view with explicit length.
//
// Used for every string parameter and string-bearing callback
// argument in the SDK.
//
// Direction-dependent contract:
//
//   Caller -> SDK (input strings, e.g. config fields,
//   `ovstream_send_message`):
//       - `ptr` may be any UTF-8 byte sequence; the SDK reads
//         exactly `length` bytes and never reads at `ptr[length]`.
//         The bytes are NOT required to be null-terminated.
//       - `ptr` may be NULL only when `length == 0` (empty string);
//         a NULL pointer with non-zero length is an error.
//
//   SDK -> caller (output strings, including all callback
//   arguments and `ovstream_get_last_error`):
//       - `ptr` is non-NULL and ALWAYS null-terminated:
//         `ptr[length] == '\0'`. `length` is the byte count
//         excluding the terminator.
//       - Callers may either pass `ptr` directly to C string
//         APIs or memcpy `length` bytes -- no extra `strlen`
//         pass is needed.
//       - Pointer lifetime is documented per call site.
//
// This struct is intentionally analogous to ovrtx's `ovx_string_t`
// (same `{ptr, length}` shape) and the two should eventually be
// consolidated into a shared `ovx` utility header once the broader
// OV libraries effort agrees on the input null-termination contract.
// Until then, this is the ovstream-side definition.
//--------------------------------------------------------------
typedef struct
{
    const char* ptr;    // UTF-8 bytes (see direction-dependent contract above).
    size_t      length; // Byte count; does NOT include any null terminator.
} ovstream_string_t;

//--------------------------------------------------------------
// @brief Build an `ovstream_string_t` from a string literal at compile
// time. `sizeof(s) - 1` strips the trailing null terminator from
// the byte count, so the produced view satisfies the "input"
// contract (length excludes any terminator). C++ uses brace-init;
// C uses a compound literal.
//--------------------------------------------------------------
#ifdef __cplusplus
#   define OVSTREAM_STRING_LITERAL(s) ovstream_string_t{ (s), sizeof(s) - 1 }
#else
#   define OVSTREAM_STRING_LITERAL(s) (ovstream_string_t){ (s), sizeof(s) - 1 }
#endif

//--------------------------------------------------------------
// @brief Result status codes returned in `ovstream_result_t::status`.
//
//   SUCCESS              The call completed successfully. Out-parameters
//                        (if any) are populated.
//   ERROR                Generic failure. `ovstream_get_last_error()`
//                        returns the call-site detail string.
//   TIMEOUT              The operation timed out (e.g.
//                        `ovstream_shm_client_wait_frame` returned no
//                        frame within the requested wait window).
//   INVALID_ARGUMENT     A required pointer was NULL, an enum was
//                        out-of-range, or a value violated the
//                        documented constraints (e.g. zero width).
//   INVALID_STATE        The server (or SDK) is not in a state where
//                        this call is valid (e.g. streaming before
//                        start, double-start, calling functions on a
//                        destroyed handle).
//   NOT_SUPPORTED        The operation is not available for this
//                        protocol or build (e.g. audio on RTSP, message
//                        send on RTSP).
//
// Detail strings are always available via `ovstream_get_last_error()`
// regardless of the status code -- the enum is the programmatic
// switch, the string is the human-readable explanation.
//--------------------------------------------------------------
typedef enum
{
    OVSTREAM_API_SUCCESS = 0,
    OVSTREAM_API_ERROR,
    OVSTREAM_API_TIMEOUT,
    OVSTREAM_API_INVALID_ARGUMENT,
    OVSTREAM_API_INVALID_STATE,
    OVSTREAM_API_NOT_SUPPORTED,
} ovstream_api_status_t;

//--------------------------------------------------------------
// @brief Result returned by every public ovstream entry point.
//
// Status-only today; the struct shape (rather than returning the
// enum directly) reserves room for future detail fields (e.g.
// async-op handles) without an ABI break.
//--------------------------------------------------------------
typedef struct
{
    ovstream_api_status_t status;
} ovstream_result_t;

//--------------------------------------------------------------
// @brief Convenience check for the "happy path".
//
// Equivalent to `((r).status == OVSTREAM_API_SUCCESS)`. Use at
// call sites that only care whether the operation succeeded:
//
//     if (!OVSTREAM_OK(ovstream_start(server, &config)))
//     {
//         fprintf(stderr, "%s\n", ovstream_get_last_error().ptr);
//         return 1;
//     }
//--------------------------------------------------------------
#define OVSTREAM_OK(r) ((r).status == OVSTREAM_API_SUCCESS)

//--------------------------------------------------------------
// @brief Severity of a log message delivered through `ovstream_log_callback_t`.
//
// VERBOSE is the most verbose; ERROR is the least verbose level
// that gets delivered to the callback. DEFAULT and NONE are
// set-level-only sentinels: pass them via
// `ovstream_init_config_t::log_min_severity` to mean "use the
// SDK's default of WARNING" or "suppress every line"; the
// callback itself never receives either value.
//--------------------------------------------------------------
typedef enum
{
    OVSTREAM_LOG_DEFAULT = 0,
    OVSTREAM_LOG_VERBOSE = 1,
    OVSTREAM_LOG_INFO    = 2,
    OVSTREAM_LOG_WARNING = 3,
    OVSTREAM_LOG_ERROR   = 4,
    OVSTREAM_LOG_NONE    = 5,
} ovstream_log_level_t;

//--------------------------------------------------------------
// @brief Log callback signature.
//
// Registered via `ovstream_init_config_t` during
// `ovstream_initialize`. Invoked synchronously from whichever
// thread produced the log line (SDK internals, GStreamer, or
// StreamSDK) -- the callback body is responsible for its own
// thread marshaling.
//
// @note The Python `ovstream.initialize(log_fn=...)` wrapper
// adapts this signature for ergonomic reasons: the Python
// callback receives `(severity, channel, message, timestamp)`
// (no `user_data`, and `channel` precedes `message` per the
// usual logger convention). C consumers see the signature
// declared below.
//
// @param severity  Severity of the message. Never `OVSTREAM_LOG_NONE`
//                  (which is a set-level-only sentinel).
// @param message   UTF-8 message body. Output-string contract:
//                  `message.ptr` is non-NULL and null-terminated;
//                  `message.length` excludes the terminator. Valid
//                  only for the duration of the callback.
// @param channel   Tag identifying the originating subsystem (e.g.
//                  "ovstream.rtsp", "ovstream.webrtc"). Output-string
//                  contract: `channel.ptr` is non-NULL and null-terminated;
//                  `channel.length` excludes the terminator. May be
//                  the empty view for SDK-internal lines that don't
//                  belong to a named subsystem.
// @param timestamp Monotonic seconds since an unspecified epoch
//                  (`std::chrono::steady_clock`). The library
//                  auto-fills this field before dispatch, so the
//                  callback always observes a positive value.
// @param user_data Opaque pointer from `ovstream_init_config_t::log_user_data`.
//--------------------------------------------------------------
typedef void (*ovstream_log_callback_t)(ovstream_log_level_t severity,
                                        ovstream_string_t message,
                                        ovstream_string_t channel,
                                        double timestamp,
                                        void* user_data);

//--------------------------------------------------------------
// @brief Whether a key / mouse button is pressed or released.
//--------------------------------------------------------------
typedef enum
{
    OVSTREAM_KEY_STATE_UP   = 0,
    OVSTREAM_KEY_STATE_DOWN = 1,
} ovstream_key_state_t;

//--------------------------------------------------------------
// @brief Which category of mouse event is being reported.
//
// Determines which fields of `ovstream_mouse_event_t` are
// meaningful.
//--------------------------------------------------------------
typedef enum
{
    OVSTREAM_MOUSE_MOVE   = 0,
    OVSTREAM_MOUSE_WHEEL  = 1,
    OVSTREAM_MOUSE_BUTTON = 2,
} ovstream_mouse_event_type_t;

//--------------------------------------------------------------
// @brief Which mouse button is being reported.
//
// Only meaningful for `OVSTREAM_MOUSE_BUTTON` events.
//--------------------------------------------------------------
typedef enum
{
    OVSTREAM_MOUSE_BUTTON_NONE   = 0,
    OVSTREAM_MOUSE_BUTTON_LEFT   = 1,
    OVSTREAM_MOUSE_BUTTON_MIDDLE = 2,
    OVSTREAM_MOUSE_BUTTON_RIGHT  = 3,
    OVSTREAM_MOUSE_BUTTON_EXTRA1 = 4,
    OVSTREAM_MOUSE_BUTTON_EXTRA2 = 5,
} ovstream_mouse_button_t;

//--------------------------------------------------------------
// @brief A keyboard key press or release reported by the client.
//--------------------------------------------------------------
typedef struct
{
    uint16_t                key_code;     // Platform key code from the client.
    uint16_t                scan_code;    // Raw hardware scan code.
    uint16_t                modifiers;    // Bitmask of held modifiers (Shift/Ctrl/Alt/Meta).
    ovstream_key_state_t    key_state;    // Up (release) or Down (press).
    uint64_t                timestamp_us; // Client-side capture timestamp in us (0 if unavailable).
} ovstream_keyboard_event_t;

//--------------------------------------------------------------
// @brief A mouse movement, wheel, or button event reported by the client.
//
// The `type` field selects how the other fields are interpreted:
//
//   type    | x, y             | data                    | data2   | button_state | scroll_x/y
//   --------+------------------+-------------------------+---------+--------------+-----------
//   MOVE    | absolute pixels  | width (absolute-coord)  | height  | unused       | unused
//   WHEEL   | cursor position  | unused                  | unused  | unused       | scroll notches
//   BUTTON  | cursor position  | ovstream_mouse_button_t | unused  | Up / Down    | unused
//
// WHEEL: scroll_x / scroll_y are signed scroll distances in standard notches
// (1.0 = one full notch). High-precision devices (most browsers on Windows)
// deliver sub-notch values; treat as a continuous quantity.
//--------------------------------------------------------------
typedef struct
{
    ovstream_mouse_event_type_t type;         // Selects MOVE / WHEEL / BUTTON interpretation.
    uint16_t                    modifiers;    // Bitmask of held keyboard modifiers.
    int32_t                     x;            // See type-dispatch table above.
    int32_t                     y;            // See type-dispatch table above.
    int32_t                     data;         // See type-dispatch table above.
    int32_t                     data2;        // See type-dispatch table above.
    ovstream_key_state_t        button_state; // BUTTON events only (Up / Down).
    uint64_t                    timestamp_us; // Client-side capture timestamp in us.
    float                       scroll_x;     // WHEEL events only: horizontal scroll in notches.
    float                       scroll_y;     // WHEEL events only: vertical scroll in notches.
} ovstream_mouse_event_t;

//--------------------------------------------------------------
// @brief Gamepad control identifier.
//
// Buttons are binary; axes are signed 16-bit; triggers are
// scaled to the positive half of a signed 16-bit range. See
// `ovstream_gamepad_event_t::position` for per-group value-range
// details.
//--------------------------------------------------------------
typedef enum
{
    // Buttons
    OVSTREAM_GAMEPAD_BTN_A = 0,
    OVSTREAM_GAMEPAD_BTN_B,
    OVSTREAM_GAMEPAD_BTN_X,
    OVSTREAM_GAMEPAD_BTN_Y,
    OVSTREAM_GAMEPAD_BTN_LEFT_SHOULDER,
    OVSTREAM_GAMEPAD_BTN_RIGHT_SHOULDER,
    OVSTREAM_GAMEPAD_BTN_BACK,
    OVSTREAM_GAMEPAD_BTN_START,
    OVSTREAM_GAMEPAD_BTN_LEFT_THUMB,
    OVSTREAM_GAMEPAD_BTN_RIGHT_THUMB,
    OVSTREAM_GAMEPAD_BTN_DPAD_UP,
    OVSTREAM_GAMEPAD_BTN_DPAD_DOWN,
    OVSTREAM_GAMEPAD_BTN_DPAD_LEFT,
    OVSTREAM_GAMEPAD_BTN_DPAD_RIGHT,
    // Axes (-32768 to +32767)
    OVSTREAM_GAMEPAD_AXIS_LEFT_X,
    OVSTREAM_GAMEPAD_AXIS_LEFT_Y,
    OVSTREAM_GAMEPAD_AXIS_RIGHT_X,
    OVSTREAM_GAMEPAD_AXIS_RIGHT_Y,
    // Triggers (0 to 32767)
    OVSTREAM_GAMEPAD_TRIGGER_LEFT,
    OVSTREAM_GAMEPAD_TRIGGER_RIGHT,
    OVSTREAM_GAMEPAD_NUM_CONTROLS,
} ovstream_gamepad_control_t;

//--------------------------------------------------------------
// @brief A gamepad button / axis / trigger change reported by the client.
//
// Exactly one control is reported per event; clients diff their
// gamepad state and emit one event per change. The `position`
// field's range depends on `control`:
//   Button:  0 = released, 1 = pressed
//   Axis:    -32768..+32767
//   Trigger: 0..32767
//--------------------------------------------------------------
typedef struct
{
    ovstream_gamepad_control_t  control;      // Which control changed.
    int16_t                     position;     // New value; range depends on control (see above).
    uint8_t                     gamepad_id;   // Gamepad index (0..3).
    uint64_t                    timestamp_us; // Client-side capture timestamp in us.
} ovstream_gamepad_event_t;

//--------------------------------------------------------------
// @brief Which kind of input event is being reported.
//
// Selects the active member of the `ovstream_input_event_t`
// union.
//--------------------------------------------------------------
typedef enum
{
    OVSTREAM_INPUT_KEYBOARD = 0,
    OVSTREAM_INPUT_MOUSE    = 1,
    OVSTREAM_INPUT_GAMEPAD  = 2,
} ovstream_input_event_type_t;

//--------------------------------------------------------------
// @brief Tagged union over the three input event variants.
//
// Exactly one of `keyboard`, `mouse`, or `gamepad` is
// meaningful, determined by `type`.
//--------------------------------------------------------------
typedef struct
{
    ovstream_input_event_type_t type;
    union
    {
        ovstream_keyboard_event_t keyboard;
        ovstream_mouse_event_t    mouse;
        ovstream_gamepad_event_t  gamepad;
    };
} ovstream_input_event_t;

//--------------------------------------------------------------
// @brief Client connect / disconnect callback signature.
//
// Registered via `ovstream_set_connection_callback`. See that
// function's header for lifecycle and "fire on already-connected"
// semantics.
//
// @param server    The server that produced the event.
// @param connected True on connect, false on disconnect.
// @param user_data Opaque pointer from `ovstream_set_connection_callback`.
//--------------------------------------------------------------
typedef void (*ovstream_connection_callback_t)(ovstream_server_t* server,
                                               bool connected,
                                               void* user_data);

//--------------------------------------------------------------
// @brief Client-to-server text message callback signature.
//
// Registered via `ovstream_set_message_callback`.
//
// @param server    The server that produced the event.
// @param message   UTF-8 message payload. Output-string contract:
//                  `message.ptr` is non-NULL and null-terminated,
//                  `message.length` excludes the terminator. Valid
//                  only for the duration of the callback.
// @param user_data Opaque pointer from `ovstream_set_message_callback`.
//--------------------------------------------------------------
typedef void (*ovstream_message_callback_t)(ovstream_server_t* server,
                                            ovstream_string_t message,
                                            void* user_data);

//--------------------------------------------------------------
// @brief Client input-event callback signature.
//
// Registered via `ovstream_set_input_callback`.
//
// @param server    The server that produced the event.
// @param event     Non-NULL input event. Valid only for the
//                  duration of the callback; copy out any fields
//                  that need to outlive it.
// @param user_data Opaque pointer from `ovstream_set_input_callback`.
//--------------------------------------------------------------
typedef void (*ovstream_input_callback_t)(ovstream_server_t* server,
                                          const ovstream_input_event_t* event,
                                          void* user_data);

//--------------------------------------------------------------
// @brief Client Unicode / IME text-input callback signature.
//
// Registered via `ovstream_set_unicode_callback`. Delivered when
// the client sends a composed text event (IME, emoji picker,
// clipboard paste on some platforms) that can't be represented as
// a single keycode.
//
// @param server    The server that produced the event.
// @param text      UTF-8 text payload. Output-string contract:
//                  `text.ptr` is non-NULL and null-terminated,
//                  `text.length` excludes the terminator. Valid only
//                  for the duration of the callback; copy out if the
//                  string needs to outlive it.
// @param user_data Opaque pointer from `ovstream_set_unicode_callback`.
//--------------------------------------------------------------
typedef void (*ovstream_unicode_callback_t)(ovstream_server_t* server,
                                            ovstream_string_t text,
                                            void* user_data);

//--------------------------------------------------------------
// @brief A single ICE server entry for WebRTC NAT traversal.
//
// Unified STUN/TURN shape modelled on WebRTC's `RTCIceServer`:
// STUN entries leave `username` and `credential` empty; TURN
// entries fill them. The server kind is inferred from each URL's
// scheme (`stun:` / `stuns:` -> STUN, `turn:` / `turns:` -> TURN).
//
// All string fields follow the input-string contract: read for
// exactly `length` bytes, NOT required to be null-terminated.
// Empty views are permitted for `username` / `credential` to
// signal "no credentials" (STUN); an empty `urls` view rejects
// the entry.
//--------------------------------------------------------------
typedef struct
{
    // Comma-separated URL list (one or more). Per-entry URL count
    // is capped at 3 to match the underlying StreamSDK limit.
    ovstream_string_t urls;
    // TURN username, or empty for STUN-only entries.
    ovstream_string_t username;
    // TURN password / token, or empty for STUN-only entries.
    ovstream_string_t credential;
} ovstream_webrtc_ice_server_t;

//--------------------------------------------------------------
// @brief Batch ICE configuration passed to `ovstream_webrtc_set_ice_servers`.
//
// Caller owns the `servers` array and its referenced strings; the
// SDK copies any storage it needs to retain before
// `ovstream_webrtc_set_ice_servers` returns, so the caller is
// free to release / reuse both immediately after.
//
// `server_count == 0` (or `config == NULL` to the setter) clears
// the previously configured ICE servers. Otherwise `servers` must
// be non-NULL and reference exactly `server_count` entries.
//--------------------------------------------------------------
typedef struct
{
    const ovstream_webrtc_ice_server_t* servers;      // Array, may be NULL when server_count == 0.
    uint32_t                            server_count; // 0 to clear all ICE servers.
} ovstream_webrtc_ice_config_t;

//--------------------------------------------------------------
// @brief Library-wide initialization config passed to `ovstream_initialize`.
//
// All fields are optional (a zero-initialized struct is valid).
//
// `log_min_severity` is the minimum severity at which the SDK invokes
// `log_callback`. Messages below this level are filtered inside the
// SDK before the callback fires, so the callback never pays the
// formatting cost of per-frame VERBOSE chatter. `OVSTREAM_LOG_DEFAULT`
// (the value a zero-initialized struct produces) remaps to
// `OVSTREAM_LOG_WARNING`; `OVSTREAM_LOG_VERBOSE` is the firehose;
// `OVSTREAM_LOG_NONE` suppresses every line. `log_min_severity` is
// only consulted when `log_callback` is non-NULL; with no callback
// registered the SDK does not log at all and the threshold is moot.
//--------------------------------------------------------------
typedef struct
{
    ovstream_log_callback_t  log_callback;     // Optional log callback (NULL disables logging).
    void*                    log_user_data;    // Opaque pointer passed back to log_callback.
    ovstream_log_level_t     log_min_severity; // Minimum severity (DEFAULT -> WARNING).
} ovstream_init_config_t;

//--------------------------------------------------------------
// @brief Video input selector for `ovstream_server_config_t`.
//
// Identifies how each frame passed to `ovstream_stream_video` is
// described; different inputs use different
// `ovstream_video_frame_t` fields.
//
//   CUDA   - Raw CUDA BGRA8. `frame.buffer` is a CUDA device pointer;
//            `width` / `height` / `pitch_bytes` set; `size_bytes = 0`.
//            The SDK encodes with its own NVENC instance.
//
//   TENSOR - Raw CUDA BGRA8 described as a DLPack tensor.
//            `frame.buffer` points to a `DLTensor` with shape
//            `{H, W, 4}`, dtype `uint8`, device `kDLCUDA`, and strides
//            describing the row pitch. The frame's `width` / `height`
//            / `pitch_bytes` / `size_bytes` fields are ignored.
//            Callers supply their own `dlpack.h`; see
//            https://github.com/dmlc/dlpack for the DLPack spec.
//
//   CUSTOM - RTSP-only pre-encoded host buffer through a custom
//            GStreamer pipeline. Requires
//            `ovstream_server_config_t::rtsp::pipeline` to be set.
//            `frame.buffer` is a host pointer; `size_bytes > 0` and
//            `pitch_bytes = 0`. Raw-CUDA through a custom pipeline
//            is not supported -- write your pipeline against
//            `video/x-h264` or `video/x-h265` appsrc caps, not
//            `video/x-raw`.
//
//   H264   - Pre-encoded bitstream passthrough. `frame.buffer` is a
//   H265     host pointer; the caller has already encoded the frame;
//   AV1      `size_bytes > 0` and `pitch_bytes = 0`. AV1 is supported
//            only on WebRTC and native servers, not RTSP.
//--------------------------------------------------------------
typedef enum
{
    OVSTREAM_VIDEO_INPUT_CUDA   = 0,
    OVSTREAM_VIDEO_INPUT_TENSOR = 1,
    OVSTREAM_VIDEO_INPUT_CUSTOM = 2,
    OVSTREAM_VIDEO_INPUT_H264   = 3,
    OVSTREAM_VIDEO_INPUT_H265   = 4,
    OVSTREAM_VIDEO_INPUT_AV1    = 5,
} ovstream_video_input_t;

//--------------------------------------------------------------
// @brief Streaming protocol selector for `ovstream_create_server`.
//
// `WEBRTC` and `NATIVE` both use StreamSDK but with different
// backend modes (WebRTC-interop and native-NVSS, respectively);
// they otherwise expose the same feature set. `SHM` writes raw
// BGRA8 frames into a shared-memory ring buffer for same-machine
// consumers (Electron/WebGL clients via the bundled
// `ovstream_shm_client` library); see `ovstream_shm_client.h`.
// `CUDASHM` keeps frames GPU-resident: the server allocates a
// ring of CUDA buffers and hands out `cudaIpcMemHandle_t` values
// over the control channel, so same-host (or same-container-host)
// consumers can read pixels directly from GPU memory without a
// device-to-host copy. See `ovstream_cudashm_client.h`.
//--------------------------------------------------------------
typedef enum
{
    OVSTREAM_SERVER_WEBRTC  = 0,
    OVSTREAM_SERVER_NATIVE  = 1,
    OVSTREAM_SERVER_RTSP    = 2,
    OVSTREAM_SERVER_SHM     = 3,
    OVSTREAM_SERVER_CUDASHM = 4,
} ovstream_server_type_t;

//--------------------------------------------------------------
// @brief Pixel-format identifier carried in the SHM wire protocol's per-region header.
//
// Only BGRA8 is supported in V1; the field is a `uint32_t` on the
// wire so future formats can be added without breaking the ABI.
//--------------------------------------------------------------
#define OVSTREAM_SHM_FORMAT_BGRA8 1u

//--------------------------------------------------------------
// @brief Server configuration passed to `ovstream_start`.
//
// Use `ovstream_config_defaults` to populate with sensible
// defaults before overriding specific fields.
//
// Only the sub-struct matching the server type is consulted at
// `ovstream_start`; the rest are ignored:
//   WEBRTC / NATIVE -> `webrtc`
//   RTSP            -> `rtsp`
//   SHM             -> `shm`
//   CUDASHM         -> `cudashm`
//--------------------------------------------------------------
typedef struct
{
    uint32_t                width;        // Stream width in pixels (default 1920).
    uint32_t                height;       // Stream height in pixels (default 1080).
    uint16_t                target_fps;   // Target frames per second (default 60).
    uint16_t                stream_port;  // Media transport port (0 = default: 8554 RTSP, 47998 WebRTC, 47999 native; ignored for SHM and CUDASHM).
    ovstream_video_input_t  video_input;  // See ovstream_video_input_t. Must be CUDA or TENSOR for SHM and CUDASHM.
    int32_t                 cuda_device;  // GPU ordinal for raw-CUDA input. Any negative value uses the backend default (-1 by convention; WebRTC/native display adapter, SHM/CUDASHM the calling thread's current device). Set to the producer's device on multi-GPU hosts.
    uintptr_t               cuda_context; // Producer's CUcontext (cast to uintptr_t) paired with cuda_device. WebRTC/native only; ignored when cuda_device < 0. REQUIRED if the producer allocates frames in its own context (Warp, ovrtx, most renderers); 0 is only safe when frames live in the device's primary context (e.g. plain cudaMalloc).

    // WebRTC/native-specific (ignored for RTSP, SHM, and CUDASHM).
    struct
    {
        uint16_t          signal_port; // Signaling TCP port (0 = default 49100).
        ovstream_string_t public_ip;   // Fixed public IP (disables ICE when non-empty).
                                       // Input-string contract; empty view = ICE enabled.
    } webrtc;

    // RTSP-specific (ignored for WebRTC/native, SHM, and CUDASHM).
    struct
    {
        // Custom GStreamer pipeline string (only consulted when
        // `video_input == OVSTREAM_VIDEO_INPUT_CUSTOM`). Pipeline must
        // accept pre-encoded host buffers through its appsrc element;
        // raw-CUDA input is not supported in custom mode. Input-string
        // contract; empty view rejects CUSTOM.
        ovstream_string_t pipeline;
        // RTSP URL path (empty defaults to "/stream"). Input-string
        // contract.
        ovstream_string_t mount_point;
    } rtsp;

    // SHM-specific (ignored for WebRTC/native, RTSP, and CUDASHM).
    //
    // The SHM backend writes raw BGRA8 frames into a named shared-memory
    // ring buffer plus a sibling control channel (Unix domain socket on
    // POSIX, named pipe on Windows). Same-machine clients attach via
    // `ovstream_shm_client_create` with the same `stream_name`. The
    // control channel carries the reverse direction too: `send_message`
    // plus the message / input / unicode callbacks all work on SHM
    // servers and clients. `stream_audio` is not supported (always
    // fails).
    struct
    {
        // Identifier shared with attaching clients. Becomes part of the
        // shared-memory and control endpoint paths; max 63 UTF-8 bytes,
        // ASCII recommended. Input-string contract; empty view defaults
        // to "ovstream-<pid>".
        ovstream_string_t stream_name;
        // Ring depth (number of slots in the shared region). 0 defaults
        // to 3; clamped to [2, 8] at start(). Larger values tolerate
        // slower readers at the cost of memory.
        uint32_t          slot_count;
    } shm;

    // CUDASHM-specific (ignored for WebRTC/native, RTSP, and SHM).
    //
    // The CUDASHM backend keeps frames in GPU memory: the server
    // allocates a ring of CUDA buffers, hands out
    // `cudaIpcMemHandle_t` values over the control channel at
    // attach time, then D2D-copies each new frame into the next
    // slot. Same-host (or same-container-host) clients attach via
    // `ovstream_cudashm_client_create` and read pixels directly
    // from the imported device pointers -- no device-to-host copy.
    //
    // Sharing semantics:
    //   - Server owns the buffers; producer lifecycle is identical
    //     to SHM (call `ovstream_stream_video` with a CUDA pointer
    //     each frame).
    //   - Consumer is responsible for finishing its read kernel
    //     before the server's ring wraps onto the same slot;
    //     `slot_count` is the tuning knob.
    //
    // `stream_audio` is not supported (always fails). The control
    // channel carries `send_message` / input / unicode the same way
    // as SHM.
    struct
    {
        // Identifier shared with attaching clients. Becomes part of
        // the shared-memory and control endpoint paths; max 63
        // UTF-8 bytes, ASCII recommended. Input-string contract;
        // empty view defaults to "ovstream-<pid>". Coexists with a
        // same-named SHM server: cudashm uses a distinct OS-level
        // path prefix internally.
        ovstream_string_t stream_name;
        // Ring depth (number of GPU buffers + slot headers). 0
        // defaults to 4; clamped to [2, 8] at start(). The
        // "consumer falls behind" tuning knob -- larger values
        // tolerate longer-running consumer kernels at the cost of
        // GPU memory (slot_count * width * height * 4 bytes).
        //
        // The ring buffers are allocated on `cuda_device` when set,
        // otherwise on the calling thread's current CUDA device at
        // `ovstream_start` time (set it via `cudaSetDevice(N)` first).
        uint32_t          slot_count;
    } cudashm;
} ovstream_server_config_t;

//--------------------------------------------------------------
// @brief Optional CUDA synchronization hint on `ovstream_video_frame_t`.
//
// Describes where the producer's GPU work was queued so the
// encoder ingest can chain on it without a global device-wide
// sync (`cudaDeviceSynchronize`).
//
// Zero-initialised (both fields 0) means "the caller has already
// synchronized; the buffer is safe to read on entry to
// `ovstream_stream_video`" -- the contract that applied before
// this field existed. Existing callers keep working unchanged.
//
// `wait_event` (a `cudaEvent_t` / `CUevent` cast to `uintptr_t`)
// is the most precise signal: ovstream waits on that event before
// reading the buffer. If only `stream` is set, ovstream
// synchronizes on the whole stream instead. `wait_event` takes
// precedence when both are non-zero.
//
// Per-backend pipelining behaviour:
//   SHM      - chains the device-to-host memcpy on `wait_event`
//              via `cudaStreamWaitEvent`; no host block. If only
//              `stream` is set (no event), SHM falls back to
//              `cudaStreamSynchronize` on the caller's stream,
//              which DOES host-block; supply `wait_event` to keep
//              the call asynchronous.
//   RTSP /   - host-blocks on the event (or stream) before
//   WebRTC     handing the buffer to GStreamer / StreamSDK. The
//              caller still avoids a producer-side device-wide
//              sync, but the encoder itself doesn't get pipelined
//              against the next frame's render.
//--------------------------------------------------------------
typedef struct
{
    uintptr_t  stream;     // `cudaStream_t` / `CUstream` cast to `uintptr_t`; 0 = unknown.
    uintptr_t  wait_event; // `cudaEvent_t`  / `CUevent`  cast to `uintptr_t`; 0 = none.
} ovstream_cuda_sync_t;

//--------------------------------------------------------------
// @brief Video frame descriptor passed to `ovstream_stream_video`.
//
// How the fields are interpreted depends on the server's
// configured `video_input` (see `ovstream_video_input_t`):
//   CUDA              - `buffer` is a CUDA device pointer; set
//                       `width` / `height` / `pitch_bytes`;
//                       `size_bytes = 0`.
//   TENSOR            - `buffer` is a `DLTensor*` describing a
//                       CUDA-resident BGRA8 image; `width` /
//                       `height` / `pitch_bytes` / `size_bytes` are
//                       ignored (derived from the tensor).
//   CUSTOM / H264 /   - `buffer` is a host pointer to a pre-encoded
//   H265 / AV1          bitstream; set `size_bytes`;
//                       `pitch_bytes = 0`.
//
// Buffer lifetime contract:
//   `ovstream_stream_video` stages the frame data into server-owned
//   memory before returning. Concretely:
//     - SHM / CUDASHM copy the source into the ring slot and
//       `cudaStreamSynchronize` before returning.
//     - WebRTC / native push through StreamSDK which copies into its
//       internal encoder ring before `nvstPushStreamData` returns.
//     - RTSP raw-CUDA copies the source into a server-owned device
//       slot on a dedicated CUDA stream and synchronizes; RTSP
//       pre-encoded copies the bitstream bytes into a freshly
//       allocated GstBuffer.
//   As a consequence the caller may freely reuse or free `buffer`
//   (and any backing CUDA allocation, host allocation, or DLTensor
//   `data` pointer) immediately after `ovstream_stream_video`
//   returns. The `DLTensor` struct itself for TENSOR input only
//   needs to outlive the call. Stack buffers are legal too.
//--------------------------------------------------------------
typedef struct
{
    void*                buffer;             // CUDA device ptr (CUDA), DLTensor* (TENSOR), or host ptr (pre-encoded).
    uint32_t             width;              // Frame width in pixels (ignored for TENSOR).
    uint32_t             height;             // Frame height in pixels (ignored for TENSOR).
    uint32_t             pitch_bytes;        // Row pitch for CUDA (0 for pre-encoded; ignored for TENSOR).
    uint32_t             size_bytes;         // Buffer size for pre-encoded (0 for CUDA; ignored for TENSOR).
    uint64_t             start_timestamp_ns; // Optional capture start timestamp (ns, 0 = SDK picks).
    uint64_t             ended_timestamp_ns; // Optional capture end timestamp (ns, 0 = SDK picks).
    const void*          metadata;           // Optional per-frame metadata blob.
    uint32_t             metadata_size;      // Size of `metadata` in bytes.
    const uint8_t*       metadata_uuid;      // 16-byte UUID identifying the metadata type.
    ovstream_cuda_sync_t sync;               // Optional CUDA sync hint (0/0 = caller pre-synced).
} ovstream_video_frame_t;

//--------------------------------------------------------------
// @brief Audio frame descriptor passed to `ovstream_stream_audio`.
//
// Buffer contents:
//   CPU pointer to little-endian PCM samples, interleaved
//   across channels. Only 16-bit PCM is currently supported;
//   frames with `bits_per_sample != 16` are rejected by
//   `ovstream_stream_audio`.
//
// Buffer lifetime contract:
//   Same rule as `ovstream_video_frame_t` above: `ovstream_stream_audio`
//   stages the samples into server-owned memory before returning, so
//   the caller may reuse or free `buffer` (including stack buffers)
//   as soon as the call returns.
//--------------------------------------------------------------
typedef struct
{
    void*       buffer;          // PCM samples (little-endian, interleaved).
    uint32_t    size_bytes;      // Total size of the sample buffer.
    uint32_t    channels;        // 1 = mono, 2 = stereo, 6 = 5.1, 8 = 7.1, etc.
    uint32_t    sample_rate;     // Samples per second per channel (e.g. 48000).
    uint32_t    bits_per_sample; // Must be 16.
} ovstream_audio_frame_t;

//--------------------------------------------------------------
// Compile-time ABI checks. The public headers assume a 64-bit
// build (several structs contain void* pointers mixed with 32-bit
// fields; padding layout differs on 32-bit targets) and that enum
// types are sized the same as `int` (the default on all currently
// supported compilers). Both are true in every supported build
// configuration; the asserts turn a silent ABI break into an
// obvious compile error if someone later ports to a platform
// where they don't hold.
//--------------------------------------------------------------
#ifdef __cplusplus
static_assert(sizeof(void*) == 8, "OVSTREAM requires a 64-bit build.");
static_assert(sizeof(ovstream_string_t)      == 16,          "OVSTREAM string view ABI mismatch.");
static_assert(sizeof(ovstream_server_type_t) == sizeof(int), "OVSTREAM enum size mismatch.");
static_assert(sizeof(ovstream_log_level_t)   == sizeof(int), "OVSTREAM enum size mismatch.");
static_assert(sizeof(ovstream_video_input_t) == sizeof(int), "OVSTREAM enum size mismatch.");
static_assert(sizeof(ovstream_api_status_t)  == sizeof(int), "OVSTREAM enum size mismatch.");
static_assert(sizeof(ovstream_result_t)      == sizeof(int), "OVSTREAM result_t ABI mismatch.");
#endif

#ifdef __cplusplus
}
#endif
