# Local Stream (C, SHM producer)

A minimal SHM (shared-memory) producer in C. Allocates a 1280×720 CUDA buffer, animates a BGRA8 gradient, and pushes frames into a named shared region for **same-machine** consumers. No network protocol involved — zero-encode, zero-network, `cudaMemcpy`-only path.

## Prerequisites

### Linux

```bash
sudo apt-get install build-essential cmake
```

### Windows

[Visual Studio 2019 or newer](https://visualstudio.microsoft.com/downloads/).

A working CUDA toolkit (for `nvcc`) is required on both platforms.

## Build

```bash
cmake -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build --config Release
```

## Run

Linux:

```bash
./build/local_stream             # default stream name "local_stream"
./build/local_stream my-stream   # explicit name
```

Windows:

```pwsh
.\build\Release\local_stream.exe
.\build\Release\local_stream.exe my-stream
```

## Attach a reader

The producer publishes frames into the named region but does nothing with them itself. Attach one or more readers from another process:

```bash
# Python frame-count reader:
python ../../python/local_stream/main.py local_stream --reader

# Python OpenCV viewer (requires `pip install opencv-python`):
python ../../python/local_stream/main_viewer.py local_stream
```

For a C / C++ consumer, see the `<ovstream/ovstream_client.h>` API (`ovstream_create_client(OVSTREAM_CLIENT_SHM, …)`, `ovstream_client_wait_frame`, `ovstream_client_release_frame`, `ovstream_client_is_alive`) and the `shm-consumers` skill.

> [!NOTE]
> This example is intentionally **SHM-only** (host-resident pixels). For the GPU-resident CUDASHM transport, use any of the pluggable examples with a `cudashm:<name>` spec instead — e.g. `./build/basic_stream cudashm:my-stream` from [`../basic_stream`](../basic_stream/). The CUDASHM consumer is `examples/python/local_stream/main_cudashm_viewer.py`; see the `cudashm-consumers` skill for the C client API (`<ovstream/ovstream_client.h>` with `OVSTREAM_CLIENT_CUDASHM`).

## What it shows

- `OVSTREAM_SERVER_SHM` server creation.
- `cfg.shm.stream_name` configuration (the only knob unique to the SHM transport).
- Connection callback firing when a reader attaches / detaches.
- Clean shutdown on Ctrl+C unlinks the shared-memory region. (A hard kill leaves the region behind; the next `ovstream_start` with the same stream name unlinks any stale region before recreating, so a crashed producer doesn't permanently wedge the name.)
