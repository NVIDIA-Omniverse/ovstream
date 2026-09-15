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

# /// script
# requires-python = ">=3.12,<3.14"
# dependencies = [
#     "ovstream>=0.5",
#     "ovrtx>=0.5,<0.7",
#     "ovstage>=0.2",
#     "ovui>=0.2",
#     "warp-lang>=1.11",
#     "numpy",
# ]
# ///

"""Reference implementation: ovrtx + ovstage + ovui + ovstream, with DLSS Frame Generation.

Sets process environment before any CUDA or UI import, then hands over to the
app_stream package. `--no-ui` never imports ovui, so it runs where the ovui
wheel is unavailable.

    python main.py --report          print the capability report and exit
    python main.py                   render + control panel, stream over WebRTC
    python main.py --no-ui shm:demo  render only, on a named SHM stream
"""

import os
import sys

if sys.platform.startswith("linux"):
    # ovrtx guidance: CUDA stream waits that run concurrently with the
    # renderer's Vulkan submits cause scheduling bubbles on Linux unless
    # submissions are serialised. Must be set before the first CUDA context.
    os.environ.setdefault("CUDA_DEVICE_MAX_CONNECTIONS", "1")

from app_stream.app import run  # noqa: E402

if __name__ == "__main__":
    sys.exit(run(sys.argv[1:]))
