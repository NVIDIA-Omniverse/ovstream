# Local Stream (Python, SHM producer + reader)

Same-machine zero-copy streaming via the SHM transport. The `main.py` script runs a producer that fills a 1280×720 CUDA buffer at 60 FPS and a colocated reader thread that attaches as `ovstream.ShmClient` and reports observed frames. `main_viewer.py` is an OpenCV-based visual reader that opens a window and renders each incoming frame.

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

## What it shows

- `ovstream.Server(ServerType.SHM)` producer-side setup with `ServerConfig.shm_stream_name`.
- `ovstream.ShmClient(stream_name)` consumer-side attach.
- `client.wait_frame(timeout_ms=...)` blocking pull, with `is_producer_alive()` watchdog.
- Multi-reader semantics: many readers can attach to one producer concurrently.
- `frame.as_numpy()` zero-copy view into the shared region (used by `main_viewer.py`; the headless `main.py` consumer reads dataclass fields only).
