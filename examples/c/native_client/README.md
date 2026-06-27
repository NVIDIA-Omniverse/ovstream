# Native Client (C, StreamSDK consumer)

A minimal native (StreamSDK) **consumer** in C. Connects to a running ovstream NATIVE server over the network, receives the encoded video stream, decodes it client-side via StreamSDK's NvStreamingMedia into host BGRA8 frames, reports each frame's dimensions / sequence, and writes the first frame to `native_frame.ppm` so the result can be eyeballed.

Unlike the SHM / CUDASHM consumers, this needs neither the same host nor the same GPU as the producer — only an NVIDIA GPU on **this** (the consumer) machine, which the client uses to decode. Producer and consumer can run on two different machines.

This is a **pure consumer**: it links only `ovstream::ovstream_client` and includes only `<ovstream/ovstream_client.h>` — never the server-side ovstream library.

## Prerequisites

### Linux

```bash
sudo apt-get install build-essential cmake
```

### Windows

[Visual Studio 2019 or newer](https://visualstudio.microsoft.com/downloads/).

An NVIDIA GPU + driver is required on the consumer machine for the NvStreamingMedia decode (NVCodec uses NVDEC on NVIDIA GPUs).

## Build

```bash
cmake -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build --config Release
```

## Run

Start a native server first, in another shell:

```bash
python ../../python/basic_stream/main.py native
```

Then run the client:

Linux:

```bash
./build/native_client                  # connect to 127.0.0.1:49100
./build/native_client 10.0.0.5         # connect to a remote server
./build/native_client 10.0.0.5 --frames 200
```

Windows:

```pwsh
.\build\Release\native_client.exe
.\build\Release\native_client.exe 10.0.0.5
```

Options: `--signal-port <p>` (default 49100), `--stream-port <p>` (default 47999), `--frames <n>` (0 = run until Ctrl+C or the server stops). Press Ctrl+C to exit.

For a full viewer with live display and mouse / keyboard forwarding, see the Python sibling [`../../python/native_client`](../../python/native_client/).

## What it shows

- `ovstream_create_client(OVSTREAM_CLIENT_NATIVE, …)` consumer-side connect, with `cfg.native.server_ip` / `signal_port` / `stream_port` / `cuda_device`.
- A retry-on-connect loop so the producer / consumer launch order is forgiving (`nvstConnectToServer` fails synchronously when the server isn't yet reachable).
- `ovstream_client_wait_frame(client, timeout_ms, …)` blocking pull, distinguishing `OVSTREAM_API_TIMEOUT` (no frame yet) from `OVSTREAM_API_INVALID_STATE` (connection dropped), with `ovstream_client_is_alive` as the watchdog.
- `frame.data` host BGRA8 pixels (the native client decodes to host memory; `frame.source` is `OVSTREAM_CLIENT_NATIVE`), `frame.pitch_bytes` row stride, `frame.sequence` monotonic counter.
- `ovstream_destroy_client` teardown (disconnects the StreamSDK session and tears down the decoder).
- The same `ovstream_client_*` surface also drives `OVSTREAM_CLIENT_SHM` / `OVSTREAM_CLIENT_CUDASHM` — only the config sub-struct and which frame member carries the pixels differ. See the `protocol-selection` skill.
