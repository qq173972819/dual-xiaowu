#include "lightacade_client.h"

#include <algorithm>
#include <cstdint>
#include <cstring>

#include <cJSON.h>
#include <esp_log.h>
#include <esp_timer.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include <http.h>
#include <network_interface.h>
#include <web_socket.h>

#include "audio/audio_codec.h"

namespace {
constexpr char kTag[] = "LightAcade";
// 播放队列上限：2s@24kHz（96KB）。突发到达时丢最旧的数据，防止内存膨胀。
constexpr size_t kMaxPlaybackSamples = 48000;
// 预缓冲：播放前先攒 500ms（12000 采样@24kHz），网络抖动 <500ms 不卡音。
constexpr size_t kPrebufferSamples = 12000;
// 预缓冲超时：服务端停止发数据 400ms 后，无论攒够与否都开始播
//（否则每轮结尾的最后一小段会被卡在缓冲里播不出来）。
constexpr int64_t kPrebufferTimeoutMs = 400;
constexpr size_t kMaxMicChunks = 25;  // 麦克风队列 500ms，超出丢最旧

std::string JsonString(cJSON* root, const char* key) {
    cJSON* value = cJSON_GetObjectItem(root, key);
    return cJSON_IsString(value) ? value->valuestring : "";
}

std::string EscapeJson(const std::string& value) {
    cJSON* item = cJSON_CreateString(value.c_str());
    char* raw = cJSON_PrintUnformatted(item);
    std::string result = raw ? raw : "\"\"";
    if (raw) cJSON_free(raw);
    cJSON_Delete(item);
    return result;
}
}  // namespace

LightAcadeClient::LightAcadeClient(NetworkInterface* network, AudioCodec* codec)
    : network_(network), codec_(codec) {}

LightAcadeClient::~LightAcadeClient() { Stop(); }

void LightAcadeClient::PublishStatus(const std::string& status) {
    if (status_callback_) status_callback_(status);
}

bool LightAcadeClient::Start(const std::string& ticket_endpoint,
                            const std::string& device_id,
                            const std::string& proxy_token,
                            const std::string& language,
                            const std::string& grade_band,
                            const std::string& voice,
                            StatusCallback status_callback) {
    if (running_.exchange(true)) return false;
    stop_requested_.store(false);
    status_callback_ = std::move(status_callback);
    PublishStatus("外教连接中…");

    auto fail = [this](const std::string& message) {
        ESP_LOGE(kTag, "%s", message.c_str());
        PublishStatus(message);
        running_.store(false);
        return false;
    };
    if (!network_ || !codec_) {
        return fail("网络或音频设备未就绪");
    }
    if (ticket_endpoint.empty()) {
        return fail("陪练服务地址未配置");
    }
    if (proxy_token.empty()) {
        return fail("陪练服务未配置访问令牌");
    }

    auto http = network_->CreateHttp(-1);
    if (!http) return fail("无法创建网络连接");
    http->SetTimeout(15000);
    http->SetKeepAlive(false);
    http->SetHeader("Content-Type", "application/json");
    http->SetHeader("Accept", "application/json");
    http->SetHeader("Authorization", "Bearer " + proxy_token);
    if (!device_id.empty()) {
        http->SetHeader("X-Device-Id", device_id);
    }
    std::string body = "{\"language\":" + EscapeJson(language) +
                       ",\"gradeBand\":" + EscapeJson(grade_band) +
                       ",\"voice\":" + EscapeJson(voice) + "}";
    http->SetContent(std::move(body));
    if (!http->Open("POST", ticket_endpoint)) {
        return fail("陪练服务器连接失败");
    }
    const int status = http->GetStatusCode();
    const std::string response = http->ReadAll();
    if (status < 200 || status >= 300) {
        // 打印服务端原始状态码与响应体，便于定位 401/403 的具体原因
        //（令牌不匹配 / 设备 MAC 未在白名单 / 服务端配置缺失等）。
        ESP_LOGE(kTag, "Ticket endpoint HTTP %d: %.*s", status,
                 static_cast<int>(std::min<size_t>(response.size(), 256)),
                 response.c_str());
        if (status == 429) return fail("今日陪练额度已用完或服务繁忙");
        if (status == 401) return fail("陪练令牌无效(401)");
        if (status == 403) return fail("设备未授权使用陪练(403)");
        return fail("陪练服务暂不可用 (HTTP " + std::to_string(status) + ")");
    }

    cJSON* root = cJSON_ParseWithLength(response.data(), response.size());
    if (!root) return fail("陪练服务器返回格式错误");
    std::string ws_url = JsonString(root, "wsUrl");
    if (ws_url.empty()) {
        const std::string ticket = JsonString(root, "ticket");
        const std::string ws_path = JsonString(root, "wsPath");
        if (!ticket.empty() && !ws_path.empty()) {
            ws_url = "wss://api.aioneschool.com" + ws_path + "?ticket=" + ticket;
        }
    }
    cJSON_Delete(root);
    if (ws_url.empty()) return fail("陪练服务器未返回会话地址");

    websocket_ = network_->CreateWebSocket(-1);
    if (!websocket_) return fail("无法创建实时语音连接");
    websocket_->SetReceiveBufferSize(16 * 1024);
    websocket_->OnData([this](const char* data, size_t len, bool binary) {
        if (binary) {
            if (len < sizeof(int16_t)) return;
            std::vector<int16_t> pcm(len / sizeof(int16_t));
            std::memcpy(pcm.data(), data, pcm.size() * sizeof(int16_t));
            {
                std::lock_guard<std::mutex> lock(playback_mutex_);
                // 总量上限：超出丢最旧，而不是按块数（帧长不固定）
                while (playback_samples_ + pcm.size() > kMaxPlaybackSamples &&
                       !playback_queue_.empty()) {
                    playback_samples_ -= playback_queue_.front().size();
                    playback_queue_.pop_front();
                }
                playback_samples_ += pcm.size();
                last_playback_data_ms_ = esp_timer_get_time() / 1000;
                playback_queue_.push_back(std::move(pcm));
            }
            return;
        }
        cJSON* event = cJSON_ParseWithLength(data, len);
        if (!event) return;
        const std::string type = JsonString(event, "type");
        if (type == "transcript") {
            const std::string speaker = JsonString(event, "speaker");
            const std::string text = JsonString(event, "text");
            if (!text.empty()) PublishStatus((speaker == "student" ? "我：" : "外教：") + text);
        } else if (type == "turn_start") {
            // 只在外教说话时显示提示，学生轮次不再弹出"请说英语"提示。
            if (JsonString(event, "speaker") != "student") {
                PublishStatus("外教正在说…");
            }
        } else if (type == "interrupted") {
            std::lock_guard<std::mutex> lock(playback_mutex_);
            playback_queue_.clear();
        } else if (type == "end") {
            end_received_.store(true);
            ESP_LOGI(kTag, "服务端下发 end 帧正常结束，会话时长 %lld 秒",
                     (long long)(esp_timer_get_time() / 1000 - session_start_ms_) / 1000);
            // 仍发送状态回调（回调方凭"结束"触发 RestoreAudioService），
            // 但 SetSpeakingStatus 会清空显示，屏幕回到居中"小悟AI"。
            PublishStatus("本次英语陪练已结束");
            stop_requested_.store(true);
        } else if (type == "error") {
            // 完整打印服务端错误内容（错误码/原因），用于区分固件与服务端问题
            ESP_LOGE(kTag, "服务端下发 error 帧: %.*s",
                     static_cast<int>(len), data);
            PublishStatus("陪练服务发生错误");
            stop_requested_.store(true);
        } else {
            ESP_LOGW(kTag, "未知服务端事件: %.*s", static_cast<int>(len), data);
        }
        cJSON_Delete(event);
    });
    websocket_->OnDisconnected([this]() {
        // 无 end/error 帧即断开：大概率服务端/中间代理或网络主动断连
        ESP_LOGE(kTag, "WebSocket 连接断开：会话时长 %lld 秒，end帧=%d，TCP最后错误=%d",
                 (long long)(esp_timer_get_time() / 1000 - session_start_ms_) / 1000,
                 end_received_.load() ? 1 : 0,
                 websocket_ ? websocket_->GetLastError() : 0);
        stop_requested_.store(true);
    });
    websocket_->OnError([this](int error) {
        ESP_LOGE(kTag, "WebSocket error: %d", error);
        PublishStatus("实时语音连接中断");
        stop_requested_.store(true);
    });
    if (!websocket_->Connect(ws_url.c_str())) {
        websocket_.reset();
        return fail("实时语音连接失败");
    }
    session_start_ms_ = esp_timer_get_time() / 1000;
    ESP_LOGI(kTag, "实时语音连接已建立，会话计时开始");

    codec_->EnableInput(true);
    codec_->EnableOutput(true);
    if (xTaskCreate(MicTaskStatic, "lacade_mic", 4096, this, 6, &mic_task_) != pdPASS) {
        websocket_.reset();
        return fail("无法启动麦克风任务");
    }
    if (xTaskCreate(StreamTask, "lightacade", 6144, this, 5, &stream_task_) != pdPASS) {
        stop_requested_.store(true);  // 让麦克风任务随之退出
        websocket_.reset();
        return fail("无法启动陪练音频任务");
    }
    PublishStatus("已连接，外教即将主动开场…");
    return true;
}

void LightAcadeClient::Stop() {
    if (!running_.load()) return;
    stop_requested_.store(true);
    if (websocket_ && websocket_->IsConnected()) {
        websocket_->Send("{\"type\":\"stop\"}");
    }
}

void LightAcadeClient::StreamTask(void* argument) {
    static_cast<LightAcadeClient*>(argument)->RunStream();
}

void LightAcadeClient::MicTaskStatic(void* argument) {
    static_cast<LightAcadeClient*>(argument)->RunMic();
}

// 持续排空 ADC：20ms/块入队（上限 500ms，超出丢最旧）。
// 只有一个读者（StreamTask），队首永远是最新可用的音频。
void LightAcadeClient::RunMic() {
    std::vector<int16_t> chunk(480);  // 20ms @24kHz
    while (!stop_requested_.load()) {
        if (!codec_->InputData(chunk)) {
            vTaskDelay(pdMS_TO_TICKS(10));
            continue;
        }
        std::vector<int16_t> copy(chunk);
        {
            std::lock_guard<std::mutex> lock(mic_mutex_);
            if (mic_queue_.size() >= kMaxMicChunks) mic_queue_.pop_front();
            mic_queue_.push_back(std::move(copy));
        }
    }
    mic_task_ = nullptr;
    vTaskDelete(nullptr);
}

void LightAcadeClient::RunStream() {
    // 麦克风块由 MicTask 持续排空 ADC 后入队（20ms@24kHz = 480 采样），
    // 这里只做出队 → 重采样到 16kHz（320 采样）→ 按半双工规则上行。
    std::vector<int16_t> input_16k(320);
    std::vector<int16_t> silence_16k(320, 0);
    std::vector<int16_t> mic_chunk;
    // 设备扬声器与麦克风距离近且无回声消除（AEC）：外教播放期间麦克风拾到的
    // 全是扬声器声音，若照发上行，服务端 VAD 会误判"学生在说话"，外教反复
    // 被打断、抢话。改为半双工：外教发言及余音期间上行静音帧。
    // 注意：协议要求上行连续推送（静音也推），一帧不发会被服务端判定上行
    // 中断/空闲，表现为越说越卡直至断开。因此"抑制回声"必须以"发静音帧"
    // 实现，绝不能停发。
    constexpr int64_t kEchoHangoverMs = 600;  // 扬声器 DMA 排空 + 余音衰减
    constexpr size_t kPieceSamples = 1440;    // 送播分片 ~60ms@24kHz，避免
                                              // 大块音频长时间阻塞上行节奏
    int64_t last_upstream_ms = 0;
    int64_t last_playback_ms = -kEchoHangoverMs - 1;
    bool send_failed = false;  // 上行发送失败退出（区别于服务端主动断开）
    bool prebuffer = true;  // 播放前先攒 kPrebufferSamples，抗网络抖动
    std::vector<int16_t> current;   // 正在送播的服务端音频块
    size_t current_offset = 0;
    std::vector<int16_t> piece;
    while (!stop_requested_.load() && websocket_ && websocket_->IsConnected()) {
        const int64_t now_ms = esp_timer_get_time() / 1000;
        const bool in_flight = current_offset < current.size();
        // ---- 下行出队：预缓冲决策 ----
        if (!in_flight) {
            std::vector<int16_t> next;
            bool queue_empty = false;
            {
                std::lock_guard<std::mutex> lock(playback_mutex_);
                if (!playback_queue_.empty()) {
                    const bool enough =
                        playback_samples_ >= kPrebufferSamples ||
                        now_ms - last_playback_data_ms_ > kPrebufferTimeoutMs;
                    if (!prebuffer || enough) {
                        prebuffer = false;
                        next = std::move(playback_queue_.front());
                        playback_queue_.pop_front();
                        playback_samples_ -= next.size();
                    }
                } else {
                    queue_empty = true;
                }
            }
            if (queue_empty) {
                prebuffer = true;  // 队列空：为下一段重新预缓冲
            } else if (!next.empty()) {
                current = std::move(next);
                current_offset = 0;
            }
        }
        bool teacher_talking =
            current_offset < current.size() ||
            (now_ms - last_playback_ms) < kEchoHangoverMs;
        if (!teacher_talking) {
            std::lock_guard<std::mutex> lock(playback_mutex_);
            // 待播音频已到但尚未开播（预缓冲中）也视为外教发言；
            // 队列为空（含预缓冲等待新数据）时不抑制，学生可正常发言
            teacher_talking = !playback_queue_.empty();
        }
        bool did_work = false;
        // ---- 上行：20ms 一帧（16kHz 下 320 采样）----
        if (now_ms - last_upstream_ms >= 20) {
            last_upstream_ms = now_ms;
            bool has_mic = false;
            {
                std::lock_guard<std::mutex> lock(mic_mutex_);
                if (!mic_queue_.empty()) {
                    if (teacher_talking) {
                        // 外教发言期间：清空丢弃（全部是回声污染的旧音频），
                        // 防止余音结束后把积压的回声当学生语音发出
                        mic_queue_.clear();
                    } else {
                        mic_chunk = std::move(mic_queue_.front());
                        mic_queue_.pop_front();
                        has_mic = true;
                    }
                }
            }
            did_work = true;
            if (teacher_talking || !has_mic) {
                // 外教发言期间发静音抑制回声；无麦音时发静音保持连续上行
                if (!websocket_->Send(silence_16k.data(),
                                      silence_16k.size() * sizeof(int16_t), true)) {
                    ESP_LOGE(kTag, "上行静音帧发送失败，TCP最后错误=%d",
                             websocket_->GetLastError());
                    send_failed = true;
                    break;
                }
            } else {
                for (size_t i = 0; i < input_16k.size(); ++i) {
                    const size_t source = i * 3;
                    input_16k[i] = static_cast<int16_t>(
                        (static_cast<int32_t>(mic_chunk[source / 2]) +
                         static_cast<int32_t>(mic_chunk[(source + 1) / 2])) / 2);
                }
                if (!websocket_->Send(input_16k.data(),
                                      input_16k.size() * sizeof(int16_t), true)) {
                    ESP_LOGE(kTag, "上行音频发送失败，TCP最后错误=%d",
                             websocket_->GetLastError());
                    send_failed = true;
                    break;
                }
            }
        }
        // ---- 下行送播：按 ~60ms 分片（i2s 写阻塞即实时节拍）----
        if (current_offset < current.size()) {
            const size_t take = std::min(kPieceSamples, current.size() - current_offset);
            piece.assign(current.begin() + current_offset,
                         current.begin() + current_offset + take);
            current_offset += take;
            codec_->OutputData(piece);
            last_playback_ms = esp_timer_get_time() / 1000;
            did_work = true;
        }
        if (!did_work) vTaskDelay(pdMS_TO_TICKS(1));  // 让位给 LVGL UI 渲染
    }
    // 会话结束诊断汇总：判定"固件问题"还是"接口问题"
    // - end帧=1：服务端按策略正常结束（接口行为，看是否为固定时长超时策略）
    // - end帧=0 且 发送失败=1：本机 TCP 上行失败（网络/链路问题）
    // - end帧=0 且 WS断开、TCP错误=0：服务端/中间代理主动断连（接口问题，
    //   若每次会话时长都固定在 N 秒，基本是代理/网关超时配置，如 nginx 60s）
    // - 最后收到音频距今很大但上行一直正常：服务端早就不发数据了（服务端停滞）
    int64_t last_audio_age_ms = -1;
    {
        std::lock_guard<std::mutex> lock(playback_mutex_);
        if (last_playback_data_ms_ > 0) {
            last_audio_age_ms = esp_timer_get_time() / 1000 - last_playback_data_ms_;
        }
        playback_queue_.clear();
        playback_samples_ = 0;
    }
    ESP_LOGW(kTag,
             "会话结束汇总：时长 %lld 秒 | end帧=%d | 上行发送失败=%d | "
             "WS已断开=%d | TCP最后错误=%d | 最后收到音频距今 %lld ms",
             (long long)(esp_timer_get_time() / 1000 - session_start_ms_) / 1000,
             end_received_.load() ? 1 : 0,
             send_failed ? 1 : 0,
             (websocket_ && !websocket_->IsConnected()) ? 1 : 0,
             websocket_ ? websocket_->GetLastError() : 0,
             (long long)last_audio_age_ms);
    if (websocket_) websocket_->Close();
    websocket_.reset();
    {
        std::lock_guard<std::mutex> lock(mic_mutex_);
        mic_queue_.clear();
    }
    // 等麦克风任务退出（≤一个 20ms 读块），避免恢复音频服务后双读者竞争编解码器
    for (int i = 0; i < 50 && mic_task_ != nullptr; ++i) {
        vTaskDelay(pdMS_TO_TICKS(10));
    }
    stream_task_ = nullptr;
    running_.store(false);
    // 仍发送状态回调触发 RestoreAudioService，屏幕不显示"已结束"。
    PublishStatus("英语陪练已结束，长按返回首页");
    vTaskDelete(nullptr);
}
