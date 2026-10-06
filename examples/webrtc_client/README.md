# WebRTC Browser Client

A drop-in HTML/JS client for the ovstream WebRTC transport. No build step, no npm — just open `index.html` in a browser.

## Usage

1. Start any ovstream example configured for WebRTC (the default for `basic_stream`, `starfield_stream`, `ovrtx_stream`, `warp_stream`).
2. Double-click `index.html` (or drag it into a browser tab).
3. Enter the server IP and signal port (defaults: `127.0.0.1:49100`).
4. Click **Connect**.

## What's bundled

- `index.html` — the client UI and signaling-handshake glue.
- `ov-web-rtc.umd.cjs` — the UMD build of `@nvidia/ov-web-rtc` 6.7.1, vendored unmodified from the npm package (`dist/ov-web-rtc.umd.cjs`). It speaks the StreamSDK signaling flavor and exposes the library as `window.OVWebRTC`.

Off-the-shelf WebRTC tooling like `webrtc-cli` or a raw browser `RTCPeerConnection` will **not** interoperate with ovstream's WebRTC server — the client has to speak the StreamSDK signaling flavor that this library implements.

With this library version the client also applies cursor changes sent by the server (`set_cursor`) to the video element, reassembles multi-part application messages before delivering them to `onCustomEvent`, and splits large outbound application messages into parts (see the `app-messaging` skill).

### Updating the bundled library

```bash
npm pack @nvidia/ov-web-rtc@<version> --registry https://artifactory.nvidia.com/artifactory/api/npm/sw-pandora-npm-local/
tar xzf nvidia-ov-web-rtc-<version>.tgz
cp package/dist/ov-web-rtc.umd.cjs .
```

No patching is needed; update the version above and in `index.html` if the API used there changed.

## Headless client for automation

`playwright_client.py` drives this page in a headless Google Chrome: it serves the directory over a local HTTP server, connects to a loopback ovstream WebRTC server, and writes a JSON result (connected, video decoded, decoded size) plus the browser console log. Options cover application-message echo checks, `app_stream` state checks, resize requests, the frame marker (`--marker-capture`, `--marker-echo`), ICE/TURN settings and `--headed` for watching the run. It needs `pip install playwright` and `python -m playwright install chrome`; Playwright's bundled Chromium cannot decode H.264, so Google Chrome is required. The SDK's own tests and `app_stream/tools/compare.py` use it.

```bash
python playwright_client.py --signal-port 49100 --result-file result.json
```

## Resolution

The stream starts at whatever resolution was passed to `ovstream_start` on the server side (1920×1080 for the bundled examples).

**Fit to window** asks the server to re-render at the size of the visible stream area, and keeps doing so as the browser window resizes. The request is honored only when the server was started with `webrtc.resizable` and handles the resize-request callback — the config handshake reports this as `allow_dynamic_resize`. Otherwise the server logs a warning and drops the request, and the stream stays at its starting resolution. Of the bundled examples, `starfield_stream`, `warp_stream` and `app_stream --resizable` support it.
