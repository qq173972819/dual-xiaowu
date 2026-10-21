#pragma once

#include <atomic>
#include <functional>
#include <memory>
#include <mutex>
#include <deque>
#include <string>
#include <vector>

#include <freertos/FreeRTOS.h>
#include <freertos/task.h>

class AudioCodec;
class NetworkInterface;
class WebSocket;

// Runs a LightAcade live-practice session using a short-lived ticket issued
// by the product backend. The Partner API key must never be stored here.
class LightAcadeClient {
public:
    using StatusCallback = std::function<void(const std::string&)>;

    LightAcadeClient(NetworkInterface* network, AudioCodec* codec);
    ~LightAcadeClient();

    bool Start(const std::string& ticket_endpoint,
               const std::string& device_id,
               const std::string& proxy_token,
               const std::string& language,
               const std::string& grade_band,
               const std::string& voice,
               StatusCallback status_callback);
    void Stop();
    bool IsRunning() const { return running_.load(); }

private:
    NetworkInterface* network_;
    AudioCodec* codec_;
    std::unique_ptr<WebSocket> websocket_;
    std::atomic<bool> running_{false};
    std::atomic<bool> stop_requested_{false};
    StatusCallback status_callback_;
    TaskHandle_t stream_task_ = nullptr;
    std::mutex playback_mutex_;
    std::deque<std::vector<int16_t>> playback_queue_;
    // 播放队列总采样数（24kHz 单声道），用于预缓冲判断与总量上限
    size_t playback_samples_ = 0;
    int64_t last_playback_data_ms_ = 0;  // 最近一次收到服务端音频的时刻
    // 麦克风专用任务：持续排空 ADC（20ms/块）。若在发送循环里顺手读麦，
    // 播放阻塞期间输入会积压数秒的回声污染音频，恢复上传后全部发给服务端，
    // 表现为严重回声。独立任务保证任何时刻缓冲里只有"新鲜"音频。
    TaskHandle_t mic_task_ = nullptr;
    std::mutex mic_mutex_;
    std::deque<std::vector<int16_t>> mic_queue_;

    static void StreamTask(void* argument);
    void RunStream();
    static void MicTaskStatic(void* argument);
    void RunMic();
    void PublishStatus(const std::string& status);

    // 会话诊断：区分"服务端正常结束"与"网络/服务端异常断开"
    std::atomic<bool> end_received_{false};
    int64_t session_start_ms_ = 0;  // Start() 成功建连后设置
};
