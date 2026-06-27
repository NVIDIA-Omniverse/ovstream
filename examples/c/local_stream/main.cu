// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: LicenseRef-NvidiaProprietary
//
// NVIDIA CORPORATION, its affiliates and licensors retain all intellectual
// property and proprietary rights in and to this material, related
// documentation and any modifications thereto. Any use, reproduction,
// disclosure or distribution of this material and related documentation
// without an express license agreement from NVIDIA CORPORATION or
// its affiliates is strictly prohibited.

// SHM streaming example: animated CUDA gradient pushed into a named
// shared-memory ring buffer for same-machine consumers.
//
// Run a reader in another shell to observe frames:
//   python examples/python/local_stream/main.py local_stream --reader
//
// Or attach from your own consumer using the ovstream_client_*
// API in <ovstream/ovstream_client.h> with OVSTREAM_CLIENT_SHM.

#include <ovstream/ovstream.h>
#include <cuda_runtime.h>

#include <chrono>
#include <csignal>
#include <cstdio>
#include <cstring>
#include <thread>

__global__ void generateTestPattern(uint8_t* buffer, int width, int height,
                                    int pitch, int frameNum)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x;
    const int y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= width || y >= height)
    {
        return;
    }
    uint8_t* pixel = buffer + y * pitch + x * 4;
    pixel[0] = (uint8_t)((x + frameNum) % 256);
    pixel[1] = (uint8_t)((y + frameNum) % 256);
    pixel[2] = (uint8_t)((x + y + frameNum) % 256);
    pixel[3] = 255;
}

static volatile bool g_running = true;
void signalHandler(int sig) { (void)sig; g_running = false; }

void logCallback(ovstream_log_level_t severity, ovstream_string_t message,
                 ovstream_string_t channel, double timestamp, void* user_data)
{
    (void)timestamp;
    (void)user_data;
    const char* lvl[] = { "DEFAULT", "VERBOSE", "INFO", "WARNING", "ERROR", "NONE" };
    const size_t lvlCount = sizeof(lvl) / sizeof(lvl[0]);
    const char* levelStr =
        (severity >= 0 && (size_t)severity < lvlCount) ? lvl[severity] : "UNKNOWN";
    fprintf(stderr, "[%s][%s] %s\n", levelStr, channel.ptr, message.ptr);
}

void onConnection(ovstream_server_t* server, bool connected, void* userData)
{
    (void)server;
    (void)userData;
    printf("Reader %s\n", connected ? "attached" : "detached");
}

int main(int argc, char** argv)
{
    signal(SIGINT, signalHandler);

    const char* streamName = (argc > 1) ? argv[1] : "local_stream";
    const int WIDTH  = 1280;
    const int HEIGHT = 720;

    ovstream_init_config_t initCfg = {};
    initCfg.log_callback = logCallback;
    initCfg.log_min_severity = OVSTREAM_LOG_WARNING;
    if (!OVSTREAM_OK(ovstream_initialize(&initCfg)))
    {
        fprintf(stderr, "Failed to initialize: %s\n", ovstream_get_last_error().ptr);
        return 1;
    }

    // [snippet:create-shm-server]
    ovstream_server_t* server = nullptr;
    if (!OVSTREAM_OK(ovstream_create_server(OVSTREAM_SERVER_SHM, &server)))
    {
        fprintf(stderr, "create_server failed: %s\n", ovstream_get_last_error().ptr);
        (void)ovstream_shutdown();
        return 1;
    }
    (void)ovstream_set_connection_callback(server, onConnection, nullptr);

    ovstream_server_config_t cfg;
    ovstream_config_defaults(&cfg);
    cfg.width  = WIDTH;
    cfg.height = HEIGHT;
    cfg.shm.stream_name.ptr = streamName;
    cfg.shm.stream_name.length = std::strlen(streamName);

    if (!OVSTREAM_OK(ovstream_start(server, &cfg)))
    {
        fprintf(stderr, "ovstream_start failed: %s\n", ovstream_get_last_error().ptr);
        (void)ovstream_destroy_server(server);
        (void)ovstream_shutdown();
        return 1;
    }
    // [/snippet:create-shm-server]
    printf("Streaming as '%s' (Ctrl+C to stop).\n", streamName);
    printf("Reader command: python examples/python/local_stream/main.py %s --reader\n",
           streamName);

    uint8_t* d_buf = nullptr;
    size_t pitch = 0;
    if (cudaMallocPitch(&d_buf, &pitch, WIDTH * 4, HEIGHT) != cudaSuccess || !d_buf)
    {
        fprintf(stderr, "cudaMallocPitch failed\n");
        (void)ovstream_stop(server);
        (void)ovstream_destroy_server(server);
        (void)ovstream_shutdown();
        return 1;
    }

    const dim3 block(16, 16);
    const dim3 grid((WIDTH + 15) / 16, (HEIGHT + 15) / 16);
    const auto targetFrameTime = std::chrono::microseconds(1000000 / 60);
    int frameNum = 0;

    while (g_running)
    {
        const auto frameStart = std::chrono::steady_clock::now();

        generateTestPattern<<<grid, block>>>(d_buf, WIDTH, HEIGHT, (int)pitch, frameNum);
        cudaDeviceSynchronize();

        ovstream_video_frame_t frame = {};
        frame.buffer = d_buf;
        frame.width  = WIDTH;
        frame.height = HEIGHT;
        frame.pitch_bytes = (uint32_t)pitch;
        (void)ovstream_stream_video(server, &frame);
        frameNum++;

        const auto elapsed = std::chrono::steady_clock::now() - frameStart;
        if (elapsed < targetFrameTime)
        {
            std::this_thread::sleep_for(targetFrameTime - elapsed);
        }
    }

    printf("\nShutting down after %d frames.\n", frameNum);
    cudaFree(d_buf);
    (void)ovstream_stop(server);
    (void)ovstream_destroy_server(server);
    (void)ovstream_shutdown();
    return 0;
}
