# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary
#
# NVIDIA CORPORATION, its affiliates and licensors retain all intellectual
# property and proprietary rights in and to this material, related
# documentation and any modifications thereto. Any use, reproduction,
# disclosure or distribution of this material and related documentation
# without an express license agreement from NVIDIA CORPORATION or
# its affiliates is strictly prohibited.

"""`python -m app_stream` entry point."""

import os
import sys

if sys.platform.startswith("linux"):
    os.environ.setdefault("CUDA_DEVICE_MAX_CONNECTIONS", "1")

from .app import run  # noqa: E402

sys.exit(run(sys.argv[1:]))
