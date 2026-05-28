# WebRTC Browser Client

A drop-in HTML/JS client for the ovstream WebRTC transport. No build step, no npm — just open `index.html` in a browser.

## Usage

1. Start any ovstream example configured for WebRTC (the default for `basic_stream`, `starfield_stream`, `ovrtx_stream`, `warp_stream`).
2. Double-click `index.html` (or drag it into a browser tab).
3. Enter the server IP and signal port (defaults: `127.0.0.1:49100`).
4. Click **Connect**.

## What's bundled

- `index.html` — the client UI and signaling-handshake glue.
- `omniverse-webrtc-streaming-library.js` — vendored copy of the streaming client library that speaks the StreamSDK signaling flavor.

Off-the-shelf WebRTC tooling like `webrtc-cli` or a raw browser `RTCPeerConnection` will **not** interoperate with ovstream's WebRTC server — the client has to speak the StreamSDK signaling flavor that this library implements.

## Resolution

The stream resolution is whatever was passed to `ovstream_start` on the server side (1920×1080 for the bundled examples). Client-driven resize is not currently supported.
