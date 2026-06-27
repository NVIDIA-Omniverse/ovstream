# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary
#
# NVIDIA CORPORATION, its affiliates and licensors retain all intellectual
# property and proprietary rights in and to this material, related
# documentation and any modifications thereto. Any use, reproduction,
# disclosure or distribution of this material and related documentation
# without an express license agreement from NVIDIA CORPORATION or
# its affiliates is strictly prohibited.

cmake_minimum_required(VERSION 3.18)
project(native_client LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)

list(APPEND CMAKE_MODULE_PATH "${CMAKE_CURRENT_LIST_DIR}/../cmake")
include(ovstream)
ovstream_fetch()

# A pure consumer: link only the client library, never the server-side
# ovstream library. ovstream_setup_runtime stages ovstream_client and its
# StreamClientShared dependency alongside the binary.
add_executable(native_client main.cpp)
target_link_libraries(native_client PRIVATE ovstream::ovstream_client)
ovstream_setup_runtime(native_client)
