---
name: project-setup-python
description: Setting up a new Python project that uses ovstream. Use when user asks to create a new Python project, set up ovstream in Python, create a pyproject.toml, or scaffold a streaming app.
---

# Project Setup (Python)

## Overview

ovstream is distributed as a self-contained, platform-tagged Python wheel on PyPI. The wheel bundles the native library (`ovstream.dll` / `libovstream.so`), GStreamer, the bundled `gstnvenc` plugin, and the CUDA runtime — no environment variable tweaks needed after install.

## Project Structure

```
my-streaming-app/
  pyproject.toml
  main.py
```

## Setup with uv (Recommended)

```bash
mkdir my-streaming-app && cd my-streaming-app
uv init
uv add ovstream
```

Then run:

```bash
uv run main.py
```

The resulting `pyproject.toml` looks like:

```toml
[project]
name = "my-streaming-app"
version = "0.1.0"
requires-python = ">=3.8"
dependencies = [
    "ovstream",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
```

## Setup with pip

```bash
pip install ovstream
python main.py
```

## Minimal main.py

> **Source:** `examples/python/basic_stream/main.py` snippet `initialize-sdk`
>
> Followed by: `examples/python/basic_stream/main.py` snippet `create-server`
>
> Followed by: `examples/python/basic_stream/main.py` snippet `stream-loop`
>
> Followed by: `examples/python/basic_stream/main.py` snippet `cleanup`

## Optional dependencies

| Package | Purpose |
|---------|---------|
| `ovstream` | The streaming SDK itself. |
| `opencv-python` | Required by [`examples/python/local_stream/main_viewer.py`](../../examples/python/local_stream/main_viewer.py) for an OpenCV-rendered SHM reader, and by [`examples/python/local_stream/main_cudashm_viewer.py`](../../examples/python/local_stream/main_cudashm_viewer.py) for the equivalent CUDASHM (GPU-resident) reader. |
| `numpy` | Required by `main_viewer.py` (zero-copy numpy view of SHM pixels) and `main_cudashm_viewer.py` (host buffer for the D2H copy used by display). |
| `warp-lang` | If you produce CUDA frames with [NVIDIA Warp](https://developer.nvidia.com/warp-python). |
| `ovrtx` | If you compose ovstream with the ovrtx renderer (see [`examples/python/ovrtx_stream`](../../examples/python/ovrtx_stream/)). |

## Companion package: ovstream_utils

The same wheel ships an optional companion module `ovstream_utils` with a small set of frame-pacing primitives:

```python
import ovstream_utils
with ovstream_utils.Loop(ovstream_utils.LoopConfig(fps_target=60)) as loop:
    while True:
        t = loop.tick()
        # ... render and stream ...
```

`import ovstream_utils` is optional — ovstream's API works on its own.

## Common Pitfalls

- ovstream requires Python 3.8 or newer. (ovrtx is more restrictive at 3.10–3.13; if your app composes both, follow ovrtx's range.)
- ovstream requires an NVIDIA GPU with a compatible driver. The bundled CUDA runtime needs the **driver-side** CUDA library (`libcuda.so.1` / `nvcuda.dll`) installed by the NVIDIA display driver.
- The wheel is platform-tagged; `pip install` automatically picks the matching `linux-x86_64`, `linux-aarch64`, or `windows-x86_64` build.
- If you see `ovstream.dll` / `libovstream.so` not found errors, the wheel install didn't complete — re-run `pip install ovstream`. Setting `OVSTREAM_LIB_PATH` is only needed when running from a source checkout without installing.
