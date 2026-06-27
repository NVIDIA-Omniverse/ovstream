# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary
#
# NVIDIA CORPORATION, its affiliates and licensors retain all intellectual
# property and proprietary rights in and to this material, related
# documentation and any modifications thereto. Any use, reproduction,
# disclosure or distribution of this material and related documentation
# without an express license agreement from NVIDIA CORPORATION or
# its affiliates is strictly prohibited.
#
# ovstream.cmake -- Fetch and configure the ovstream library.
#
# Usage:
#   list(APPEND CMAKE_MODULE_PATH "${CMAKE_CURRENT_LIST_DIR}/../cmake")
#   include(ovstream)
#   ovstream_fetch()
#
#   add_executable(myapp main.cpp)
#   target_link_libraries(myapp PRIVATE ovstream::ovstream)
#   ovstream_setup_runtime(myapp)
#
# By default `ovstream_fetch()` downloads a pre-built ovstream zip from
# the matching GitHub Release. Override one of the following at configure
# time if you need to point at a different URL or a local build:
#
#   -DOVSTREAM_PACKAGE_URL_OVERRIDE=<http(s) URL to ovstream-*.zip>
#       Pass a direct URL to a pre-built ovstream zip. Useful when
#       testing an un-published build uploaded to a personal location.
#
#   -DOVSTREAM_PACKAGE_URL_BASE=<base URL>
#       Override only the base URL (path up to but not including the
#       per-platform archive filename). Useful for staging repos.
#
#   -DOVSTREAM_LOCAL_PACKAGE_DIR=<path to an already-extracted ovstream-*.zip>
#       Skip the download entirely and use a local extracted package.
#       Useful for developers iterating against a local build.

set(_OVSTREAM_CMAKE_DIR "${CMAKE_CURRENT_LIST_DIR}")

# This default must match OVSTREAM_VERSION_MAJOR.MINOR.PATCH in
# include/ovstream/ovstream_types.h. This file is a bootstrap -- it runs
# before the ovstream package has been fetched, so it can't read the
# header. Update this literal at release time alongside the integer
# macros; `scripts/package.py` enforces the match at build time.
set(OVSTREAM_VERSION "0.4.2" CACHE STRING "ovstream version to fetch")

# Default download base URL. ovstream archives are attached to the
# GitHub Release at github.com/NVIDIA-Omniverse/ovstream/releases/tag/v<ver>.
# The per-platform archive URL is formed as:
#   <base>/ovstream@<version>.<platform>.zip
set(OVSTREAM_PACKAGE_URL_BASE
    "https://github.com/NVIDIA-Omniverse/ovstream/releases/download/v${OVSTREAM_VERSION}"
    CACHE STRING "Base URL for ovstream package downloads")

# Detect the platform tag used by ovstream's release packaging.
function(_ovstream_detect_platform OUT_VAR)
    if(CMAKE_SYSTEM_NAME STREQUAL "Windows")
        set(${OUT_VAR} "windows-x86_64" PARENT_SCOPE)
    elseif(CMAKE_SYSTEM_NAME STREQUAL "Linux")
        if(CMAKE_SYSTEM_PROCESSOR STREQUAL "aarch64"
           OR CMAKE_SYSTEM_PROCESSOR STREQUAL "arm64")
            set(${OUT_VAR} "linux-aarch64" PARENT_SCOPE)
        else()
            set(${OUT_VAR} "linux-x86_64" PARENT_SCOPE)
        endif()
    else()
        message(FATAL_ERROR
            "ovstream_fetch(): unsupported platform ${CMAKE_SYSTEM_NAME}. "
            "The library ships pre-built archives for windows-x86_64, "
            "linux-x86_64, and linux-aarch64 only.")
    endif()
endfunction()

macro(ovstream_fetch)
    find_package(ovstream QUIET)
    if(ovstream_FOUND)
        message(STATUS "Found ovstream at: ${ovstream_DIR}")
    elseif(DEFINED OVSTREAM_LOCAL_PACKAGE_DIR)
        # Developer override: point at a pre-extracted local package.
        # Absolutise so find_package() doesn't depend on quirky relative-
        # path resolution against the wrong base. If the caller passed an
        # absolute path, this is a no-op; if relative, it's resolved
        # against this helper's own source dir, which is at least
        # deterministic. Callers passing relative paths from a third
        # CWD should pre-absolutise (`%CD%\...` / `$PWD/...`) instead.
        get_filename_component(_ovstream_local_abs
            "${OVSTREAM_LOCAL_PACKAGE_DIR}" ABSOLUTE)
        list(APPEND CMAKE_PREFIX_PATH "${_ovstream_local_abs}")
        find_package(ovstream REQUIRED)
        message(STATUS "Found ovstream (local): ${ovstream_DIR}")
    else()
        if(DEFINED OVSTREAM_PACKAGE_URL_OVERRIDE)
            # Full URL override takes precedence over the
            # base+version+platform derivation.
            set(OVSTREAM_PACKAGE_URL "${OVSTREAM_PACKAGE_URL_OVERRIDE}")
        else()
            _ovstream_detect_platform(_ovstream_platform)
            # Archive filename is ovstream@<version>.<platform>.zip
            # (the '@' separator matches the canonical naming convention).
            set(OVSTREAM_PACKAGE_URL
                "${OVSTREAM_PACKAGE_URL_BASE}/ovstream@${OVSTREAM_VERSION}.${_ovstream_platform}.zip")
        endif()

        message(STATUS "Fetching ovstream from ${OVSTREAM_PACKAGE_URL}")

        include(FetchContent)
        set(FETCHCONTENT_QUIET FALSE)

        if(NOT DEFINED CACHE{FETCHCONTENT_BASE_DIR})
            set(FETCHCONTENT_BASE_DIR "${_OVSTREAM_CMAKE_DIR}/_deps"
                CACHE PATH "Shared FetchContent directory")
        endif()

        FetchContent_Declare(ovstream
            URL "${OVSTREAM_PACKAGE_URL}"
            DOWNLOAD_EXTRACT_TIMESTAMP TRUE
        )
        FetchContent_MakeAvailable(ovstream)
        list(APPEND CMAKE_PREFIX_PATH ${ovstream_SOURCE_DIR})
        find_package(ovstream REQUIRED)
    endif()
endmacro()

function(ovstream_setup_runtime TARGET_NAME)
    if(CMAKE_SYSTEM_NAME STREQUAL "Windows")
        # Copy all runtime DLLs and plugins to the build directory.
        file(GLOB OVSTREAM_RUNTIME_DLLS "${OVSTREAM_BINARY_DIR}/*.dll")
        foreach(DLL ${OVSTREAM_RUNTIME_DLLS})
            add_custom_command(TARGET ${TARGET_NAME} POST_BUILD
                COMMAND ${CMAKE_COMMAND} -E copy_if_different "${DLL}" "$<TARGET_FILE_DIR:${TARGET_NAME}>"
            )
        endforeach()
        add_custom_command(TARGET ${TARGET_NAME} POST_BUILD
            COMMAND ${CMAKE_COMMAND} -E make_directory "$<TARGET_FILE_DIR:${TARGET_NAME}>/gst-plugins"
        )
        file(GLOB OVSTREAM_GST_PLUGINS "${OVSTREAM_BINARY_DIR}/gst-plugins/*.dll")
        foreach(PLUGIN ${OVSTREAM_GST_PLUGINS})
            add_custom_command(TARGET ${TARGET_NAME} POST_BUILD
                COMMAND ${CMAKE_COMMAND} -E copy_if_different "${PLUGIN}" "$<TARGET_FILE_DIR:${TARGET_NAME}>/gst-plugins/"
            )
        endforeach()
    else()
        set_target_properties(${TARGET_NAME} PROPERTIES
            BUILD_RPATH "${OVSTREAM_BINARY_DIR}"
            INSTALL_RPATH "${OVSTREAM_BINARY_DIR}"
        )
    endif()
endfunction()
