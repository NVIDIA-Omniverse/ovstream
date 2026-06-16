<!--
SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: LicenseRef-NvidiaProprietary

NVIDIA CORPORATION, its affiliates and licensors retain all intellectual
property and proprietary rights in and to this material, related
documentation and any modifications thereto. Any use, reproduction,
disclosure or distribution of this material and related documentation
without an express license agreement from NVIDIA CORPORATION or
its affiliates is strictly prohibited.
-->
# WebRTC Connectivity Reference

This document is the ovstream SDK's self-contained reference for diagnosing
WebRTC/native connection behaviour across network topologies. It is the
human-readable companion to the machine-readable scenario catalog
(`tests/webrtc_net/scenarios.toml`) and the source of truth for the
`webrtc-connection-diagnostics` skill.

ovstream's WebRTC/native backend is built on NVIDIA StreamSDK (the same
streaming stack behind GeForce NOW). It uses StreamSDK's signaling and ICE,
**not** a standard browser-interoperable WebRTC stack — off-the-shelf WebRTC
tools will not interoperate with it. Connect with the bundled browser client
(`examples/webrtc_client/`) or the StreamSDK-based C++ client.

**A note on the automated probe vs. the browser:** the StreamSDK-based C++
client (`ovstream_webrtc_probe`, used by the automated tests) pairs with the
server's **native** backend (`ServerType.NATIVE`), which shares the *same*
StreamSDK signaling, ICE, and NAT/STUN/TURN transport as the WebRTC backend —
so the connectivity behaviour exercised below (NAT traversal, STUN/TURN relay,
firewall/UDP, container/cloud reachability) is representative of both. The
browser-specific WebRTC path (Chrome policies, `wss`, codec/autoplay) is
covered separately by the browser-client lane (`browser-client-decode`). Both
backends log the same `Client connected to WebRTC server` line.

Two transport pieces matter for every scenario:

- **Signaling port** — TCP (default `49100`, set via
  `ServerConfig.webrtc_signal_port`). The client first reaches this to
  negotiate the session.
- **Media/stream port** — UDP (default `47998`, set via
  `ServerConfig.stream_port`). Encoded video/audio flows here. WebRTC ICE may
  also use additional dynamic UDP ports beyond this one.

---

## Capturing logs for diagnosis

Every diagnosis below keys off log output. ovstream routes its own logs **and**
StreamSDK/GStreamer dependency logs through one callback you install at
`initialize()`.

**Python:**

```python
import ovstream
ovstream.initialize(
    log_fn=lambda level, channel, message, ts: print(f"[{level.name}][{channel}] {message}"),
    log_min_severity=ovstream.LogLevel.INFO,   # INFO surfaces the connect/disconnect lines
)
```

**C:**

```c
ovstream_init_config_t cfg = {};
cfg.log_callback = my_log_callback;     /* (severity, message, channel, timestamp, user_data) */
cfg.log_min_severity = OVSTREAM_LOG_INFO;
ovstream_initialize(&cfg);
```

Use `LogLevel.INFO` to see the connection lifecycle lines; drop to
`LogLevel.VERBOSE` for the full StreamSDK firehose when chasing a signaling or
ICE problem. `LogLevel.WARNING` (the default) hides the positive connect log.

> **Note:** ovstream's application-level logs are emitted by the SDK; the
> lower-level signaling/ICE strings (e.g. `Signaling handshake timedout`) come
> from StreamSDK and appear **on whichever process owns that StreamSDK
> instance** — the server for server-side issues, the client for client-side
> signaling timeouts. A client-side timeout will therefore not appear in the
> server log.

The `tests/webrtc_net/` connection probe (`ovstream_webrtc_probe`) is a
StreamSDK client you can drop into any reachable network location to confirm
whether a connection completes; see `tests/webrtc_net/README.md`.

---

## Topology scenario matrix

Each subsection corresponds 1:1 to an entry in `scenarios.toml`. "Expected
logs" lists the server-side strings the automated test asserts; "human-visible"
notes lines you may additionally see that are not auto-asserted.

### loopback-direct-connect

- **Topology:** Probe and server on `127.0.0.1`; no NAT, no STUN.
- **Expected:** connect. Server logs `Client connected to WebRTC server`.
- **Diagnosis:** Baseline happy path. If this fails, the problem is the server
  itself (signaling-port bind, StreamSDK init), not the network.
- **Fix:** Confirm the signal port is free and the server started without error
  before chasing any network topology.

### loopback-clean-disconnect

- **Topology:** Loopback connect, then the client disconnects.
- **Expected:** connect, then `Client connected to WebRTC server` followed by
  `Client disconnected from WebRTC server`.
- **Diagnosis:** Confirms the connection-state callback fires on both edges. A
  missing disconnect log points at a teardown-path regression.

### loopback-video-frame

- **Topology:** Loopback connect; the server streams a solid-color CUDA frame;
  the client waits for a decoded video frame. Requires a real NVENC-capable
  GPU (skipped on CPU-only hosts).
- **Expected:** connect. Server logs `Client connected to WebRTC server`; the
  client decodes at least one frame.
- **Diagnosis:** If connect succeeds but no frame decodes, suspect NVENC
  (`NV_ENC_ERR_UNSUPPORTED_DEVICE`) or a CUDA-device mismatch — see the encoder
  notes below.
- **Fix:** For a multi-GPU host, set `ServerConfig.cuda_device` to the GPU your
  frame buffers live on.

### signal-port-in-use

- **Topology:** Two servers configured on the same signal port.
- **Expected (platform-dependent):** Either the second `start()` fails — the
  log carries `webrtc: createAndStart failed`, and the StreamSDK lines usually
  include `Failed to start signaling server: Address already in use` — **or**
  the second server is transparently reassigned a different port. The automated
  test asserts the failure log when a failure occurs and skips otherwise.
- **Diagnosis:** Another process (or a leaked prior instance) holds the
  signaling port. If a client unexpectedly connects to the wrong server, a
  silent port reassignment is the likely cause.
- **Fix:** Free the port or set `ServerConfig.webrtc_signal_port` to an unused
  one.

### wrong-port-signaling-timeout

- **Topology:** Probe connects to a port where nothing is listening; it must
  report failure within its timeout rather than hang.
- **Expected:** fail (outcome only; no server-side log to assert).
- **Diagnosis:** Signaling never completes — nothing is listening, a firewall
  drops the SYN, or the wrong port was used. **Human-visible** (client side):
  `Signaling handshake timedout` and StreamerNoOffer `0xC0F22219`.
- **Fix:** Verify the signaling port (TCP) is reachable from the client and
  matches the server's `webrtc_signal_port`.

### stun-local-loopback

- **Topology:** Local coturn as a STUN server; the server points at it via
  `set_webrtc_ice_servers`; the probe connects over loopback.
- **Expected:** connect. Server logs `Client connected to WebRTC server`.
- **Diagnosis:** Smoke test that ICE-server configuration is wired and harmless
  on a network that needs no relay. The meaningful STUN/TURN assertions live in
  the NAT scenarios below.
- **Fix:** If connect fails *only* when STUN is configured, the STUN URL is
  malformed or unreachable.

> **All netns scenarios use a server-behind-NAT topology.** The automated probe
> is a *public* client (it needs no client-side ICE), so the simulated NAT sits
> in front of the **server**: `public client-netns (runs coturn/STUN) → NAT
> gateway (MASQUERADE; signaling TCP DNAT-forwarded) → server-netns`. This
> exercises the server's STUN/ICE candidate gathering and NAT traversal with a
> real ovstream server and the real `ovstream_webrtc_probe`.

> **How TURN is exercised — the browser, not the native probe.** The ovstream
> WebRTC server is an **ICE-lite** agent: it advertises only host candidates and
> never gathers server-reflexive or relay candidates (StreamSDK's native ICE has
> no relay candidate type — `NvstIceCandidateOptions` is host / STUN-reflexive /
> API-configured-public only). The server **forwards its configured TURN
> servers to the client**, and the full-ICE peer — a browser (Chrome) — does
> standard RFC-5766 TURN against **any** server, including external/self-hosted
> coturn (this is the production path; it is *not* NVIDIA-only). The TURN-relay
> scenarios below are therefore validated through the **browser lane**: the
> driver forces `iceTransportPolicy: "relay"` so media can only flow through
> coturn, and asserts a decoded frame over a `relay` candidate. (Chrome refuses
> to allocate a relay on a `127.0.0.1` TURN server, so the test uses a
> dummy-interface address — hence root is required.) The native
> `ovstream_webrtc_probe` cannot exercise relay and does not attempt it.

### nat-full-cone-stun

- **Topology:** Server behind a cone NAT (`MASQUERADE`); public client with a
  reachable coturn STUN server dials the NAT's public address.
- **Expected:** connect. Server logs `Client connected to WebRTC server`.
- **Diagnosis:** A cone NAT reuses one external mapping, so the server's
  STUN-learned reflexive (srflx) candidate is reachable by the public client and
  media flows.
- **Fix:** Ensure a STUN server is configured (`set_webrtc_ice_servers`) and
  reachable from the NAT'd server.

### nat-symmetric-no-turn

- **Topology:** Server behind a *symmetric* NAT (`MASQUERADE --random-fully`);
  public client with coturn STUN.
- **Expected:** connect. Server logs `Client connected to WebRTC server`.
- **Diagnosis:** Counter to the common belief that symmetric NAT always needs
  TURN: when **one peer is public** (here the client), the server-initiated
  hole-punch produces a peer-reflexive candidate the client can use, so the
  connection succeeds *without* TURN. The symmetric NAT's per-destination port
  remapping only defeats the *srflx* path; the direct hole-punch to the public
  peer still works. TURN becomes mandatory only when **both** peers are
  NAT/firewall-constrained so neither can be reached directly.
- **Fix:** None for a public client. For the both-sides-constrained case, use a
  TURN relay (next).

### nat-symmetric-with-turn

- **Topology:** Browser forced relay-only (`iceTransportPolicy: "relay"`)
  against a coturn TURN server over UDP — reproducing the condition (e.g. both
  peers behind symmetric NAT) where only a relay works. Exercised via the
  **browser lane** (see the note above).
- **Expected:** connect via a `relay` candidate; server logs `Client connected
  to WebRTC server`.
- **Diagnosis:** When neither peer is directly reachable, only a TURN relay
  works. The browser allocates the relay; the ICE-lite server forwards its TURN
  config to the browser. Any standard TURN server works (external/coturn) — this
  is the production path, not NVIDIA-only.
- **Fix:** Configure a reachable TURN server (`turn:` URL + credentials via
  `set_webrtc_ice_servers`); the server forwards it to the browser, which
  allocates the relay.

### blocked-udp-no-turn

- **Topology:** Server behind NAT with `iptables` dropping **all** UDP to/from
  the server side; STUN configured but unreachable over UDP.
- **Expected:** fail (outcome only).
- **Diagnosis:** WebRTC media is UDP. With UDP dropped, STUN cannot learn a
  reflexive candidate and direct media has no path, leaving no usable candidate
  pair. Signaling (TCP) still completes, so this presents as "connects but no
  media".
- **Fix:** Open the UDP media range, or use a TURN server offering a TCP/TLS
  transport (`turns:...?transport=tcp`) — next.

### blocked-udp-turn-tcp

- **Topology:** Browser forced relay-only against a coturn TURN server over TCP
  (`turn:...?transport=tcp`) — reproducing a UDP-hostile path where media must
  relay over TCP. Exercised via the **browser lane** (see the note above).
- **Expected:** connect via a `relay` candidate; server logs `Client connected
  to WebRTC server`.
- **Diagnosis:** When direct UDP is impossible, a TURN server reachable over
  TCP/TLS relays the media. The browser allocates the relay over TCP; the
  ICE-lite server forwards its TURN config to the browser.

### container-host-network

- **Topology:** Server in a `--gpus all --net=host` container; the native probe
  on the host connects over the shared host network. Validated via the docker
  lane.
- **Expected:** connect. Server logs `Client connected to WebRTC server`.
- **Diagnosis:** Host networking exposes the server's ports directly — the
  simplest container topology. Two requirements are easy to miss:
  **`NVIDIA_DRIVER_CAPABILITIES=all`** (the toolkit default `utility,compute`
  omits `video`, so NVENC's `libnvidia-encode.so` won't load —
  `NVST_DISCONN_SERVER_VIDEO_ENCODER_INIT_DLL_LOAD_FAILED`), and the image must
  carry the server's runtime deps (e.g. `libatomic1`).
- **Fix:** `docker run --gpus all -e NVIDIA_DRIVER_CAPABILITIES=all --net=host <image>`.

### container-bridge-portmap

- **Topology:** Server container on a bridge network; signal (TCP) **and** media
  (UDP) ports mapped to the host; client connects via the host. **Documented
  recipe, not auto-tested** — on a single host the host can reach the container
  IP directly, so forcing media through the published port isn't faithfully
  reproducible; run it cross-machine.
- **Expected:** connect. Server logs `Client connected to WebRTC server`.
- **Diagnosis:** Bridge networking requires publishing every signaling and
  media port, including the UDP media port — a common omission that yields a
  connect that signals fine but never delivers media.
- **Fix:** Publish both (`-p 49100:49100 -p 47998:47998/udp`); set
  `webrtc_public_ip` if the mapped host IP differs from what the server
  advertises.

### public-ip-no-ice

- **Topology:** Server with `webrtc_public_ip` pinned (ICE disabled), mirroring
  a cloud VM with a fixed public IP; the browser connects directly to the
  advertised endpoint. Validated via the browser lane (the native probe can't
  use an ICE-disabled pinned endpoint).
- **Expected:** connect. Server logs `Client connected to WebRTC server`; the
  server logs `WebRtcTransport configured with public endpoint: <ip>:<port>` and
  `Not creating ICE candidates since ICE is disabled`.
- **Diagnosis:** Setting `webrtc_public_ip` pins the advertised endpoint and
  disables ICE — the documented pattern for a cloud VM with a known public IP
  and an open media port.
- **Fix:** Set `webrtc_public_ip` to the reachable address and open the UDP
  media port in the cloud firewall/security group. No STUN/TURN is required for
  direct connectivity in this case.

### browser-client-decode

- **Topology:** A headless browser loads `examples/webrtc_client/index.html`
  against a loopback server and asserts a decoded video frame.
- **Expected:** connect. Server logs `Client connected to WebRTC server`.
- **Diagnosis:** Exercises the real browser/JS signaling path, not just the C++
  client. If the C++ probe connects but the browser does not, suspect a browser
  WebRTC policy (e.g. Chrome `WebRtcIPHandling=disable_non_proxied_udp`,
  client error `0xC0F2220F`) or a `wss`/mixed-content issue when served over
  HTTPS.

---

## StreamSDK hex error code reference

These codes appear in StreamSDK log output (captured via your `log_fn`). They
are protocol-level and identical regardless of how ovstream is embedded.

| Hex Code | Name | Meaning |
|----------|------|---------|
| `0x800b0000` | `NVST_R_GENERIC_ERROR` | Catch-all error — check surrounding logs for specifics |
| `0x800b001e` | `NVST_R_ERROR_UDP_RTP_SOURCE_OPEN_FAILED_NO_PORTS_AVAILABLE` | UDP port exhaustion (often from rapid restarts) |
| `0x800b1000` | `NVST_R_INVALID_STATE` | Component not in the correct state (e.g. sending a message before connected) |
| `0x800B1002` | `NVST_R_INTERNAL_ERROR` | Unexpected internal server error (encoder conflict, GPU issue, other) |
| `0x800E840C` | `NVST_DISCONN_SERVER_VIDEO_ENCODER_INIT_CUDA_ENCODE_OPEN_FAILED` | NVENC initialization failed |
| `0x8003000F` | `NVST_DISCONN_SERVER_TERMINATED_FRAME_GRAB_FAILED` | Frame timeout — no frame delivered within the timeout |
| `0xC0F22219` | `StreamerNoOffer` (client-side) | Client never received the WebRTC offer — signaling timeout |
| `0xC0F2220F` | Ragnarok connection error (client-side) | Check browser WebRTC policy (`WebRtcIPHandling`) |

## Key log messages

| Log message | Meaning |
|-------------|---------|
| `Client connected to WebRTC server` | Successful connection (server side, INFO) |
| `Client disconnected from WebRTC server` | Client disconnected (server side, INFO) |
| `webrtc: createAndStart failed` | Server failed to start — usually a port bind failure |
| `Failed to start signaling server: Address already in use` | Signaling port already held by another process |
| `Signaling handshake timedout` | Connection failed during negotiation (often client side) |
| `NV_ENC_ERR_UNSUPPORTED_DEVICE` | GPU does not support NVENC |
| `JSON Exception: invalid UTF-8 character` | Historical StreamSDK signaling bug — update StreamSDK |

---

## Multi-machine manual procedures

Some topologies cannot be faithfully simulated on a single host. Run these
across separate machines and capture the server log (`log_fn`) plus the
`ovstream_webrtc_probe` output on the client side.

### True public-internet / carrier-grade NAT

1. Run the server on a host with a routable address (cloud VM or port-forwarded
   home server). Set `webrtc_public_ip` to its public IP and open the UDP media
   port.
2. From a client on a *different* physical network (e.g. a phone hotspot), run
   the probe (or load `examples/webrtc_client/`) against the public IP and
   signal port.
3. If direct fails, add a publicly reachable STUN/TURN server via
   `set_webrtc_ice_servers` and retry. CGNAT generally behaves like symmetric
   NAT and requires TURN.

### Real cloud / Kubernetes ingress

1. Deploy the server in the pod/container with the signal (TCP) and media (UDP)
   ports exposed through the ingress/NodePort/load balancer.
2. Verify the advertised address (`webrtc_public_ip`) matches what clients can
   actually reach through the ingress, not the pod-internal IP.
3. Connect from outside the cluster. A connect that signals but delivers no
   media almost always means the UDP media path is not exposed end-to-end.

### Cross-physical-machine on the same LAN

1. Server on machine A, client on machine B, same subnet. Use A's LAN IP (not
   `127.0.0.1`) for the probe's `--host`.
2. If this fails while loopback on A succeeds, a host firewall on A is blocking
   the signal or media port — open both (TCP signal, UDP media).

### TURN relay (browser/WebRTC client)

The TURN-relay scenarios (`nat-symmetric-with-turn`, `blocked-udp-turn-tcp`)
cannot be exercised with the native `ovstream_webrtc_probe`: StreamSDK's native
ICE stack has no relay candidate type (`NvstIceCandidateOptions` is host /
STUN-reflexive / API-configured-public only). The relay is gathered by the
**WebRTC client's** ICE stack — a browser does standard RFC-5766 TURN against
any server, **including self-hosted/external ones**. To validate a relay path:

1. Stand up a standard TURN server reachable by the client (e.g. coturn with
   long-term credentials, or your production TURN). For the UDP-blocked case,
   ensure it offers a TCP/TLS transport (`turns:...?transport=tcp`).
2. Configure the server's ICE with a matching `stun:` + `turn:` entry via
   `set_webrtc_ice_servers([WebRTCIceServer(urls="turn:<host>:<port>", username=..., credential=...)])`.
   (Pair a `stun:` entry with the `turn:` entry — a TURN-only config leaves
   StreamSDK's NATT with zero STUN servers and it gathers no candidates.)
3. Drive the connection from a **browser** client (`examples/webrtc_client/` or
   the Playwright lane), not the native probe. To force the relay path rather
   than direct/srflx, place the peers so neither is directly reachable — both
   behind symmetric NAT, or with UDP blocked end-to-end so only the TURN
   server's TCP/TLS transport survives (browser-side, `iceTransportPolicy:
   "relay"` forces relay-only).
4. Capture the server log. A successful relayed connection still logs `Client
   connected to WebRTC server`.

### IPv6 / IPv4 stack mismatch

If you see client-side socket errors like `WSAEAFNOSUPPORT (10047)` during NAT
hole-punch, the peers disagree on address family. Force a single stack
(disable IPv6 if unused) on both ends and retry.
