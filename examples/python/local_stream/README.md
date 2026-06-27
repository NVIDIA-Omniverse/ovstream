# Local Stream (Python, SHM / CUDASHM producer + readers)

Same-machine zero-copy streaming. `main.py` runs an SHM producer that fills a 1280×720 CUDA buffer at 60 FPS and a colocated reader thread that attaches as `ovstream.Client(ovstream.ClientType.SHM, stream_name=…)` and reports observed frames.

Two OpenCV-based visual readers live in this directory:

- **`main_viewer.py`** — attaches to a host-resident **SHM** stream via `ovstream.Client(ovstream.ClientType.SHM, stream_name=…)`. Frame pixels arrive already on the host; OpenCV renders them directly.
- **`main_cudashm_viewer.py`** — attaches to a GPU-resident **CUDASHM** stream via `ovstream.Client(ovstream.ClientType.CUDASHM, stream_name=…)`. Frame pixels live in GPU memory; the viewer `cudaMemcpy2D` D2Hs each slot for display. A real consumer (sim kernel, GPU post-processing) would launch its own CUDA kernels against the imported device pointer directly and skip the host copy — the viewer's D2H is purely for screen display.

## Run the single-process demo

```bash
uv run main.py
```

Output (approximately):

```
[producer] streaming on 'local_stream'
Press Ctrl+C to stop.
[reader] attached to 'local_stream'
[producer] reader attached
[reader] received 60 frames (latest seq=60, 1280x720)
```

## Cross-process: producer in one shell, reader(s) in others

Shell 1 — start the producer (this script, no flags):

```bash
uv run main.py demo-stream
```

Shell 2 — attach a reader:

```bash
uv run main.py demo-stream --reader
```

Or attach the OpenCV viewer:

```bash
uv run main_viewer.py demo-stream
```

`uv run` resolves the viewer's `opencv-python` dependency from the PEP 723 inline metadata at the top of `main_viewer.py`. If you'd rather invoke with plain `python main_viewer.py`, install OpenCV yourself first: `pip install opencv-python`.

Multiple readers are supported — start as many shells as you like with the same stream name.

The C producer in [`../../c/local_stream`](../../c/local_stream/) interoperates with these Python readers; either side can play either role.

## CUDASHM: GPU-resident pixels

The CUDASHM transport keeps frames in GPU memory end-to-end. Run any of the pluggable examples (`basic_stream`, `warp_stream`, `ovrtx_stream`) with a `cudashm:<name>` spec to produce a CUDASHM stream, then attach `main_cudashm_viewer.py` in a separate shell:

Shell 1 — start a CUDASHM producer:

```bash
uv run ../basic_stream/main.py cudashm:demo-cudashm
```

Shell 2 — attach the CUDASHM viewer:

```bash
uv run main_cudashm_viewer.py demo-cudashm
```

> [!IMPORTANT]
> CUDASHM consumers **must run in a separate process** from the producer. CUDA forbids `cudaIpcOpenMemHandle` in the process that called `cudaIpcGetMemHandle`, so co-located producer-and-viewer in one Python script (the trick `main.py` plays for SHM) is not supported for CUDASHM.
>
> [!NOTE]
> The CUDASHM ring's per-slot buffers are GPU-resident, so the producer and viewer processes need to be on the **same host** (or in containers with `--ipc=host --gpus all` on the same host). Cross-machine clients should use WebRTC, RTSP, or native.
>
> [!NOTE]
> On a multi-GPU host the viewer must attach on the producer's GPU (CUDA IPC imports that device's memory). If the producer set a non-default `cuda_device`, pass the matching ordinal: `uv run main_cudashm_viewer.py demo-cudashm --device 1`. The viewer prints the producer's device after attaching, and a wrong device shows up as an attach error naming the right one.

## What it shows

- `ovstream.Server(ServerType.SHM)` producer-side setup with `ServerConfig.shm_stream_name` (and `ServerType.CUDASHM` + `cudashm_stream_name` for the GPU variant).
- `ovstream.Client(ovstream.ClientType.SHM, stream_name=…)` / `ovstream.Client(ovstream.ClientType.CUDASHM, stream_name=…)` consumer-side attach (the same `Client` also speaks `ClientType.NATIVE` for network streams).
- `client.wait_frame(timeout_ms=...)` blocking pull, with `is_alive()` watchdog.
- Multi-reader semantics: many readers can attach to one producer concurrently (true for both SHM and CUDASHM).
- `frame.as_numpy()` zero-copy view into the shared region for SHM (`frame.source` is `ClientType.SHM`); `frame.device_ptr` raw CUDA device pointer for CUDASHM (the viewer D2Hs it; a real GPU consumer wouldn't).
