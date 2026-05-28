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
project(starfield_stream LANGUAGES CXX CUDA)

set(CMAKE_CXX_STANDARD 17)

list(APPEND CMAKE_MODULE_PATH "${CMAKE_CURRENT_LIST_DIR}/../cmake")
include(ovstream)
ovstream_fetch()

add_executable(starfield_stream main.cu)
target_link_libraries(starfield_stream PRIVATE ovstream::ovstream)
ovstream_setup_runtime(starfield_stream)

# main.cu uses the header-only ovstream_utils::Loop frame pacer from
# <ovstream_utils/loop.hpp>. That header ships in the same <pkg>/include
# root that ovstream::ovstream's INTERFACE_INCLUDE_DIRECTORIES already
# exposes, so no extra include or link step is needed here.

# Copy the audio sample into the same output directory as the executable.
add_custom_command(TARGET starfield_stream POST_BUILD
    COMMAND ${CMAKE_COMMAND} -E copy_if_different
        ${CMAKE_CURRENT_LIST_DIR}/data/audio_sample_48khz.pcm
        $<TARGET_FILE_DIR:starfield_stream>/audio_sample_48khz.pcm)
