#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary
#
# NVIDIA CORPORATION, its affiliates and licensors retain all intellectual
# property and proprietary rights in and to this material, related
# documentation and any modifications thereto. Any use, reproduction,
# disclosure or distribution of this material and related documentation
# without an express license agreement from NVIDIA CORPORATION or
# its affiliates is strictly prohibited.

"""Save one streamed frame as a PNG: attach to the application's SHM stream and write
exactly what a client receives (BGRA8, converted to RGB). No image library needed.

    python main.py shm:snap            # in one shell
    python tools/snapshot.py snap out.png
"""

from __future__ import annotations

import struct
import sys
import zlib

import ovstream


def write_png(path: str, rgb_rows: list, width: int, height: int) -> None:
    raw = b"".join(b"\x00" + row for row in rgb_rows)

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")
        f.write(chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)))
        f.write(chunk(b"IDAT", zlib.compress(raw, 6)))
        f.write(chunk(b"IEND", b""))


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    name, out = sys.argv[1], sys.argv[2]
    ovstream.initialize()
    try:
        with ovstream.Client(ovstream.ClientType.SHM, stream_name=name) as client:
            frame = None
            for _ in range(20):
                frame = client.wait_frame(timeout_ms=1000)
                if frame is not None:
                    break
            if frame is None:
                print("no frame received", file=sys.stderr)
                return 1
            pixels = frame.as_numpy()[:, :frame.width, :]          # BGRA
            rgb = pixels[:, :, [2, 1, 0]]
            write_png(out, [rgb[y].tobytes() for y in range(frame.height)], frame.width, frame.height)
            print(f"wrote {out} ({frame.width}x{frame.height})")
    finally:
        ovstream.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
