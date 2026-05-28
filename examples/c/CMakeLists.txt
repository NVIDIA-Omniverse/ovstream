# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary
#
# NVIDIA CORPORATION, its affiliates and licensors retain all intellectual
# property and proprietary rights in and to this material, related
# documentation and any modifications thereto. Any use, reproduction,
# disclosure or distribution of this material and related documentation
# without an express license agreement from NVIDIA CORPORATION or
# its affiliates is strictly prohibited.

# examples/c/CMakeLists.txt - top level for all C examples.
cmake_minimum_required(VERSION 3.18)
project(ovstream_examples LANGUAGES CXX CUDA)

# Enforce a consistent C++ standard across all examples rather than
# relying on each sub-project to set it individually.
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

add_subdirectory(basic_stream)
add_subdirectory(local_stream)
add_subdirectory(starfield_stream)
add_subdirectory(pre_encoded_stream)
