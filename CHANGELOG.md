# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.4.2] - 2026-06-25
### Added
- Unified `ovstream/ovstream_client.h` client API
- `ovstream_input_event_t` / `OVSTREAM_INPUT_TOUCH`

### Changed
- `OVSTREAM_SHM_FORMAT_BGRA8` to `OVSTREAM_PIXEL_FORMAT_BGRA8`

### Removed
- `ovstream_shm_client.h` / `ovstream_cudashm_client.h` client APIs

## [0.4.1] - 2026-06-16
### Changed
- Bump to force publish

## [0.4.0] - 2026-06-16
### Added
- `cudashm` backend implementation
- `ovstream_webrtc_set_ice_servers`
- `ovstream_server_config_t::cuda_device`
- `ovstream_server_config_t::cuda_context`
- `ovstream_cudashm_client_get_producer_device`
- `ovstream.__version__` Python package attribute
- `Server.destroy` Python alias for `Server.close()`
- `tests/webrtc_net/` connection-topology test suite
- `docs/WEBRTC_CONNECTIVITY.md` connectivity reference
- `webrtc-connection-diagnostics` skill for troubleshooting

### Changed
- `ovstream_stream_video` buffer lifetime contract
  to be consistent across backend implementations.

### Removed
- `ovstream_string_is_empty` from the public API.

## [0.3.0] - 2026-06-02
### Added
- ovstream pre-release.
