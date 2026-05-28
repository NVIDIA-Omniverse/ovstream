# ovstream Examples

This directory contains example projects demonstrating various features of ovstream.

## C Examples

<table>
  <tr>
    <td align="center" width="50%">
      <a href="c/basic_stream/"><b>Basic Stream</b></a>
      <br>
      <sub>Animated CUDA gradient streamed via WebRTC, RTSP, native, SHM, or any combination. The canonical "hello world".</sub>
    </td>
    <td align="center" width="50%">
      <a href="c/pre_encoded_stream/"><b>Pre-Encoded Stream</b></a>
      <br>
      <sub>Streams an already-encoded H.264 bitstream through ovstream's pre-encoded passthrough path. No CUDA, no NVENC — exercises the <code>size_bytes</code> code path.</sub>
    </td>
  </tr>
  <tr>
    <td align="center" width="50%">
      <a href="c/local_stream/"><b>Local Stream (SHM)</b></a>
      <br>
      <sub>SHM-only producer for same-machine, zero-network, zero-encode consumers. Pairs with the Python SHM viewer.</sub>
    </td>
    <td align="center" width="50%">
      <a href="c/starfield_stream/"><b>Starfield Stream</b></a>
      <br>
      <sub><b>Headline composition demo.</b> CUDA-animated starfield plus looping audio, mouse-input-driven avoidance, and the optional <code>ovstream_utils::Loop</code> frame pacer.</sub>
    </td>
  </tr>
</table>

## Python Examples

<table>
  <tr>
    <td align="center" width="50%">
      <a href="python/basic_stream/"><b>Basic Stream</b></a>
      <br>
      <sub>Animated CUDA fill via ctypes-bound cudart, streamed via any transport. The Python "hello world".</sub>
    </td>
    <td align="center" width="50%">
      <a href="python/warp_stream/"><b>Warp Stream</b></a>
      <br>
      <sub>NVIDIA Warp kernel produces the per-frame fill; <code>VideoFrame.from_dlpack</code> hands the buffer to ovstream zero-copy via DLPack + <code>VideoInput.TENSOR</code>.</sub>
    </td>
  </tr>
  <tr>
    <td align="center" width="50%">
      <a href="python/local_stream/"><b>Local Stream (SHM)</b></a>
      <br>
      <sub>SHM producer + reader pair in one script, with a separate OpenCV-rendered visual reader (<code>main_viewer.py</code>).</sub>
    </td>
    <td align="center" width="50%">
      <a href="python/ovrtx_stream/"><b>ovrtx + ovstream</b></a>
      <br>
      <sub><b>Headline composition demo.</b> Load a USD scene with <a href="https://github.com/NVIDIA-Omniverse/ovrtx">ovrtx</a>, render it, and stream the result through ovstream.</sub>
    </td>
  </tr>
</table>

## Web Client

[`webrtc_client/`](webrtc_client/) — drop-in browser client for the WebRTC transport. No build step, no npm; just open `index.html`.

## Protocol selection (CLI)

`basic_stream`, `starfield_stream`, `warp_stream`, and `ovrtx_stream` accept zero or more positional args of the form `<protocol>[:port-or-name]`. Multiple args spin up multiple servers off the same frame producer.

| Protocol | Default | Example arg |
|---|---|---|
| `webrtc` | signal port `49100` | `webrtc`, `webrtc:49200` |
| `native` | signal port `49100` | `native`, `native:49200` |
| `rtsp`   | port `8554`, mount `/stream` | `rtsp`, `rtsp:9000` |
| `shm`    | example-defined segment name | `shm`, `shm:demo` |

```bash
# Single transport
python python/basic_stream/main.py rtsp

# Two transports off the same producer
python python/basic_stream/main.py webrtc rtsp

# Three transports, named SHM segment
python python/warp_stream/main.py webrtc rtsp shm:demo
```

`pre_encoded_stream` (RTSP only) and `local_stream` (SHM only) are transport-specific by design and use their own positional-arg shapes — see each example's README.
