# AGENTS.md - AI Agent Guide for ovstream

This file gives AI coding agents the minimum context needed to work effectively with the public-facing ovstream content. Use it as a starting map.

## What This Repo Is

`ovstream` is a pre-release NVIDIA SDK that exposes a thin C and Python API for live-streaming CUDA-rendered frames over WebRTC, RTSP, the low-latency native protocol, or local shared memory. The C library and Python wheel are distributed via GitHub Releases and `pip install ovstream`.

This repository is the public-facing source for documentation, runnable examples, and AI agent skills.

## Important: this is a generated mirror

The consumer-facing content in this repository — `README.md`, `CHANGELOG.md`, `THIRD_PARTY_NOTICES.md`, `examples/`, `skills/` — is **automatically mirrored** from an upstream source repository. **Do not hand-edit those files here.** Changes will be silently overwritten on the next sync.

Contributions to those files go to the internal upstream. External contributions are not currently accepted regardless — see [`CONTRIBUTING.md`](CONTRIBUTING.md).

The mirror runs as part of the internal upstream's CI on every commit that touches mirrored content. Idempotent: re-syncs at the same upstream commit are no-ops.

## Files maintained directly in this repo (NOT mirrored)

The following are owned by this repo and survive every sync:

- `LICENSE`
- `PRODUCT_TERMS_OMNIVERSE`
- `SECURITY.md`
- `CONTRIBUTING.md`
- `AGENTS.md` / `CLAUDE.md` (this file and its alias)
- `.github/` (CODEOWNERS, issue templates, PR template, any future workflows)
- `.gitignore`

Edit these directly when needed; the mirror never touches them.

## Start Here

- Read `README.md` for product context and quick starts.
- Read `examples/README.md` to choose a runnable reference project.
- Read `skills/README.md` to understand the skill format.

## Repo Layout

- `examples/python/` — Python example projects (`basic_stream`, `local_stream`, `warp_stream`, `ovrtx_stream`).
- `examples/c/` — C/C++ example projects (`basic_stream`, `starfield_stream`, `local_stream`, `pre_encoded_stream`).
- `examples/webrtc_client/` — drop-in browser client for the WebRTC transport.
- `skills/` — task-oriented agent skills (`*/SKILLS.md`).

## Common Workflows

### Python

- Use Python 3.8 or newer.
- Install the wheel: `pip install ovstream`
- Run example:
  - `cd examples/python/basic_stream`
  - `python main.py`

### C/C++ (CMake)

- Build example:
  - `cd examples/c/basic_stream`
  - `cmake -B build`
  - `cmake --build build --config Release`
- Run binary on Linux:
  - `./build/basic_stream`
- Run binary on Windows:
  - `.\build\Release\basic_stream.exe`

## API Documentation

The public C API is documented inline as Doxygen-style comments in the headers that ship inside the release zip (under `include/ovstream/`). The Python API is docstring-documented on the installed wheel — run `help(ovstream)` or `help(ovstream.Server)` after `pip install ovstream`.

There is no separately-maintained API reference document. The headers and the examples are the API documentation.

## Use Skills for Task-Specific Work

When a request maps to a known ovstream workflow, go directly to the relevant skill in `skills/`:

- C project bootstrapping -> `skills/project-setup-c/SKILLS.md`
- Python project bootstrapping -> `skills/project-setup-python/SKILLS.md`
- App-level lifecycle and ordering -> `skills/application-flow/SKILLS.md`
- Creating and configuring a server -> `skills/server-creation/SKILLS.md`
- Streaming frames (raw CUDA, pre-encoded) -> `skills/streaming-frames/SKILLS.md`
- Picking a transport (WebRTC/RTSP/native/SHM) -> `skills/protocol-selection/SKILLS.md`
- Connection / message / input callbacks -> `skills/callbacks-and-input/SKILLS.md`
- CUDA buffer interop and pitch alignment -> `skills/cuda-interop/SKILLS.md`
- Error/reporting patterns -> `skills/error-handling/SKILLS.md`
- Writing an SHM reader/consumer -> `skills/shm-consumers/SKILLS.md`

If multiple skills seem relevant, start with `skills/application-flow/SKILLS.md`, then layer in specialized skills.

## Agent Expectations

- For changes to `README.md`, `CHANGELOG.md`, `THIRD_PARTY_NOTICES.md`, `examples/`, `skills/`: edit upstream and let the sync land them here. Don't edit them here directly.
- For changes to the repo's own files (`LICENSE`, `SECURITY.md`, `CONTRIBUTING.md`, `.github/`, etc.): edit directly here.
- Snippet markers in examples (`# [snippet:name]` / `// [snippet:...]`) are referenced from skills via `> **Source:** ...` blockquotes. Preserve markers when modifying examples upstream.

## Notes

- The project is pre-release; behavior, APIs, and packaging details may evolve.
