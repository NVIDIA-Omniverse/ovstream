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

#include <array>
#include <chrono>
#include <cstdint>
#include <limits>
#include <thread>

namespace ovstream_utils
{

//--------------------------------------------------------------
inline uint64_t now_ns() noexcept;

//--------------------------------------------------------------
//! Stack-friendly RAII object for encapsulating loop instances.
//--------------------------------------------------------------
class Loop
{
public:
    //----------------------------------------------------------
    //! Type aliases for the public ovstream_utils C data types.
    //! Ensure that publicly visible C/C++ surfaces never drift.
    //----------------------------------------------------------
    using Config = ::ovstream_utils_loop_config_t;
    using Stats  = ::ovstream_utils_stats_t;
    using Tick   = ::ovstream_utils_tick_t;

    explicit Loop(const Config& a_config = Config{}) noexcept;
    ~Loop() = default;

    // Non-copyable, non-movable. The ring-buffer state is ~4 KB and
    // copying / moving a live pacer object isn't a meaningful use
    // case -- the canonical pattern is one Loop per pacing thread,
    // scoped to that thread's lifetime. Disabling makes the intent
    // explicit and avoids a silent ~4 KB shallow copy if a caller
    // mistakenly returns a Loop by value or stores one in a
    // container.
    Loop(const Loop&)            = delete;
    Loop& operator=(const Loop&) = delete;
    Loop(Loop&&)                 = delete;
    Loop& operator=(Loop&&)      = delete;

    Tick tick() noexcept;
    void reset() noexcept;
    void reconfigure(const Config& a_config) noexcept;

private:
    static void waitUntil(uint64_t deadlineNs) noexcept;
    void recordFrameTime(uint64_t startTimeNs) noexcept;

    uint32_t computeFpsCurrent(uint64_t startTimeNs) const noexcept;
    uint32_t computeFpsAverage(uint64_t startTimeNs) const noexcept;

    uint64_t m_fixedStepNs        = 0;
    uint64_t m_targetFrameNs      = 0;
    uint32_t m_fixedStepMax       = 0;
    float    m_fixedTimeSeconds   = 0.0f;

    static constexpr uint64_t kInvalidTimeNs =
        std::numeric_limits<uint64_t>::max();
    uint64_t m_lastTickStartTimeNs = kInvalidTimeNs;
    uint64_t m_lastWaitDeadlineNs  = 0;
    uint64_t m_accumulatorNs       = 0;
    uint64_t m_frameIndex          = 0;
    uint64_t m_fixedStepTotal      = 0;
    uint64_t m_resetMonoNs         = 0;

    static constexpr uint32_t kRecentFrameTimesCapacity = 512u;
    std::array<uint64_t, kRecentFrameTimesCapacity> m_recentFrameTimes {};
    uint32_t m_recentFrameTimesHead  = 0;
    uint32_t m_recentFrameTimesCount = 0;
};

//--------------------------------------------------------------
// Inline (header-only) implementation. The C ABI shared library
// reuses the same class internally for a single source of truth.
//--------------------------------------------------------------

//--------------------------------------------------------------
//! @ref ovstream_utils_now_ns()
//--------------------------------------------------------------
inline uint64_t now_ns() noexcept
{
    using namespace std::chrono;
    using clock = steady_clock;
    using duration = clock::duration;

    const duration t = clock::now().time_since_epoch();
    const nanoseconds ns = duration_cast<nanoseconds>(t);
    return static_cast<uint64_t>(ns.count());
}

//--------------------------------------------------------------
//! Create a new Loop from the supplied configuration values.
//!
//! @param[in] a_config Config values to create the Loop with.
//--------------------------------------------------------------
inline Loop::Loop(const Config& a_config) noexcept
{
    reconfigure(a_config);
}

//--------------------------------------------------------------
inline void Loop::waitUntil(uint64_t deadlineNs) noexcept
{
    // 0.5 ms covers normal kernel timer granularity on Windows
    // and is well below the typical Linux/macOS wakeup jitter.
    constexpr uint64_t kSpinThresholdNs = 500'000ull;

    // Sleep until reaching spin threshold.
    const uint64_t beforeSleepNs = now_ns();
    if (deadlineNs > beforeSleepNs + kSpinThresholdNs)
    {
        const uint64_t sleepNs = deadlineNs - beforeSleepNs - kSpinThresholdNs;
        std::this_thread::sleep_for(std::chrono::nanoseconds(sleepNs));
    }

    // Spin until reaching deadline.
    while (now_ns() < deadlineNs)
    {
        std::this_thread::yield();
    }
}

//--------------------------------------------------------------
inline void Loop::recordFrameTime(uint64_t startTimeNs) noexcept
{
    m_recentFrameTimes[m_recentFrameTimesHead] = startTimeNs;
    m_recentFrameTimesHead =
        (m_recentFrameTimesHead + 1u) % kRecentFrameTimesCapacity;
    if (m_recentFrameTimesCount < kRecentFrameTimesCapacity)
    {
        ++m_recentFrameTimesCount;
    }
}

//--------------------------------------------------------------
// O(`kRecentFrameTimesCapacity` = 512) on every tick. At a 60 Hz
// pacing rate that's ~30,720 timestamp comparisons / second, well
// below the noise floor of the surrounding nanosecond clock reads
// and frame work. If the buffer ever grows large enough for this
// to matter, switch the ring storage to a sorted/timestamped
// structure that lets us binary-search the window bounds; the
// current array layout makes the linear scan acceptable in
// exchange for cache-friendliness on the steady-state path.
//--------------------------------------------------------------
inline uint32_t Loop::computeFpsCurrent(uint64_t startTimeNs) const noexcept
{
    constexpr uint64_t kOneSecondNs = 1'000'000'000ull;
    const uint64_t windowStartNs = (startTimeNs > kOneSecondNs)
        ? (startTimeNs - kOneSecondNs)
        : 0ull;

    uint32_t count = 0u;
    for (uint32_t i = 0u; i < m_recentFrameTimesCount; ++i)
    {
        if (m_recentFrameTimes[i] >= windowStartNs && m_recentFrameTimes[i] <= startTimeNs)
        {
            ++count;
        }
    }
    return count;
}

//--------------------------------------------------------------
inline uint32_t Loop::computeFpsAverage(uint64_t startTimeNs) const noexcept
{
    if (startTimeNs <= m_resetMonoNs || m_frameIndex == 0u)
    {
        return 0u;
    }
    const uint64_t elapsedNs = startTimeNs - m_resetMonoNs;
    const double seconds = static_cast<double>(elapsedNs) * 1e-9;
    if (seconds <= 0.0)
    {
        return 0u;
    }
    const double avg = static_cast<double>(m_frameIndex + 1u) / seconds;
    return static_cast<uint32_t>(avg + 0.5);
}

//--------------------------------------------------------------
//! Tick loop instance, and wait if needed to hit the fpsTarget.
//! Calculates variable and fixed-step timing values along with
//! other frame related statistics (returned with `Loop::Tick`).
//!
//! @return Frame timing/statistics values that were calculated.
//--------------------------------------------------------------
inline Loop::Tick Loop::tick() noexcept
{
    Tick out{};

    // Wait phase: skipped when uncapped or on the first tick after
    // create / reconfigure-with-reset. Cadence is anchored on the
    // previous deadline so per-frame work-duration jitter does not
    // accumulate into long-term drift while the loop is under budget.
    if (m_targetFrameNs > 0u && m_lastTickStartTimeNs != kInvalidTimeNs)
    {
        const uint64_t deadlineNs = (m_lastWaitDeadlineNs == 0u)
            ? (m_lastTickStartTimeNs + m_targetFrameNs)
            : (m_lastWaitDeadlineNs + m_targetFrameNs);

        const uint64_t nowAtCheckNs = now_ns();
        if (deadlineNs <= nowAtCheckNs)
        {
            // Over budget: don't sleep, re-anchor on now. Matches
            // simple-process; avoids artificial frame drops.
            m_lastWaitDeadlineNs = nowAtCheckNs;
        }
        else
        {
            waitUntil(deadlineNs);
            m_lastWaitDeadlineNs = deadlineNs;
        }
    }

    // Measurement phase.
    const uint64_t startTimeNs = now_ns();

    if (m_lastTickStartTimeNs == kInvalidTimeNs)
    {
        // First tick after create or reset -- no previous frame.
        out.deltaTimeSeconds = 0.0f;
        out.fixedStepCount   = 0u;
        out.fixedTimeSeconds = m_fixedTimeSeconds;
        out.fixedTimeAlpha   = 0.0f;
    }
    else
    {
        const uint64_t rawDeltaNs = startTimeNs - m_lastTickStartTimeNs;

        // Variable/render dt: raw measured wall-clock between ticks.
        out.deltaTimeSeconds = static_cast<float>(rawDeltaNs) * 1e-9f;

        if (m_fixedStepNs > 0u)
        {
            // Accumulator uses raw elapsed plus leftover remainder.
            // The result is fixed step work the caller owes on this
            // tick. At integer rate ratios it is steady; at non-
            // integer ratios it alternates over time to preserve the
            // long-run average. The fixedStepMax cap is the spiral-
            // of-death guard; when hit, excess time is discarded
            // rather than carried forever.
            m_accumulatorNs += rawDeltaNs;
            uint64_t steps = m_accumulatorNs / m_fixedStepNs;
            if (steps > m_fixedStepMax)
            {
                steps = m_fixedStepMax;
                m_accumulatorNs = 0u;
            }
            else
            {
                m_accumulatorNs -= steps * m_fixedStepNs;
            }
            out.fixedStepCount   = static_cast<uint32_t>(steps);
            out.fixedTimeSeconds = m_fixedTimeSeconds;
            out.fixedTimeAlpha   =
                static_cast<float>(m_accumulatorNs) / static_cast<float>(m_fixedStepNs);
            m_fixedStepTotal += steps;
        }
        else
        {
            out.fixedStepCount   = 0u;
            out.fixedTimeSeconds = 0.0f;
            out.fixedTimeAlpha   = 0.0f;
        }
    }

    out.frameIndex  = m_frameIndex;
    out.startTimeNs = startTimeNs;

    recordFrameTime(startTimeNs);
    out.stats.fpsCurrent = computeFpsCurrent(startTimeNs);
    out.stats.fpsAverage = computeFpsAverage(startTimeNs);
    out.stats.frameTotal = m_frameIndex + 1u;
    out.stats.fixedTotal = m_fixedStepTotal;

    m_lastTickStartTimeNs = startTimeNs;
    ++m_frameIndex;

    return out;
}

//--------------------------------------------------------------
//! Reset internal loop state, preserving current configuration.
//--------------------------------------------------------------
inline void Loop::reset() noexcept
{
    m_lastTickStartTimeNs = kInvalidTimeNs;
    m_lastWaitDeadlineNs = 0;
    m_accumulatorNs = 0;
    m_frameIndex = 0;
    m_fixedStepTotal = 0;
    m_resetMonoNs = now_ns();
    m_recentFrameTimes.fill(0);
    m_recentFrameTimesHead = 0;
    m_recentFrameTimesCount = 0;
}

//--------------------------------------------------------------
// Convert an integer rate in Hz to its frame interval in ns.
// Returns 0 when `hz` is 0 (the documented "disabled" sentinel).
// At very large `hz` (>= 1 GHz), the integer division would
// otherwise truncate to 0 and trigger divide-by-zero downstream
// in the accumulator path; floor the result at 1 ns to keep the
// hot path well-defined under garbage input.
//--------------------------------------------------------------
inline uint64_t intervalNsFromHz(uint32_t hz) noexcept
{
    if (hz == 0u)
    {
        return 0u;
    }
    const uint64_t interval = 1'000'000'000ull / hz;
    return interval == 0u ? 1u : interval;
}

//--------------------------------------------------------------
//! Set new configuration values, and reset internal loop state.
//!
//! @param[in] a_config New configuration values to be applied.
//--------------------------------------------------------------
inline void Loop::reconfigure(const Config& a_config) noexcept
{
    m_fixedStepNs      = intervalNsFromHz(a_config.fixedStepHz);
    m_fixedTimeSeconds = a_config.fixedStepHz > 0u ?
                         1.0f / static_cast<float>(a_config.fixedStepHz) :
                         0.0f;

    m_targetFrameNs = intervalNsFromHz(a_config.fpsTarget);

    constexpr uint32_t kDefaultFixedStepMax = 8u;
    m_fixedStepMax = a_config.fixedStepMax > 0u ?
                     a_config.fixedStepMax :
                     kDefaultFixedStepMax;

    reset();
}

} // namespace ovstream_utils
