// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: LicenseRef-NvidiaProprietary
//
// NVIDIA CORPORATION, its affiliates and licensors retain all intellectual
// property and proprietary rights in and to this material, related
// documentation and any modifications thereto. Any use, reproduction,
// disclosure or distribution of this material and related documentation
// without an express license agreement from NVIDIA CORPORATION or
// its affiliates is strictly prohibited.
#pragma once

#include "loop_types.h"

#ifndef __cplusplus
#   include <stdbool.h>
#endif

#ifdef _WIN32
#   ifdef OVSTREAM_UTILS_BUILD
#       define OVSTREAM_UTILS_API __declspec(dllexport)
#   else
#       define OVSTREAM_UTILS_API __declspec(dllimport)
#   endif
#else
#   define OVSTREAM_UTILS_API __attribute__((visibility("default")))
#endif

#ifdef __cplusplus
extern "C"
{
#endif

//--------------------------------------------------------------
//! Get the current monotonic time of the internal steady clock.
//
//! @return Current monotonic time of the internal steady clock.
//--------------------------------------------------------------
OVSTREAM_UTILS_API uint64_t ovstream_utils_now_ns(void);

//--------------------------------------------------------------
//! Retrieve the default configuration values for a loop object.
//! Defaults are all zero; uncapped, no fixed step calculations.
//
//! @param[out] o_config Configuration values for a loop object.
//--------------------------------------------------------------
OVSTREAM_UTILS_API void ovstream_utils_loop_config_defaults(ovstream_utils_loop_config_t* o_config);

//--------------------------------------------------------------
//! Create a new loop instance using the supplied config values.
//!
//! @param[in] a_config Config values to create the loop object.
//! @param[out] o_loop Opaque handle to store a new loop object.
//--------------------------------------------------------------
OVSTREAM_UTILS_API void ovstream_utils_loop_create(const ovstream_utils_loop_config_t* a_config,
                                                   ovstream_utils_loop_t** o_loop);

//--------------------------------------------------------------
//! Destroy a loop handle created by ovstream_utils_loop_create.
//!
//! @param[in] a_loop A loop handle to destroy; NULL is a no-op.
//--------------------------------------------------------------
OVSTREAM_UTILS_API void ovstream_utils_loop_destroy(ovstream_utils_loop_t* a_loop);

//--------------------------------------------------------------
//! Tick loop instance, and wait if needed to hit the fpsTarget.
//! Calculates variable and fixed-step timing values along with
//! other related statistics returned via ovstream_utils_tick_t.
//!
//! @param[in] a_loop Loop handle to tick; NULL is a safe no-op.
//! @param[out] o_tick Calculated frame timing/statistic values.
//--------------------------------------------------------------
OVSTREAM_UTILS_API void ovstream_utils_loop_tick(ovstream_utils_loop_t* a_loop,
                                                 ovstream_utils_tick_t* o_tick);

//--------------------------------------------------------------
//! Reset internal loop state, preserving current configuration.
//!
//! @param[in] a_loop Loop handle to reset; NULL is a safe no-op.
//--------------------------------------------------------------
OVSTREAM_UTILS_API void ovstream_utils_loop_reset(ovstream_utils_loop_t* a_loop);

//--------------------------------------------------------------
//! Set new configuration values, and reset internal loop state.
//!
//! @param[in] a_loop Loop handle to reconfigure; NULL is no-op.
//! @param[in] a_config New configuration values; NULL is no-op.
//--------------------------------------------------------------
OVSTREAM_UTILS_API void ovstream_utils_loop_reconfigure(ovstream_utils_loop_t* a_loop,
                                                        const ovstream_utils_loop_config_t* a_config);

#ifdef __cplusplus
} // extern "C"
#endif
