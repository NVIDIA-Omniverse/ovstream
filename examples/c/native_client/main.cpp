// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: LicenseRef-NvidiaProprietary
//
// NVIDIA CORPORATION, its affiliates and licensors retain all intellectual
// property and proprietary rights in and to this material, related
// documentation and any modifications thereto. Any use, reproduction,
// disclosure or distribution of this material and related documentation
// without an express license agreement from NVIDIA CORPORATION or
// its affiliates is strictly prohibited.

// Native (StreamSDK) client example: connect to a running ovstream NATIVE
// server, pull decoded BGRA8 frames, and report their dimensions / sequence.
// The first frame is written to native_frame.ppm so the result can be eyeballed.
//
// Unlike the SHM / CUDASHM consumers this needs neither the same host nor the
// same GPU as the producer -- only an NVIDIA GPU on this (the consumer)
// machine, which the client uses to decode the incoming stream via
// StreamSDK's NvStreamingMedia.
//
// A pure consumer: it links only ovstream::ovstream_client and includes only
// <ovstream/ovstream_client.h> -- never the server-side ovstream library.
//
// Run a native server first, e.g.:
//   python examples/python/basic_stream/main.py native
//
// Then:
//   native_client                 connect to 127.0.0.1:49100
//   native_client 10.0.0.5        connect to a remote server
//   native_client 10.0.0.5 --frames 200

#include <ovstream/ovstream_client.h>

#include <chrono>
#include <csignal>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <thread>

static volatile bool g_running = true;
static void signalHandler(int sig) { (void)sig; g_running = false; }

// Write a BGRA8 frame to a binary PPM (P6, RGB) so it can be opened in any
// image viewer. Returns true on success.
static bool writePpm(const char* path, const ovstream_frame_t* frame)
{
    FILE* f = fopen(path, "wb");
    if (!f)
    {
        return false;
    }
    fprintf(f, "P6\n%u %u\n255\n", frame->width, frame->height);
    const uint8_t* rows = static_cast<const uint8_t*>(frame->data);
    for (uint32_t y = 0; y < frame->height; ++y)
    {
        const uint8_t* row = rows + static_cast<size_t>(y) * frame->pitch_bytes;
        for (uint32_t x = 0; x < frame->width; ++x)
        {
            const uint8_t* px = row + static_cast<size_t>(x) * 4;
            const uint8_t rgb[3] = { px[2], px[1], px[0] };  // BGRA -> RGB
            fwrite(rgb, 1, 3, f);
        }
    }
    fclose(f);
    return true;
}

int main(int argc, char** argv)
{
    signal(SIGINT, signalHandler);

    const char* serverIp = "127.0.0.1";
    uint16_t    signalPort = 0;  // 0 = default 49100
    uint16_t    streamPort = 0;  // 0 = default 47999
    int         maxFrames = 0;   // 0 = run until Ctrl+C / disconnect

    for (int i = 1; i < argc; ++i)
    {
        if (std::strcmp(argv[i], "--signal-port") == 0 && i + 1 < argc)
        {
            signalPort = static_cast<uint16_t>(std::atoi(argv[++i]));
        }
        else if (std::strcmp(argv[i], "--stream-port") == 0 && i + 1 < argc)
        {
            streamPort = static_cast<uint16_t>(std::atoi(argv[++i]));
        }
        else if (std::strcmp(argv[i], "--frames") == 0 && i + 1 < argc)
        {
            maxFrames = std::atoi(argv[++i]);
        }
        else if (argv[i][0] != '-')
        {
            serverIp = argv[i];
        }
    }

    ovstream_client_config_t cfg = {};
    cfg.native.server_ip.ptr    = serverIp;
    cfg.native.server_ip.length = std::strlen(serverIp);
    cfg.native.signal_port      = signalPort;
    cfg.native.stream_port      = streamPort;
    cfg.native.cuda_device      = -1;  // default device

    // nvstConnectToServer fails synchronously if the server isn't reachable;
    // retry for a few seconds so the launch order is forgiving.
    printf("Connecting to %s ...\n", serverIp);
    ovstream_client_t* client = nullptr;
    const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(10);
    while (std::chrono::steady_clock::now() < deadline)
    {
        if (OVSTREAM_OK(ovstream_create_client(OVSTREAM_CLIENT_NATIVE, &cfg, &client)))
        {
            break;
        }
        client = nullptr;
        std::this_thread::sleep_for(std::chrono::milliseconds(500));
    }
    if (!client)
    {
        fprintf(stderr, "Failed to connect: %s\n", ovstream_get_last_error().ptr);
        return 1;
    }
    printf("Connected. Receiving frames (Ctrl+C to stop).\n");

    int frames = 0;
    bool savedPpm = false;
    bool alive = true;
    while (g_running && (maxFrames == 0 || frames < maxFrames))
    {
        ovstream_frame_t frame = {};
        const ovstream_result_t r = ovstream_client_wait_frame(client, /*timeout_ms=*/500, &frame);
        if (r.status == OVSTREAM_API_TIMEOUT)
        {
            (void)ovstream_client_is_alive(client, &alive);
            if (!alive)
            {
                break;
            }
            continue;
        }
        if (r.status == OVSTREAM_API_INVALID_STATE)
        {
            break;  // connection dropped
        }
        if (!OVSTREAM_OK(r))
        {
            fprintf(stderr, "wait_frame error: %s\n", ovstream_get_last_error().ptr);
            break;
        }

        ++frames;
        if (frames == 1 || frames % 30 == 0)
        {
            printf("frame %d: %ux%u pitch=%u seq=%llu\n", frames, frame.width,
                   frame.height, frame.pitch_bytes,
                   static_cast<unsigned long long>(frame.sequence));
        }
        if (!savedPpm && frame.data)
        {
            savedPpm = writePpm("native_frame.ppm", &frame);
            if (savedPpm)
            {
                printf("Wrote first frame to native_frame.ppm\n");
            }
        }
    }

    (void)ovstream_client_is_alive(client, &alive);
    printf("\nReceived %d frames; connection %s.\n", frames, alive ? "still up" : "closed");
    (void)ovstream_destroy_client(client);
    return 0;
}
