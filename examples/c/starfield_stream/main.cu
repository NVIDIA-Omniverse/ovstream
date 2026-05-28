// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: LicenseRef-NvidiaProprietary
//
// NVIDIA CORPORATION, its affiliates and licensors retain all intellectual
// property and proprietary rights in and to this material, related
// documentation and any modifications thereto. Any use, reproduction,
// disclosure or distribution of this material and related documentation
// without an express license agreement from NVIDIA CORPORATION or
// its affiliates is strictly prohibited.

// Interactive starfield streamer based on the simple-streamer sample.
// Streams video (animated starfield on the GPU) plus audio (48kHz stereo
// PCM loaded from the bundled audio_sample_48khz.pcm file, looped).
// Mouse movement hides stars near the cursor (WebRTC, native, and SHM).
// Audio is silently dropped on RTSP and SHM (neither supports audio).
//
// Usage:
//   starfield_stream                       (no args: WebRTC on default signal port)
//   starfield_stream webrtc                (WebRTC with mouse input)
//   starfield_stream rtsp                  (RTSP on default port)
//   starfield_stream shm:stars             (SHM with stream name 'stars')
//   starfield_stream webrtc rtsp shm       (three transports simultaneously)

#include <ovstream/ovstream.h>
#include <ovstream_utils/loop.hpp>
#include <cuda_runtime.h>
#include <curand_kernel.h>

#ifdef _WIN32
#define NOMINMAX
#include <windows.h>
#else
#include <unistd.h>
#endif

#include <algorithm>
#include <atomic>
#include <csignal>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <memory>
#include <string>
#include <vector>

//--------------------------------------------------------------
// Returns the directory containing the running executable, so the
// audio sample can be loaded from a path relative to the exe instead
// of the process's current working directory.
//--------------------------------------------------------------
std::string getExecutableDir()
{
#ifdef _WIN32
    char filePath[MAX_PATH] = { 0 };
    GetModuleFileNameA(nullptr, filePath, MAX_PATH);
#else
    char filePath[FILENAME_MAX] = { 0 };
    const ssize_t len = readlink("/proc/self/exe", filePath, FILENAME_MAX - 1);
    if (len > 0)
    {
        filePath[len] = '\0';
    }
#endif
    const std::string path(filePath);
    const size_t slash = path.find_last_of("/\\");
    return slash != std::string::npos ? path.substr(0, slash + 1) : std::string();
}

//--------------------------------------------------------------
// Simple 48kHz stereo 16-bit PCM audio source. Mirrors the
// AudioSource in the simple-streamer sample: opens a raw PCM file
// and fills a one-second scratch buffer with the last `dt` seconds
// of samples on each update, looping on EOF.
//--------------------------------------------------------------
class AudioSource
{
public:
    static constexpr uint32_t kSampleRate = 48000;
    static constexpr uint32_t kChannels = 2;
    static constexpr uint32_t kByteRate = kSampleRate * kChannels * sizeof(int16_t);

    bool open(const std::string& pcmFilePath)
    {
#ifdef _WIN32
        fopen_s(&m_file, pcmFilePath.c_str(), "rb");
#else
        m_file = fopen(pcmFilePath.c_str(), "rb");
#endif
        if (!m_file)
        {
            return false;
        }
        m_buffer.resize(kByteRate);
        return true;
    }

    ~AudioSource()
    {
        if (m_file)
        {
            fclose(m_file);
        }
    }

    void update(float deltaTimeSeconds)
    {
        if (!m_file)
        {
            return;
        }
        // Align down to whole sample-frames so the file offset stays
        // sample-aligned across iterations -- misalignment flips the
        // channel order and produces static.
        constexpr size_t kSampleFrameBytes = kChannels * sizeof(int16_t);
        size_t bytesToRead = std::min<size_t>(
            kByteRate, static_cast<size_t>(kByteRate * deltaTimeSeconds));
        bytesToRead -= bytesToRead % kSampleFrameBytes;
        size_t bytesRead = 0;
        while (bytesRead < bytesToRead)
        {
            bytesRead += fread(m_buffer.data() + bytesRead, 1,
                               bytesToRead - bytesRead, m_file);
            if (feof(m_file))
            {
                rewind(m_file);
            }
        }
        m_validBytes = static_cast<uint32_t>(bytesRead);
    }

    uint8_t* data() { return m_buffer.data(); }
    uint32_t sizeBytes() const { return m_validBytes; }

private:
    FILE* m_file = nullptr;
    std::vector<uint8_t> m_buffer;
    uint32_t m_validBytes = 0;
};

//--------------------------------------------------------------
struct Star
{
    float x, y, z;
    float r, g, b;
};

//--------------------------------------------------------------
__global__ void updateStars(Star* stars, uint8_t* framebuffer,
                            int width, int height, int pitch,
                            int numStars, float dt, uint64_t seed,
                            float mouseX, float mouseY)
{
    const int idx = blockIdx.x * blockDim.x + threadIdx.x;
    if (idx >= numStars)
    {
        return;
    }

    const float fWidth = static_cast<float>(width);
    const float fHeight = static_cast<float>(height);
    const float centerX = fWidth * 0.5f;
    const float centerY = fHeight * 0.5f;
    const float zFar = 800.0f;
    const float focalLength = 200.0f;
    const float speed = 60.0f;
    const float hideRadius = 64.0f;

    Star& s = stars[idx];

    if (s.z <= 0.0f)
    {
        curandState rng;
        curand_init(seed, idx, 0, &rng);
        s.x = (curand_uniform(&rng) * fWidth) - centerX;
        s.y = (curand_uniform(&rng) * fHeight) - centerY;
        s.z = curand_uniform(&rng) * zFar;
        s.r = curand_uniform(&rng);
        s.g = curand_uniform(&rng);
        s.b = curand_uniform(&rng);
    }
    else
    {
        s.z -= speed * dt;
    }

    const float posX = (s.x * focalLength / s.z) + centerX;
    const float posY = (s.y * focalLength / s.z) + centerY;

    const float dx = posX - (mouseX * fWidth);
    const float dy = posY - (mouseY * fHeight);
    if (sqrtf(dx * dx + dy * dy) < hideRadius)
    {
        return;
    }

    const float scale = 1.0f - (s.z / zFar);
    const float w = 3.0f * scale;
    const float h = 3.0f * scale;

    auto toU8 = [](float v) -> uint8_t
    {
        return static_cast<uint8_t>(fmaxf(0.0f, fminf(v, 1.0f)) * 255.0f);
    };

    const int minX = max(0, static_cast<int>(posX));
    const int maxX = min(width, static_cast<int>(posX + w));
    const int minY = max(0, static_cast<int>(posY));
    const int maxY = min(height, static_cast<int>(posY + h));

    for (int y = minY; y < maxY; ++y)
    {
        for (int x = minX; x < maxX; ++x)
        {
            uint8_t* pixel = framebuffer + y * pitch + x * 4;
            pixel[0] = toU8(s.b);
            pixel[1] = toU8(s.g);
            pixel[2] = toU8(s.r);
            pixel[3] = 255;
        }
    }
}

//--------------------------------------------------------------
static volatile bool g_running = true;
static std::atomic<float> g_mouseX{ 0.0f };
static std::atomic<float> g_mouseY{ 0.0f };

void signalHandler(int sig)
{
    (void)sig;
    g_running = false;
}

void logCallback(ovstream_log_level_t severity, ovstream_string_t message,
                 ovstream_string_t channel, double timestamp, void* user_data)
{
    (void)timestamp;
    (void)user_data;
    const char* lvl[] = { "DEFAULT", "VERBOSE", "INFO", "WARNING", "ERROR", "NONE" };
    constexpr size_t lvlCount = sizeof(lvl) / sizeof(lvl[0]);
    const char* levelStr =
        (severity >= 0 && static_cast<size_t>(severity) < lvlCount) ? lvl[severity] : "UNKNOWN";
    // .ptr is null-terminated per the output-string contract.
    fprintf(stderr, "[%s][%s] %s\n", levelStr, channel.ptr, message.ptr);
}

void onConnection(ovstream_server_t* server, bool connected, void* userData)
{
    (void)server;
    const char* label = static_cast<const char*>(userData);
    printf("[%s] Client %s\n", label, connected ? "connected" : "disconnected");
}

void onInput(ovstream_server_t* server, const ovstream_input_event_t* event, void* userData)
{
    (void)server;
    (void)userData;
    if (event->type == OVSTREAM_INPUT_MOUSE && event->mouse.type == OVSTREAM_MOUSE_MOVE)
    {
        const float extentsX = static_cast<float>(event->mouse.data);
        const float extentsY = static_cast<float>(event->mouse.data2);
        if (extentsX > 0.0f && extentsY > 0.0f)
        {
            g_mouseX.store(static_cast<float>(event->mouse.x) / extentsX);
            g_mouseY.store(static_cast<float>(event->mouse.y) / extentsY);
        }
    }
}

//--------------------------------------------------------------
struct ServerSpec
{
    ovstream_server_type_t type;
    uint16_t    port;       // network protocols only; 0 for SHM
    std::string streamName; // SHM only; empty for network protocols
    std::string label;
};

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
    else
    {
        return false;
    }

    return true;
}

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

    std::vector<ServerSpec> specs;
    for (int i = 1; i < argc; ++i)
    {
        ServerSpec spec;
        if (!parseServerSpec(argv[i], spec))
        {
            fprintf(stderr, "Unknown protocol: %s\n", argv[i]);
            fprintf(stderr, "Usage: %s <protocol[:detail]> [<protocol[:detail]> ...]\n", argv[0]);
            fprintf(stderr, "  Protocols: webrtc, rtsp, native, shm\n");
            return 1;
        }
        specs.push_back(spec);
    }

    if (specs.empty())
    {
        specs.push_back({ OVSTREAM_SERVER_WEBRTC, 0, "", "WebRTC:49100" });
    }

    ovstream_init_config_t initCfg = {};
    initCfg.log_callback = logCallback;
    initCfg.log_min_severity = OVSTREAM_LOG_ERROR;
    if (!OVSTREAM_OK(ovstream_initialize(&initCfg)))
    {
        fprintf(stderr, "Failed to initialize: %s\n", ovstream_get_last_error().ptr);
        return 1;
    }

    const int WIDTH = 1920;
    const int HEIGHT = 1080;
    const int NUM_STARS = 5000;

    std::vector<ServerInstance> servers;

    for (const auto& spec : specs)
    {
        ServerInstance inst;
        inst.label = std::make_unique<std::string>(spec.label);

        if (!OVSTREAM_OK(ovstream_create_server(spec.type, &inst.server)))
        {
            fprintf(stderr, "Failed to create %s: %s\n", inst.label->c_str(), ovstream_get_last_error().ptr);
            continue;
        }

        // Safe to pass label->c_str() as callback userData because the
        // unique_ptr-managed string stays at a stable heap address
        // across std::move(inst) below and across vector reallocations.
        if (!OVSTREAM_OK(ovstream_set_connection_callback(inst.server, onConnection,
                                                          const_cast<char*>(inst.label->c_str()))))
        {
            fprintf(stderr, "Failed to set connection callback on %s: %s\n",
                    inst.label->c_str(), ovstream_get_last_error().ptr);
            (void)ovstream_destroy_server(inst.server);
            continue;
        }
        // Mouse-driven star hiding works on every transport with a
        // reverse channel: WebRTC, native, and SHM. RTSP has no input
        // path so we skip the callback there.
        if (spec.type != OVSTREAM_SERVER_RTSP)
        {
            if (!OVSTREAM_OK(ovstream_set_input_callback(inst.server, onInput, nullptr)))
            {
                fprintf(stderr, "Failed to set input callback on %s: %s\n",
                        inst.label->c_str(), ovstream_get_last_error().ptr);
                (void)ovstream_destroy_server(inst.server);
                continue;
            }
        }

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

        if (spec.type == OVSTREAM_SERVER_RTSP)
        {
            if (spec.port) cfg.stream_port = spec.port;
        }
        else if (spec.type == OVSTREAM_SERVER_SHM)
        {
            if (!spec.streamName.empty())
            {
                cfg.shm.stream_name.ptr = spec.streamName.c_str();
                cfg.shm.stream_name.length = spec.streamName.size();
            }
        }
        else // WebRTC / Native
        {
            if (spec.port) cfg.webrtc.signal_port = spec.port;
        }

        if (!OVSTREAM_OK(ovstream_start(inst.server, &cfg)))
        {
            fprintf(stderr, "Failed to start %s: %s\n", inst.label->c_str(), ovstream_get_last_error().ptr);
            (void)ovstream_destroy_server(inst.server);
            continue;
        }

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
            printf("[%s] Move the mouse in the viewer window to hide stars near the cursor.\n",
                   inst.label->c_str());
        }
        else
        {
            const uint16_t defaultStreamPort =
                (spec.type == OVSTREAM_SERVER_NATIVE) ? 47999 : 47998;
            printf("[%s] signal port %u, stream port %u\n", inst.label->c_str(),
                   cfg.webrtc.signal_port ? cfg.webrtc.signal_port : 49100,
                   cfg.stream_port ? cfg.stream_port : defaultStreamPort);
            printf("[%s] Move the mouse to hide stars near the cursor.\n", inst.label->c_str());
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

    // Allocate GPU buffers.
    uint8_t* d_framebuffer = nullptr;
    size_t pitch = 0;
    cudaError_t cudaErr = cudaMallocPitch(&d_framebuffer, &pitch, WIDTH * 4, HEIGHT);
    if (cudaErr != cudaSuccess || !d_framebuffer)
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

    Star* d_stars = nullptr;
    cudaErr = cudaMalloc(&d_stars, NUM_STARS * sizeof(Star));
    if (cudaErr != cudaSuccess || !d_stars)
    {
        fprintf(stderr, "cudaMalloc failed: %s (%s)\n",
                cudaGetErrorName(cudaErr), cudaGetErrorString(cudaErr));
        cudaFree(d_framebuffer);
        for (auto& inst : servers)
        {
            (void)ovstream_stop(inst.server);
            (void)ovstream_destroy_server(inst.server);
        }
        (void)ovstream_shutdown();
        return 1;
    }
    cudaMemset(d_stars, 0, NUM_STARS * sizeof(Star));

    // Open the bundled PCM sample. Missing audio is non-fatal -- the
    // example still streams video, just silently.
    AudioSource audio;
    const std::string pcmPath = getExecutableDir() + "audio_sample_48khz.pcm";
    if (!audio.open(pcmPath))
    {
        fprintf(stderr, "Could not open audio sample: %s (streaming video only)\n",
                pcmPath.c_str());
    }

    const dim3 block(256);
    const dim3 grid((NUM_STARS + 255) / 256);

    // Dedicated stream + event reused across frames: the kernel runs
    // on `drawStream`, the event is recorded after each launch, and
    // ovstream chains its encoder ingest on the event via the
    // `frame.sync` hint. Replaces the global cudaDeviceSynchronize
    // that used to live in the loop.
    cudaStream_t drawStream = nullptr;
    cudaEvent_t  drawEvent  = nullptr;
    cudaStreamCreate(&drawStream);
    cudaEventCreateWithFlags(&drawEvent, cudaEventDisableTiming);

    // ovstream_utils::Loop is an optional frame pacer bundled with
    // ovstream. pacer.tick() blocks as needed to hit fpsTarget and
    // returns a Tick with the measured per-frame dt and a frame
    // index. The header-only C++ wrapper means no link against
    // ovstream_utils.dll -- the entire Loop implementation is inline.
    // Bundled with ovstream but not required to use it.
    ovstream_utils::Loop::Config paceCfg{};
    paceCfg.fpsTarget = 60u;
    ovstream_utils::Loop pacer(paceCfg);

    uint64_t frameCount = 0;
    while (g_running)
    {
        const auto t = pacer.tick();
        const float dt = t.deltaTimeSeconds;

        cudaMemsetAsync(d_framebuffer, 0, pitch * HEIGHT, drawStream);
        updateStars<<<grid, block, 0, drawStream>>>(d_stars, d_framebuffer,
                                                    WIDTH, HEIGHT, (int)pitch,
                                                    NUM_STARS, dt, t.frameIndex,
                                                    g_mouseX.load(), g_mouseY.load());
        cudaEventRecord(drawEvent, drawStream);

        ovstream_video_frame_t frame = {};
        frame.buffer = d_framebuffer;
        frame.width = WIDTH;
        frame.height = HEIGHT;
        frame.pitch_bytes = (uint32_t)pitch;
        frame.sync.stream     = reinterpret_cast<uintptr_t>(drawStream);
        frame.sync.wait_event = reinterpret_cast<uintptr_t>(drawEvent);

        // Refill the audio scratch buffer with this frame's worth of samples.
        audio.update(dt);

        ovstream_audio_frame_t audioFrame = {};
        audioFrame.buffer = audio.data();
        audioFrame.size_bytes = audio.sizeBytes();
        audioFrame.channels = AudioSource::kChannels;
        audioFrame.sample_rate = AudioSource::kSampleRate;
        audioFrame.bits_per_sample = 16;

        for (auto& inst : servers)
        {
            // Ignore per-frame failures (e.g. no client connected yet).
            // Enable verbose logs for diagnostics. Audio is rejected by
            // RTSP servers and only succeeds on WebRTC/native once the
            // audio stream has been established.
            (void)ovstream_stream_video(inst.server, &frame);
            if (audioFrame.size_bytes > 0)
            {
                (void)ovstream_stream_audio(inst.server, &audioFrame);
            }
        }

        printf("\rFPS: %u", t.stats.fpsCurrent);
        fflush(stdout);
        frameCount = t.frameIndex + 1;
    }

    printf("\nShutting down after %llu frames.\n",
           static_cast<unsigned long long>(frameCount));

    cudaEventDestroy(drawEvent);
    cudaStreamDestroy(drawStream);
    cudaFree(d_stars);
    cudaFree(d_framebuffer);
    for (auto& inst : servers)
    {
        (void)ovstream_stop(inst.server);
        (void)ovstream_destroy_server(inst.server);
    }
    (void)ovstream_shutdown();
    return 0;
}
