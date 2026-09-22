# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.5.2] - 2026-09-21
### Added
- WebRTC ICE Configuration: `transport_policy`, `remote_address` / `remote_port`, and per-server `turn_type`.
- Input: `OVSTREAM_MODIFIER_*` (Python `KeyboardModifier`) to name bits of the input event `modifiers` field.

### Changed
- `ovstream_webrtc_ice_server_t` and `ovstream_webrtc_ice_config_t` struct sizes; all consumers must rebuild.

## [0.5.1] - 2026-09-15
### Added
- WebRTC Features: `webrtc.audio_capture`, `webrtc.fit_client`, and `ovstream_set_signaling_headers_callback`.
- RTSP Tests: pre-encoded H.264/H.265, SEI metadata, caller timestamps, mount points, and describe-only clients.

## [0.5.0] - 2026-09-09
### Added
- Example/Reference Implementation: `app_stream` demonstrates how to compose ovrtx, ovstage, ovui, and ovstream with DLSS Frame Generation.
- Application Message Support: `ovstream_send_app_message` / `ovstream_set_app_message_callback` / `ovstream_utils.messaging.AppMessenger`.
- Clipboard/Cursor Support: `ovstream_send_clipboard` / `ovstream_set_clipboard_callback` / `ovstream_set_cursor`.
- Resize/QoS Support: `webrtc.resizable` / `ovstream_set_resize_request_callback` / `ovstream_set_qos_callback`.
- Skills: `composing-libraries`, `frame-generation-streaming`, `remote-input-to-ui`, `resizable-streams`.

### Changed
- `ovrtx_stream` example updated ovrtx dependency from 0.3 to 0.5 (adding ovstage).
- Raw message callback no longer receives stream control messages or app envelopes.
- StreamSDK updated to a newer 04.86 point release to pickup an expat security fix.
- Example WebRTC client updated to use the new `@nvidia/ov-web-rtc` 6.7.1 package.
- Bundled GStreamer updated from 1.24.12 to 1.28.6 and GLib from 2.87.0 to 2.88.0.
- Bundled CUDA runtime updated from 12.0 to 12.8 (the same version ovrtx bundles).

### Fixed
- GStreamer lib resolution when another library initializes GStreamer first.
- GStreamer lib resolution when `libovstream.so` is loaded through a symlink.
- RTSP concurrent DESCRIBE requests against a stream with no playing clients.

## [0.4.5] - 2026-07-03
### Fixed
- Audio stream initialization regression introduced by the last StreamSDK update.
- Crash when streaming video frames with buffers that cannot be resolved by CUDA.

## [0.4.4] - 2026-07-03
### Fixed
- Minor bugs raised by QA.

## [0.4.3] - 2026-07-02
### Changed
- Update to build with a newer version of StreamSDK 04.86 to pickup an OpenSSL fix.

## [0.4.2] - 2026-06-25
### Added
- Unified `ovstream/ovstream_client.h` client API.
- `ovstream_input_event_t` / `OVSTREAM_INPUT_TOUCH`.

### Changed
- `OVSTREAM_SHM_FORMAT_BGRA8` to `OVSTREAM_PIXEL_FORMAT_BGRA8`.

### Removed
- `ovstream_shm_client.h` / `ovstream_cudashm_client.h` client APIs.

## [0.4.1] - 2026-06-16
### Changed
- Bump to force publish.

## [0.4.0] - 2026-06-16
### Added
- `cudashm` backend implementation.
- `ovstream_webrtc_set_ice_servers`.
- `ovstream_server_config_t::cuda_device`.
- `ovstream_server_config_t::cuda_context`.
- `ovstream_cudashm_client_get_producer_device`.
- `ovstream.__version__` Python package attribute.
- `Server.destroy` Python alias for `Server.close()`.
- `tests/webrtc_net/` connection-topology test suite.
- `docs/WEBRTC_CONNECTIVITY.md` connectivity reference.
- `webrtc-connection-diagnostics` skill for troubleshooting.

### Changed
- `ovstream_stream_video` buffer lifetime contract
  to be consistent across backend implementations.

### Removed
- `ovstream_string_is_empty` from the public API.

## [0.3.0] - 2026-06-02
### Added
- ovstream pre-release.
