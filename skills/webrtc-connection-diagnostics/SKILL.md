---
name: webrtc-connection-diagnostics
description: Diagnose ovstream WebRTC/native connection failures from logs. Use when a client can't connect, the stream is black, a connection times out, or you need to reason about STUN/TURN/ICE/NAT/firewall/container/cloud topology, signaling timeouts, or "what does this StreamSDK error code mean".
---

# WebRTC Connection Diagnostics

## Overview

ovstream's WebRTC/native backend uses NVIDIA StreamSDK signaling and ICE (not
browser-interoperable WebRTC). Most connection failures are network-topology or
configuration problems, not SDK bugs. This skill turns captured logs into a
diagnosis by matching them against the scenario catalog.

The authoritative reference is **`docs/WEBRTC_CONNECTIVITY.md`** — it carries
the full scenario matrix, the StreamSDK hex-error table, the key-log-message
table, and the multi-machine procedures. The machine-readable catalog is
**`tests/webrtc_net/scenarios.toml`** (each entry's `id`, `expect_logs`,
`diagnosis`, `fix`). Read both when diagnosing; this skill is the workflow that
ties them together.

## Step 1 — capture logs at the right level

Connection lifecycle lines (`Client connected to WebRTC server`) are logged at
INFO. The default severity (WARNING) hides them, so raise it on the **server**
side:

```python
ovstream.initialize(
    log_fn=lambda level, channel, message, ts: print(f"[{level.name}][{channel}] {message}"),
    log_min_severity=ovstream.LogLevel.INFO,   # or VERBOSE to chase signaling/ICE
)
```

> **Source:** `examples/python/basic_stream/main.py` snippet `initialize-sdk`

The callback receives ovstream's own logs **plus** StreamSDK/GStreamer
dependency output, so signaling and ICE detail flow through it. Drop to
`LogLevel.VERBOSE` when a connect never completes.

Key fact for diagnosis: **client-side signaling timeouts surface in the
client's StreamSDK log, not the server's.** A server that logs nothing while a
client reports a timeout means the client never reached the server — look at
reachability (signal port, firewall), not the server.

## Step 2 — match the log signature

Find the line(s) you have in the table below (a condensed view of the doc's
"Key log messages" + "hex error code" tables) and jump to the named scenario in
`docs/WEBRTC_CONNECTIVITY.md` for the full diagnosis and fix.

| Observed (server unless noted) | Scenario id | What it means → fix |
|--------------------------------|-------------|---------------------|
| `Client connected to WebRTC server` | `loopback-direct-connect` | Healthy connect. If the stream is still black, it's media/encode, not signaling — see `loopback-video-frame`. |
| `Client connected` then `Client disconnected from WebRTC server` | `loopback-clean-disconnect` | Normal teardown (or an early drop if it disconnects immediately). |
| `webrtc: createAndStart failed` / `Failed to start signaling server: Address already in use` | `signal-port-in-use` | Signal port held by another process → free it or change `webrtc_signal_port`. |
| (client) `Signaling handshake timedout` / StreamerNoOffer `0xC0F22219`, server silent | `wrong-port-signaling-timeout` | Client never reached the server → check signal-port reachability/firewall/port match. |
| Connect succeeds, no media, behind NAT | `nat-full-cone-stun` / `nat-symmetric-no-turn` | Configure a reachable STUN server (`set_webrtc_ice_servers`). With one public peer, STUN/direct usually suffices even through symmetric NAT; TURN is needed only when *both* peers are NAT/firewall-constrained. |
| Connect succeeds, no media, UDP blocked | `blocked-udp-no-turn` | Firewall drops UDP media → open the UDP media port or relay via TURN-over-TCP (`blocked-udp-turn-tcp`; the browser client relays through any standard TURN server). |
| Bridge container, signals but no media | `container-bridge-portmap` | UDP media port not published → publish it (`-p 47998:47998/udp`). |
| (client) Ragnarok `0xC0F2220F` | `browser-client-decode` | Browser WebRTC policy (e.g. `WebRtcIPHandling=disable_non_proxied_udp`). |
| `NV_ENC_ERR_UNSUPPORTED_DEVICE` / `0x800E840C` | `loopback-video-frame` | GPU/NVENC can't encode → check GPU, or `cuda_device` on multi-GPU hosts. |

For any hex code not above, see the full StreamSDK hex-error table in
`docs/WEBRTC_CONNECTIVITY.md`.

## Step 3 — reproduce to confirm (SDK source tree)

If you have the SDK source tree (not just the installed wheel), use the
connection probe and harness under `tests/webrtc_net/` to reproduce the
suspected scenario in isolation:

- Start a server: `python tests/webrtc_net/server_harness.py --signal-port 19100 --stream-port 17999 --log-file /tmp/srv.log`
- Probe it: `ovstream_webrtc_probe --host <ip> --signal-port 19100 --expect connect`
  (add `--wait-frame` to confirm media decodes; `--expect fail` to assert a
  failure path).
- The full topology matrix is driven by `pytest tests/webrtc_net/test_connectivity.py -v`;
  scenarios whose capabilities (gpu / coturn / netns / docker) aren't present
  on the host report **skipped**, not failed.

The probe runs as a plain process, so to test reachability across a real
network, run it from the client machine/namespace/container against the
server's address. See `tests/webrtc_net/README.md`.

## Step 4 — topologies you can't simulate locally

For true public-internet / CGNAT, real cloud/Kubernetes ingress,
cross-physical-machine, or IPv6/IPv4-stack-mismatch cases, follow the
**multi-machine manual procedures** at the end of
`docs/WEBRTC_CONNECTIVITY.md`.

## Common Pitfalls

- **Diagnosing from the wrong log.** A client-side signaling timeout will not
  appear in the server log. Capture both ends.
- **WARNING-level logging hides the connect line.** Raise the server to INFO
  before concluding "nothing connected".
- **"Connected but black" is not a signaling problem.** A successful
  `Client connected to WebRTC server` rules out signaling/ICE; look at NVENC,
  the CUDA device, or (for browsers) autoplay/codec.
- **Bridge networking drops media silently.** Signaling over TCP succeeds while
  UDP media is unpublished — the connection looks up but never renders.
- **Symmetric NAT does not always need TURN.** When one peer is public, the
  hole-punch to it still works through a symmetric NAT. TURN is required only
  when *both* peers are NAT/firewall-constrained.
- **TURN relay is a browser/WebRTC-client capability.** StreamSDK's native ICE
  stack gathers no relay candidates, so the native `ovstream_webrtc_probe`
  cannot use TURN — but a browser client does standard TURN against any server
  (external ones included). Diagnose relay problems with the browser client.
