// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: LicenseRef-NvidiaProprietary
//
// NVIDIA CORPORATION, its affiliates and licensors retain all intellectual
// property and proprietary rights in and to this material, related
// documentation and any modifications thereto. Any use, reproduction,
// disclosure or distribution of this material and related documentation
// without an express license agreement from NVIDIA CORPORATION or
// its affiliates is strictly prohibited.

// Pre-encoded H.264 streaming example.
// Demonstrates streaming pre-encoded video frames (no CUDA required).
// In a real application, the encoded frames would come from a hardware
// encoder, file, or network source.

#include <ovstream/ovstream.h>

#include <chrono>
#include <csignal>
#include <cstdio>
#include <cstdint>
#include <cstring>
#include <thread>
#include <vector>

//--------------------------------------------------------------
static volatile bool g_running = true;

void signalHandler(int sig)
{
    (void)sig;
    g_running = false;
}

//--------------------------------------------------------------
void logCallback(ovstream_log_level_t severity, ovstream_string_t message,
                 ovstream_string_t channel, double timestamp, void* user_data)
{
    (void)timestamp;
    (void)user_data;
    const char* levelStr[] = { "DEFAULT", "VERBOSE", "INFO", "WARNING", "ERROR", "NONE" };
    constexpr size_t levelCount = sizeof(levelStr) / sizeof(levelStr[0]);
    const char* label =
        (severity >= 0 && static_cast<size_t>(severity) < levelCount) ? levelStr[severity] : "UNKNOWN";
    // channel.ptr / message.ptr are null-terminated per the output-string
    // contract, so they're safe to pass directly to printf's %s.
    fprintf(stderr, "[%s][%s] %s\n", label, channel.ptr, message.ptr);
}

//--------------------------------------------------------------
// ============================================================
// WARNING: SYNTHETIC, NON-DECODABLE H.264 FOR DEMONSTRATION ONLY
// ============================================================
// This function emits an Annex B access unit shaped like H.264 (SPS + PPS
// + IDR prefix with random padding), but the slice payload is NOT a real
// encoded picture. ffplay / VLC / gst-rtsp clients will connect to the
// stream, negotiate H.264, and then silently fail to display anything.
//
// The purpose of this example is to exercise the SDK's pre-encoded
// bitstream ingestion path end-to-end without pulling in NVENC as a
// dependency. DO NOT copy this function into a real application --
// replace it with NVENC / x264 / hardware encoder output.
//--------------------------------------------------------------
std::vector<uint8_t> generateFakeH264Frame(uint32_t width, uint32_t height, int frameNum)
{
    // Minimal H.264 SPS for baseline profile.
    // This is a simplified/synthetic SPS — a real encoder would produce a proper one.
    static const uint8_t sps[] = {
        0x00, 0x00, 0x00, 0x01,  // Annex B start code
        0x67,                     // NAL type 7 (SPS)
        0x42, 0xC0, 0x1E,        // Baseline profile, level 3.0
        0xD9, 0x00, 0xA0, 0x47,  // Simplified SPS payload
        0xFE, 0xC8,
    };

    // Minimal PPS.
    static const uint8_t pps[] = {
        0x00, 0x00, 0x00, 0x01,  // Annex B start code
        0x68,                     // NAL type 8 (PPS)
        0xCE, 0x38, 0x80,
    };

    // Minimal IDR slice (type 5) — just enough to be parseable.
    // The actual encoded data would come from NVENC in a real application.
    static const uint8_t idr_prefix[] = {
        0x00, 0x00, 0x00, 0x01,  // Annex B start code
        0x65,                     // NAL type 5 (IDR)
    };

    // Build the access unit.
    std::vector<uint8_t> au;
    au.insert(au.end(), sps, sps + sizeof(sps));
    au.insert(au.end(), pps, pps + sizeof(pps));
    au.insert(au.end(), idr_prefix, idr_prefix + sizeof(idr_prefix));

    // Pad with some varying data to simulate encoded frame content.
    const size_t padSize = 1024;
    for (size_t i = 0; i < padSize; ++i)
    {
        au.push_back(static_cast<uint8_t>((i + frameNum) & 0xFF));
    }

    return au;
}

//--------------------------------------------------------------
int main()
{
    signal(SIGINT, signalHandler);

    ovstream_init_config_t initCfg = {};
    initCfg.log_callback = logCallback;
    initCfg.log_min_severity = OVSTREAM_LOG_ERROR;
    if (!OVSTREAM_OK(ovstream_initialize(&initCfg)))
    {
        fprintf(stderr, "Failed to initialize OVSTREAM: %s\n", ovstream_get_last_error().ptr);
        return 1;
    }

    // Create an RTSP server configured for pre-encoded H.264.
    ovstream_server_t* server = nullptr;
    if (!OVSTREAM_OK(ovstream_create_server(OVSTREAM_SERVER_RTSP, &server)))
    {
        fprintf(stderr, "Failed to create server: %s\n", ovstream_get_last_error().ptr);
        (void)ovstream_shutdown();
        return 1;
    }

    // [snippet:configure-pre-encoded]
    ovstream_server_config_t cfg;
    if (!OVSTREAM_OK(ovstream_config_defaults(&cfg)))
    {
        fprintf(stderr, "Failed to set config defaults: %s\n", ovstream_get_last_error().ptr);
        (void)ovstream_destroy_server(server);
        (void)ovstream_shutdown();
        return 1;
    }
    cfg.width = 1920;
    cfg.height = 1080;
    cfg.target_fps = 30;
    cfg.video_input = OVSTREAM_VIDEO_INPUT_H264;
    // [/snippet:configure-pre-encoded]

    if (!OVSTREAM_OK(ovstream_start(server, &cfg)))
    {
        fprintf(stderr, "Failed to start server: %s\n", ovstream_get_last_error().ptr);
        (void)ovstream_destroy_server(server);
        (void)ovstream_shutdown();
        return 1;
    }

    printf("RTSP pre-encoded H.264 stream at: rtsp://localhost:8554/stream\n");
    fprintf(stderr,
            "\n"
            "================================================================\n"
            "WARNING: The synthetic H.264 frames this example emits are NOT\n"
            "         valid decodable video. Clients (ffplay, VLC, etc) will\n"
            "         connect successfully but nothing will render.\n"
            "         In a real application, replace generateFakeH264Frame()\n"
            "         with NVENC / x264 / hardware encoder output.\n"
            "================================================================\n"
            "\n");
    printf("Press Ctrl+C to stop.\n");

    int frameNum = 0;
    const auto targetFrameTime = std::chrono::microseconds(1000000 / 30); // 30 FPS

    // [snippet:pre-encoded-loop]
    while (g_running)
    {
        const auto frameStart = std::chrono::steady_clock::now();

        // Per-iteration std::vector is safe: ovstream_stream_video stages
        // the bitstream into server-owned memory before returning, so
        // the vector can go out of scope at the bottom of the loop.
        std::vector<uint8_t> encodedFrame = generateFakeH264Frame(1920, 1080, frameNum);

        ovstream_video_frame_t frame = {};
        frame.buffer = encodedFrame.data();
        frame.width = 1920;
        frame.height = 1080;
        frame.size_bytes = static_cast<uint32_t>(encodedFrame.size());
        // Ignore per-frame failures (e.g. no client connected yet).
        // Enable verbose logs for diagnostics.
        (void)ovstream_stream_video(server, &frame);

        frameNum++;

        const auto elapsed = std::chrono::steady_clock::now() - frameStart;
        if (elapsed < targetFrameTime)
        {
            std::this_thread::sleep_for(targetFrameTime - elapsed);
        }
    }
    // [/snippet:pre-encoded-loop]

    printf("\nShutting down after %d frames.\n", frameNum);

    (void)ovstream_stop(server);
    (void)ovstream_destroy_server(server);
    (void)ovstream_shutdown();

    return 0;
}
