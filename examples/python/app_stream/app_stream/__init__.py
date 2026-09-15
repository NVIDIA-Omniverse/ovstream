# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary
#
# NVIDIA CORPORATION, its affiliates and licensors retain all intellectual
# property and proprietary rights in and to this material, related
# documentation and any modifications thereto. Any use, reproduction,
# disclosure or distribution of this material and related documentation
# without an express license agreement from NVIDIA CORPORATION or
# its affiliates is strictly prohibited.

"""app_stream: a low-latency streaming application composed from OV libraries.

Module map (dependencies point downward only):

    app          composition root and the per-tick frame path
    config       command-line options -> AppConfig
    capabilities startup probe and report
    scene        ovstage stage, RenderProduct authoring, camera writes
    camera       orbit camera math and input handling
    render       ovrtx renderer stepping and frame classification
    frames       CUDA rings, RGBA->BGRA swizzle, VideoFrame staging
    streaming    ovstream servers, callbacks, publishing
    input_bridge worker-thread event queue drained on the main thread
    pacing       ovstream_utils.Loop wrapper and rolling statistics
    contract     pure logic shared by the above; no GPU or library imports

Importing this package imports nothing heavy; the GPU libraries load when the
module that needs them is imported.
"""

__all__ = ["run"]


def run(argv=None):
    from .app import run as _run

    return _run(argv)
