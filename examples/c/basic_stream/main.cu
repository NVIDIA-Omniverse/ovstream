// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: LicenseRef-NvidiaProprietary
//
// NVIDIA CORPORATION, its affiliates and licensors retain all intellectual
// property and proprietary rights in and to this material, related
// documentation and any modifications thereto. Any use, reproduction,
// disclosure or distribution of this material and related documentation
// without an express license agreement from NVIDIA CORPORATION or
// its affiliates is strictly prohibited.

// Basic streaming example: animated CUDA gradient via WebRTC, RTSP, native,
// SHM, or any combination simultaneously.
//
// Usage:
//   basic_stream                           (no args: WebRTC on default signal port 49100)
//   basic_stream webrtc                    (WebRTC on default signal port 49100)
//   basic_stream webrtc:50000              (WebRTC on signal port 50000)
//   basic_stream rtsp                      (RTSP on default port 8554)
//   basic_stream rtsp:9000                 (RTSP on port 9000)
//   basic_stream shm                       (SHM, stream name auto: ovstream-<pid>)
//   basic_stream shm:my-stream             (SHM with explicit stream name)
//   basic_stream cudashm                   (CUDASHM, stream name auto)
//   basic_stream cudashm:my-stream         (CUDASHM with explicit stream name)
//   basic_stream webrtc rtsp shm:demo      (three transports simultaneously)
//
// For network specs the value after the colon is a port; for `shm` and
// `cudashm` it is the stream name a downstream `ovstream_client`
// (OVSTREAM_CLIENT_SHM / OVSTREAM_CLIENT_CUDASHM) (or the bundled
// `examples/python/local_stream/main_viewer.py` /
// `main_cudashm_viewer.py`) attaches to.

#include <ovstream/ovstream.h>
#include <cuda_runtime.h>

#include <chrono>
#include <csignal>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <memory>
#include <string>
#include <thread>
#include <vector>

//--------------------------------------------------------------
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

//--------------------------------------------------------------
static volatile bool g_running = true;

void signalHandler(int sig)
{
    (void)sig;
    g_running = false;
}

// [snippet:log-callback]
void logCallback(ovstream_log_level_t severity, ovstream_string_t message,
                 ovstream_string_t channel, double timestamp, void* user_data)
{
    (void)timestamp;
    (void)user_data;
    const char* lvl[] = { "DEFAULT", "VERBOSE", "INFO", "WARNING", "ERROR", "NONE" };
    const size_t lvlCount = sizeof(lvl) / sizeof(lvl[0]);
    const char* levelStr =
        (severity >= 0 && (size_t)severity < lvlCount) ? lvl[severity] : "UNKNOWN";
    // .ptr is null-terminated per the output-string contract.
    fprintf(stderr, "[%s][%s] %s\n", levelStr, channel.ptr, message.ptr);
}
// [/snippet:log-callback]

// [snippet:on-connection-callback]
void onConnection(ovstream_server_t* server, bool connected, void* userData)
{
    (void)server;
    const char* label = static_cast<const char*>(userData);
    printf("[%s] Client %s\n", label, connected ? "connected" : "disconnected");
}
// [/snippet:on-connection-callback]

// [snippet:on-message-callback]
void onMessage(ovstream_server_t* server, ovstream_string_t message, void* userData)
{
    (void)userData;
    // The message view is null-terminated per the output-string contract,
    // so we can pass message.ptr to printf's %s directly. Echoing it back
    // forwards the same view to ovstream_send_message which respects the
    // input-string contract (length-bounded, no null-termination required).
    printf("Client says: %s\n", message.ptr);
    if (!OVSTREAM_OK(ovstream_send_message(server, message)))
    {
        fprintf(stderr, "Failed to echo message: %s\n", ovstream_get_last_error().ptr);
    }
}
// [/snippet:on-message-callback]

// [snippet:on-input-callback]
void onInput(ovstream_server_t* server, const ovstream_input_event_t* event, void* userData)
{
    (void)server;
    (void)userData;
    if (event->type == OVSTREAM_INPUT_KEYBOARD)
    {
        printf("Keyboard: key_code=%u %s\n", event->keyboard.key_code,
               event->keyboard.key_state == OVSTREAM_KEY_STATE_DOWN ? "down" : "up");
    }
    else if (event->type == OVSTREAM_INPUT_MOUSE && event->mouse.type == OVSTREAM_MOUSE_BUTTON)
    {
        printf("Mouse button: %d %s\n", event->mouse.data,
               event->mouse.button_state == OVSTREAM_KEY_STATE_DOWN ? "down" : "up");
    }
    else if (event->type == OVSTREAM_INPUT_TOUCH)
    {
        printf("Touch: %u point(s)%s\n", event->touch.point_count,
               event->touch.low_level ? " (low-level)" : "");
    }
}
// [/snippet:on-input-callback]

//--------------------------------------------------------------
struct ServerSpec
{
    ovstream_server_type_t type;
    uint16_t    port;       // stream port for RTSP, signal port for WebRTC/native, 0 for SHM
    std::string streamName; // SHM only; empty for network protocols
    std::string label;
};

//--------------------------------------------------------------
bool parseServerSpec(const char* arg, ServerSpec& out)
{
    std::string s(arg);
    std::string protocol;
    std::string detail;

    const size_t colon = s.find(':');
    if (colon != std::string::npos)
    {
        protocol = s.substr(0, colon);
        detail = s.substr(colon + 1);
    }
    else
    {
        protocol = s;
    }

    out.port = 0;
    out.streamName.clear();

    if (protocol == "rtsp")
    {
        out.type = OVSTREAM_SERVER_RTSP;
        out.port = static_cast<uint16_t>(std::atoi(detail.c_str()));
        out.label = "RTSP:" + std::to_string(out.port ? out.port : 8554);
    }
    else if (protocol == "webrtc")
    {
        out.type = OVSTREAM_SERVER_WEBRTC;
        out.port = static_cast<uint16_t>(std::atoi(detail.c_str()));
        out.label = "WebRTC:" + std::to_string(out.port ? out.port : 49100);
    }
    else if (protocol == "native")
    {
        out.type = OVSTREAM_SERVER_NATIVE;
        out.port = static_cast<uint16_t>(std::atoi(detail.c_str()));
        out.label = "Native:" + std::to_string(out.port ? out.port : 49100);
    }
    else if (protocol == "shm")
    {
        out.type = OVSTREAM_SERVER_SHM;
        out.streamName = detail;
        out.label = "SHM:" + (out.streamName.empty() ? std::string("<auto>") : out.streamName);
    }
    else if (protocol == "cudashm")
    {
        out.type = OVSTREAM_SERVER_CUDASHM;
        out.streamName = detail;
        out.label = "CUDASHM:" + (out.streamName.empty() ? std::string("<auto>") : out.streamName);
    }
    else
    {
        return false;
    }

    return true;
}

//--------------------------------------------------------------
// label is owned via unique_ptr so the std::string's underlying buffer
// is at a stable heap address. Passing label->c_str() as callback
// userData is therefore safe across std::move(inst) into the vector
// and across any subsequent vector reallocations.
struct ServerInstance
{
    ovstream_server_t* server = nullptr;
    std::unique_ptr<std::string> label;
};

//--------------------------------------------------------------
int main(int argc, char** argv)
{
    signal(SIGINT, signalHandler);

    // Parse server specs from command line.
    std::vector<ServerSpec> specs;
    for (int i = 1; i < argc; ++i)
    {
        ServerSpec spec;
        if (!parseServerSpec(argv[i], spec))
        {
            fprintf(stderr, "Unknown protocol: %s\n", argv[i]);
            fprintf(stderr, "Usage: %s <protocol[:detail]> [<protocol[:detail]> ...]\n", argv[0]);
            fprintf(stderr, "  Protocols: webrtc, rtsp, native, shm, cudashm\n");
            fprintf(stderr, "  Examples:  %s webrtc\n", argv[0]);
            fprintf(stderr, "             %s webrtc:50000 rtsp\n", argv[0]);
            fprintf(stderr, "             %s webrtc rtsp:9000\n", argv[0]);
            fprintf(stderr, "             %s shm:my-stream\n", argv[0]);
            fprintf(stderr, "             %s cudashm:my-stream\n", argv[0]);
            return 1;
        }
        specs.push_back(spec);
    }

    // Default to WebRTC if no args.
    if (specs.empty())
    {
        specs.push_back({ OVSTREAM_SERVER_WEBRTC, 0, "", "WebRTC:49100" });
    }

    // [snippet:initialize-sdk]
    ovstream_init_config_t initCfg = {};
    initCfg.log_callback = logCallback;
    initCfg.log_min_severity = OVSTREAM_LOG_ERROR;
    if (!OVSTREAM_OK(ovstream_initialize(&initCfg)))
    {
        fprintf(stderr, "Failed to initialize: %s\n", ovstream_get_last_error().ptr);
        return 1;
    }
    // [/snippet:initialize-sdk]

    const int WIDTH = 1920;
    const int HEIGHT = 1080;

    std::vector<ServerInstance> servers;

    for (const auto& spec : specs)
    {
        ServerInstance inst;
        inst.label = std::make_unique<std::string>(spec.label);

        // [snippet:create-server]
        if (!OVSTREAM_OK(ovstream_create_server(spec.type, &inst.server)))
        {
            fprintf(stderr, "Failed to create %s server: %s\n", inst.label->c_str(), ovstream_get_last_error().ptr);
            continue;
        }
        // [/snippet:create-server]

        // [snippet:register-callbacks]
        // Keep label alive for connection callback. Safe to pass
        // label->c_str() here because the unique_ptr-managed string
        // stays at a stable heap address across std::move below.
        if (!OVSTREAM_OK(ovstream_set_connection_callback(inst.server, onConnection,
                                                          const_cast<char*>(inst.label->c_str()))))
        {
            fprintf(stderr, "Failed to set connection callback on %s: %s\n",
                    inst.label->c_str(), ovstream_get_last_error().ptr);
            (void)ovstream_destroy_server(inst.server);
            continue;
        }

        // Reverse-channel callbacks only fire on transports that have
        // one. WebRTC, native, SHM, and CUDASHM do; RTSP does not.
        // Enumerated explicitly so a future backend without a reverse
        // channel is not silently opted in.
        if (spec.type == OVSTREAM_SERVER_WEBRTC ||
            spec.type == OVSTREAM_SERVER_NATIVE ||
            spec.type == OVSTREAM_SERVER_SHM ||
            spec.type == OVSTREAM_SERVER_CUDASHM)
        {
            if (!OVSTREAM_OK(ovstream_set_message_callback(inst.server, onMessage, nullptr)))
            {
                fprintf(stderr, "Failed to set message callback on %s: %s\n",
                        inst.label->c_str(), ovstream_get_last_error().ptr);
                (void)ovstream_destroy_server(inst.server);
                continue;
            }
            if (!OVSTREAM_OK(ovstream_set_input_callback(inst.server, onInput, nullptr)))
            {
                fprintf(stderr, "Failed to set input callback on %s: %s\n",
                        inst.label->c_str(), ovstream_get_last_error().ptr);
                (void)ovstream_destroy_server(inst.server);
                continue;
            }
        }
        // [/snippet:register-callbacks]

        // [snippet:configure-server]
        ovstream_server_config_t cfg;
        if (!OVSTREAM_OK(ovstream_config_defaults(&cfg)))
        {
            fprintf(stderr, "Failed to set config defaults for %s: %s\n",
                    inst.label->c_str(), ovstream_get_last_error().ptr);
            (void)ovstream_destroy_server(inst.server);
            continue;
        }
        cfg.width = WIDTH;
        cfg.height = HEIGHT;
        // [/snippet:configure-server]

        if (spec.type == OVSTREAM_SERVER_RTSP)
        {
            if (spec.port)
            {
                cfg.stream_port = spec.port;
            }
        }
        else if (spec.type == OVSTREAM_SERVER_SHM)
        {
            if (!spec.streamName.empty())
            {
                cfg.shm.stream_name.ptr = spec.streamName.c_str();
                cfg.shm.stream_name.length = spec.streamName.size();
            }
        }
        else if (spec.type == OVSTREAM_SERVER_CUDASHM)
        {
            if (!spec.streamName.empty())
            {
                cfg.cudashm.stream_name.ptr = spec.streamName.c_str();
                cfg.cudashm.stream_name.length = spec.streamName.size();
            }
        }
        else // WebRTC / Native
        {
            if (spec.port)
            {
                cfg.webrtc.signal_port = spec.port;
            }
        }

        // [snippet:start-server]
        if (!OVSTREAM_OK(ovstream_start(inst.server, &cfg)))
        {
            fprintf(stderr, "Failed to start %s: %s\n", inst.label->c_str(), ovstream_get_last_error().ptr);
            (void)ovstream_destroy_server(inst.server);
            continue;
        }
        // [/snippet:start-server]

        if (spec.type == OVSTREAM_SERVER_RTSP)
        {
            printf("[%s] rtsp://localhost:%u/stream\n", inst.label->c_str(),
                   cfg.stream_port ? cfg.stream_port : 8554);
        }
        else if (spec.type == OVSTREAM_SERVER_SHM)
        {
            printf("[%s] attach with: python examples/python/local_stream/main_viewer.py %s\n",
                   inst.label->c_str(),
                   spec.streamName.empty() ? "<see ovstream log for auto name>"
                                           : spec.streamName.c_str());
        }
        else if (spec.type == OVSTREAM_SERVER_CUDASHM)
        {
            printf("[%s] attach with: python examples/python/local_stream/main_cudashm_viewer.py %s\n",
                   inst.label->c_str(),
                   spec.streamName.empty() ? "<see ovstream log for auto name>"
                                           : spec.streamName.c_str());
        }
        else
        {
            const uint16_t defaultStreamPort =
                (spec.type == OVSTREAM_SERVER_NATIVE) ? 47999 : 47998;
            printf("[%s] signal port %u, stream port %u\n", inst.label->c_str(),
                   cfg.webrtc.signal_port ? cfg.webrtc.signal_port : 49100,
                   cfg.stream_port ? cfg.stream_port : defaultStreamPort);
        }

        servers.push_back(std::move(inst));
    }

    if (servers.empty())
    {
        fprintf(stderr, "No servers started.\n");
        (void)ovstream_shutdown();
        return 1;
    }

    printf("Press Ctrl+C to stop.\n");

    // [snippet:cuda-buffer-alloc]
    // Allocate CUDA buffer.
    uint8_t* d_buf = nullptr;
    size_t pitch = 0;
    const cudaError_t cudaErr = cudaMallocPitch(&d_buf, &pitch, WIDTH * 4, HEIGHT);
    // [/snippet:cuda-buffer-alloc]
    if (cudaErr != cudaSuccess || !d_buf)
    {
        fprintf(stderr, "cudaMallocPitch failed: %s (%s)\n",
                cudaGetErrorName(cudaErr), cudaGetErrorString(cudaErr));
        for (auto& inst : servers)
        {
            (void)ovstream_stop(inst.server);
            (void)ovstream_destroy_server(inst.server);
        }
        (void)ovstream_shutdown();
        return 1;
    }

    const dim3 block(16, 16);
    const dim3 grid((WIDTH + 15) / 16, (HEIGHT + 15) / 16);
    const auto targetFrameTime = std::chrono::microseconds(1000000 / 60);
    int frameNum = 0;

    // [snippet:stream-loop]
    while (g_running)
    {
        const auto frameStart = std::chrono::steady_clock::now();

        generateTestPattern<<<grid, block>>>(d_buf, WIDTH, HEIGHT, (int)pitch, frameNum);
        cudaDeviceSynchronize();

        ovstream_video_frame_t frame = {};
        frame.buffer = d_buf;
        frame.width = WIDTH;
        frame.height = HEIGHT;
        frame.pitch_bytes = (uint32_t)pitch;

        for (auto& inst : servers)
        {
            // Ignore per-frame failures (e.g. no client connected yet).
            // Enable verbose logs for diagnostics.
            (void)ovstream_stream_video(inst.server, &frame);
        }

        frameNum++;

        const auto elapsed = std::chrono::steady_clock::now() - frameStart;
        if (elapsed < targetFrameTime)
        {
            std::this_thread::sleep_for(targetFrameTime - elapsed);
        }
    }
    // [/snippet:stream-loop]

    printf("\nShutting down after %d frames.\n", frameNum);

    // [snippet:cleanup]
    cudaFree(d_buf);
    for (auto& inst : servers)
    {
        (void)ovstream_stop(inst.server);
        (void)ovstream_destroy_server(inst.server);
    }
    (void)ovstream_shutdown();
    // [/snippet:cleanup]
    return 0;
}
