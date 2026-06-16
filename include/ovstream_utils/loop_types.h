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

#include <stdint.h>

#ifdef __cplusplus
extern "C"
{
#endif

//--------------------------------------------------------------
//! Opaque handle to a single independently paced loop instance.
//--------------------------------------------------------------
typedef struct ovstream_utils_loop ovstream_utils_loop_t;

//--------------------------------------------------------------
//! Configuration values used for creating/reconfiguring a loop.
//--------------------------------------------------------------
typedef struct
{
    uint32_t fpsTarget;     //! 0 for uncapped; no wait in tick.
    uint32_t fixedStepHz;   //! 0 for disabled; no step in tick.
    uint32_t fixedStepMax;  //! 0 for default; max 8 each frame.
} ovstream_utils_loop_config_t;

//--------------------------------------------------------------
//! Statistics accumulated over multiple ticks of the same loop.
//--------------------------------------------------------------
typedef struct
{
    uint32_t fpsCurrent;    //! Frames counted over last second.
    uint32_t fpsAverage;    //! Average fps since start / reset.
    uint64_t frameTotal;    //! Total count since start / reset.
    uint64_t fixedTotal;    //! Total count since start / reset.
} ovstream_utils_stats_t;

//--------------------------------------------------------------
//! Values calculated during calls to `ovstream_utils_loop_tick`
//! that callers can use to pace their simulation and rendering.
//!
//! `deltaTimeSeconds`: raw measured wall-clock time elapsed since
//! the previous tick; never clamped (callers needing a stable
//! integrator under stalls should drive that work off `fixedStepHz`
//! + `fixedStepCount` instead).
//!
//! `fixedTimeSeconds`: a constant value measuring time elapsed
//! between fixed steps if `fixedStepHz` set (1.0f/fixedStepHz).
//!
//! `fixedTimeAlpha`: the fraction of a fixed step that will be
//! carried over to the next frame (can be used to interpolate).
//!
//! `fixedStepCount`: the number of fixed steps that can fit in
//! the time elapsed since last tick plus accumulated remainder.
//!
//! `startTimeNs`: the monotonic tick start time in nanoseconds.
//!
//! `frameIndex`: the index of the frame/tick that was measured.
//!
//! `stats`: a snapshot of loop stats at the time it was ticked.
//--------------------------------------------------------------
typedef struct
{
    float    deltaTimeSeconds; //! Time elapsed since last tick.
    float    fixedTimeSeconds; //! Time elapsed each fixed step.
    float    fixedTimeAlpha;   //! For fixed step interpolation.
    uint32_t fixedStepCount;   //! Fixed steps since prior tick.
    uint64_t startTimeNs;      //! Monotonic tick start time ns.
    uint64_t frameIndex;       //! Index of measured frame/tick.
    ovstream_utils_stats_t stats; //! Frame/tick stats snapshot.
} ovstream_utils_tick_t;

#ifdef __cplusplus
} // extern "C"
#endif
