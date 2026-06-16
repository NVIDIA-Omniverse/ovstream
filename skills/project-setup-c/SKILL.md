---
name: project-setup-c
description: Setting up a new CMake C/C++ project that uses ovstream. Use when user asks to create a new C project, set up CMake with ovstream, scaffold a C++ streaming app, or configure build dependencies.
---

# Project Setup (C)

## Overview

ovstream provides a C API with a CMake config for easy integration. The recommended approach uses CMake `FetchContent` (wrapped in `ovstream_fetch()`) to download the matching ovstream binary archive from GitHub Releases at configure time.

## Project Structure

```
my-streaming-app/
  CMakeLists.txt
  cmake/
    ovstream.cmake       # Copy from examples/c/cmake/ovstream.cmake
  main.cu                # or main.cpp if you don't need CUDA kernels
```

## CMakeLists.txt

```cmake
cmake_minimum_required(VERSION 3.18)
project(my-streaming-app LANGUAGES CXX CUDA)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# Fetch ovstream
list(APPEND CMAKE_MODULE_PATH "${CMAKE_CURRENT_LIST_DIR}/cmake")
include(ovstream)
ovstream_fetch()

add_executable(my-streaming-app main.cu)
target_link_libraries(my-streaming-app PRIVATE ovstream::ovstream)

# Configure runtime dependencies (rpath on Linux, DLL copying on Windows)
ovstream_setup_runtime(my-streaming-app)
```

## ovstream.cmake

Copy [`examples/c/cmake/ovstream.cmake`](../../examples/c/cmake/ovstream.cmake) into your project's `cmake/` directory. Two macros it provides:

- `ovstream_fetch()` — downloads the ovstream archive via FetchContent and makes the `ovstream::ovstream` imported target available.
- `ovstream_setup_runtime(TARGET)` — copies the bundled DLLs + `gst-plugins/` (Windows) or sets rpath (Linux) so the executable can find ovstream runtime dependencies.

Pin `OVSTREAM_VERSION` inside `ovstream.cmake` to the release you want. The macro derives the per-platform archive URL automatically:

```
https://github.com/NVIDIA-Omniverse/ovstream/releases/download/v<VERSION>/ovstream@<VERSION>.<PLATFORM>.zip
```

## Working against a local build

When iterating, point at an unzipped local package instead of downloading:

```bash
cmake -B build -DOVSTREAM_LOCAL_PACKAGE_DIR=/path/to/extracted/ovstream-package
```

## Minimal main.cu

> **Source:** `examples/c/basic_stream/main.cu` snippet `initialize-sdk`
>
> Followed by: `examples/c/basic_stream/main.cu` snippet `create-server`
>
> Followed by: `examples/c/basic_stream/main.cu` snippet `configure-server`
>
> Followed by: `examples/c/basic_stream/main.cu` snippet `start-server`
>
> Followed by: `examples/c/basic_stream/main.cu` snippet `stream-loop`
>
> Followed by: `examples/c/basic_stream/main.cu` snippet `cleanup`
>
> See the full `basic_stream` example for the complete flow.

## Build and Run

```bash
cmake -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build --config Release
./build/my-streaming-app           # Linux
.\build\Release\my-streaming-app.exe  # Windows
```

## Headers

| Header | Purpose |
|--------|---------|
| `<ovstream/ovstream.h>` | Main API: initialize/shutdown, create/destroy server, start/stop, stream frames, register callbacks. |
| `<ovstream/ovstream_types.h>` | All type definitions (handles, structs, enums, version macros). |
| `<ovstream/ovstream_shm_client.h>` | SHM consumer API (host-resident; writing readers, not producers). |
| `<ovstream/ovstream_cudashm_client.h>` | CUDASHM consumer API (GPU-resident; writing readers that import producer IPC handles via `cudaIpcOpenMemHandle`). |
| `<ovstream_utils/loop.hpp>` | Optional header-only frame pacer (`ovstream_utils::Loop`). |

## Common Pitfalls

- `ovstream_fetch()` requires CMake 3.18+ (CUDA language support and `FetchContent` semantics).
- `ovstream_setup_runtime()` must be called once per executable target — without it, the bundled DLLs / `gst-plugins/` won't be on the load path at runtime on Windows.
- The `OVSTREAM_VERSION` literal in `ovstream.cmake` must match the version you actually want; an out-of-sync pin yields 404 from the GitHub Release URL.
- ovstream needs a working CUDA driver at runtime (not bundled). The CUDA *runtime* (`cudart`) is bundled in the release archive.
- `ovstream_utils` ships as both a header-only C++ helper (`<ovstream_utils/loop.hpp>`, just include) and a small shared library for C consumers (`<ovstream_utils/loop.h>`, link `libovstream_utils.so` / `ovstream_utils.dll`). C++ users typically pick the header-only path; C users must link the shared library.
