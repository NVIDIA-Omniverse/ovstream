# NVIDIA ovstream

ovstream is a lightweight C and Python SDK for live streaming Omniverse content, allowing developers to add real-time video streaming to their applications without depending on Kit or Carbonite.

ovstream is the streaming building block of the broader Omniverse libraries effort: pair it with [ovrtx](https://github.com/NVIDIA-Omniverse/ovrtx) (or any other CUDA-buffer producer) to ship interactive rendered content over WebRTC, RTSP, the low-latency native protocol, or local shared-memory in a few lines of code.

* [Get started in Python](#getting-started-in-python)
* [Get started in C](#getting-started-in-c)

> [!NOTE]
> ovstream is currently **pre-release** software.

## Features

* **Four transports in one library** — WebRTC (browser-friendly), RTSP (industry-standard), native (low-latency StreamSDK), and SHM (same-machine zero-copy). Pick one at runtime, run several simultaneously, no separate builds.
* **Zero-copy CUDA buffer streaming** — push raw BGRA8 frames directly from CUDA memory; the SDK encodes on the GPU via NVENC.
* **Pre-encoded passthrough** — stream existing H.264 / H.265 / AV1 bitstreams without re-encoding, for file replay or upstream-encoded sources.
* **Bidirectional messaging and input** (WebRTC / native / SHM) — receive keyboard, mouse, and Unicode events from connected clients; send messages back.
* **Self-contained** — no Kit, no Carbonite, no Omniverse app required. The wheel bundles its native dependencies (StreamSDK, GStreamer, the bundled `gstnvenc` plugin); `pip install ovstream` is enough.

## Getting Started in Python

Python 3.8 or newer is required.

Install the ovstream wheel, then fetch the example sources (they live in the public repo, not the wheel) and run the first example:

```bash
pip install ovstream
git clone https://github.com/NVIDIA-Omniverse/ovstream.git
cd ovstream/examples/python/basic_stream
python main.py
```

(Or download a release source archive from <https://github.com/NVIDIA-Omniverse/ovstream/releases> if you don't have `git`.)

The basic example creates a WebRTC server with signaling port 49100 (the media stream port defaults to 47998), allocates a CUDA buffer, animates a gradient fill, and pushes frames at 60 FPS. Open `examples/webrtc_client/index.html` in a browser and enter `127.0.0.1:49100` to view the stream.

Pass `rtsp`, `native`, or `shm` as arguments to switch transport — see the [examples README](examples/README.md) for the full menu.

## Getting Started in C

The C/C++ examples require CMake and a development environment. On Windows this is provided by [Visual Studio 2019 or newer](https://visualstudio.microsoft.com/). On Linux (Ubuntu):

```bash
sudo apt-get install build-essential cmake
```

Clone the public repo for the example sources (they're not in the wheel), then build and run the first example using CMake:

```bash
git clone https://github.com/NVIDIA-Omniverse/ovstream.git
cd ovstream/examples/c/basic_stream
cmake -B build -DCMAKE_BUILD_TYPE=Release
```

Then, on Windows:

```pwsh
cmake --build build --config Release
.\build\Release\basic_stream.exe
```

On Linux:

```bash
cmake --build build --config Release
./build/basic_stream
```

CMake's `ovstream_fetch()` macro downloads the matching ovstream binary archive from GitHub Releases at configure time. No manual install step is required.

## Examples

Further examples using both the C and Python APIs are available in the [examples](examples/README.md) directory. See the individual examples for building and usage instructions.

The Python `ovrtx_stream` example is the headline "composing two libraries into an app" demo — it loads a USD scene with [ovrtx](https://github.com/NVIDIA-Omniverse/ovrtx) and streams the rendered output through ovstream, end-to-end, with auto-orbiting camera and client input handling.

## Releases

The Releases page of this repository contains binary builds for the official releases of the ovstream C library and the corresponding Python wheels. These binaries are provided for the supported platforms:

 * Windows x86_64
 * Linux x86_64
 * Linux aarch64

The libraries require a compatible NVIDIA RTX-capable GPU with a compatible NVIDIA driver on the system. More detailed system requirements can be found at https://docs.omniverse.nvidia.com/dev-guide/latest/common/technical-requirements.html

## Documentation

The public C API is fully documented inline in the header files (`ovstream.h`, `ovstream_types.h`, `ovstream_shm_client.h`) shipped under `include/ovstream/` inside every release archive — each function carries its argument types, return semantics, thread-safety rules, and lifetime contracts as Doxygen-style comments. The Python API mirrors the C API and is documented via docstrings on the wheel-installed `ovstream` package (run `help(ovstream)` after `pip install ovstream`).

For runnable code, see [examples](examples/README.md). For AI-coding-agent-friendly task recipes, see [skills](skills/README.md).

## AI Coding Agents

The [skills](skills/README.md) directory contains a series of skills to help AI coding agents understand how to use the API (and they're useful for humans too). Copy this directory to your project and point your agent at it.

## Support

https://forums.developer.nvidia.com/c/omniverse/300

## Roadmap

To be announced.

## Contributing

At this time this project is not open to external contributions. Please use the [Support](#support) channels above for questions and bug reports.

## Authors and acknowledgment

NVIDIA Corporation.

## License

The software and materials are governed by the [NVIDIA Software License Agreement](https://www.nvidia.com/en-us/agreements/enterprise-software/nvidia-software-license-agreement/) and the [Product-Specific Terms for NVIDIA Omniverse](https://www.nvidia.com/en-us/agreements/enterprise-software/product-specific-terms-for-omniverse/).

This project will download and install additional third-party open source software projects — see [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md). Review the license terms of these open source projects before use.
