# Native Client (Python, StreamSDK viewer)

A live viewer for an ovstream **NATIVE** (StreamSDK) stream. `main.py` connects to a running native server over the network, receives the encoded video, decodes it client-side via StreamSDK's NvStreamingMedia into host BGRA8 frames via `ovstream.Client(ovstream.ClientType.NATIVE, …)`, and renders them with OpenCV. Mouse and keyboard events from the window are forwarded back to the server, so a producer that registered an input callback sees the same events it would from any other client.

Unlike the SHM / CUDASHM viewers, this needs neither the same host nor the same GPU as the producer — only an NVIDIA GPU on **this** (the consumer) machine, which the client uses to decode. Producer and consumer can run on two different machines.

## Run

Shell 1 — start a native producer (any pluggable example serves `native`):

```bash
uv run ../basic_stream/main.py native
```

Shell 2 — attach the viewer:

```bash
uv run main.py                       # connect to 127.0.0.1:49100
uv run main.py 10.0.0.5              # connect to a remote server
uv run main.py 10.0.0.5 --signal-port 50000
```

`uv run` resolves the viewer's `opencv-python` dependency from the PEP 723 inline metadata at the top of `main.py`. To invoke with plain `python main.py`, install OpenCV yourself first: `pip install opencv-python`.

Press `q` or `Esc`, or close the window, to exit. `ovrtx_stream` and `warp_stream` also serve `native`, so the viewer can point at those producers too.

> [!NOTE]
> The native client decodes via StreamSDK's NvStreamingMedia (which uses NVDEC on NVIDIA GPUs), so the **consumer** machine needs an NVIDIA GPU + driver. This is the trade for lifting the same-host / same-GPU constraint of SHM / CUDASHM — the producer and viewer can be on different machines (the SHM / CUDASHM viewers in [`../local_stream`](../local_stream/) cannot).

## What it shows

- `ovstream.Client(ovstream.ClientType.NATIVE, server_ip=…, signal_port=…, stream_port=…)` consumer-side connect, with a retry loop so the producer / consumer launch order is forgiving.
- `client.wait_frame(timeout_ms=...)` blocking pull, with `is_alive()` watchdog to detect a dropped connection.
- `frame.as_numpy()` host BGRA8 view (the native client decodes to host memory; `frame.source` is `ClientType.NATIVE`).
- `client.send_input_event(...)` forwarding mouse / keyboard from the viewer back to the server — the same input path SHM / CUDASHM clients use.
- The same `ovstream.Client` also speaks `ClientType.SHM` / `ClientType.CUDASHM`; only the constructor config and which `Frame` member carries the pixels differ. See the `protocol-selection` skill.

For a minimal headless C consumer of the same stream, see the C sibling [`../../c/native_client`](../../c/native_client/).
