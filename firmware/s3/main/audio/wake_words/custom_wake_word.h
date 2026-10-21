#ifndef CUSTOM_WAKE_WORD_H
#define CUSTOM_WAKE_WORD_H

#include <esp_attr.h>
#include <esp_mn_iface.h>
#include <esp_mn_models.h>
#include <model_path.h>

#include <deque>
#include <string>
#include <vector>
#include <functional>
#include <mutex>
#include <condition_variable>
#include <atomic>

#include "audio_codec.h"
#include "wake_word.h"

class CustomWakeWord : public WakeWord {
public:
    CustomWakeWord();
    ~CustomWakeWord();

    bool Initialize(AudioCodec* codec, srmodel_list_t* models_list);
    void Feed(const std::vector<int16_t>& data);
    void OnWakeWordDetected(std::function<void(const std::string& wake_word)> callback);
    void Start();
    void Stop();
    size_t GetFeedSize();
    void EncodeWakeWordData();
    bool GetWakeWordOpus(std::vector<uint8_t>& opus);
    const std::string& GetLastDetectedWakeWord() const { return last_detected_wake_word_; }

private:
    struct Command {
        std::string command;
        std::string text;
        std::string action;
    };

    // 从编译期 Kconfig 字符串构建命令表；'ni hao xiao wu;hu jiao xiao wu'
    // 这类分号分隔的写法可注册多条唤醒词，单条时行为不变。
    std::deque<Command> ParseCommandList(const char* pinyin, const char* display);

    // multinet 相关成员变量
    esp_mn_iface_t* multinet_ = nullptr;
    model_iface_data_t* multinet_model_data_ = nullptr;
    srmodel_list_t *models_ = nullptr;
    char* mn_name_ = nullptr;
    std::string language_ = "cn";
    int duration_ = 3000;
    float threshold_ = 0.2;
    std::deque<Command> commands_;
 
    std::function<void(const std::string& wake_word)> wake_word_detected_callback_;
    AudioCodec* codec_ = nullptr;
    std::string last_detected_wake_word_;
    std::atomic<bool> running_ = false;
    std::vector<int16_t> input_buffer_;
    std::mutex input_buffer_mutex_;

    TaskHandle_t wake_word_encode_task_ = nullptr;
    StaticTask_t* wake_word_encode_task_buffer_ = nullptr;
    StackType_t* wake_word_encode_task_stack_ = nullptr;
    std::deque<std::vector<int16_t>> wake_word_pcm_;
    std::deque<std::vector<uint8_t>> wake_word_opus_;
    std::mutex wake_word_mutex_;
    std::condition_variable wake_word_cv_;

    // 唤醒排查电平表：每 5s 打一次麦克风峰值电平，恒为 0 即 I2S/麦克风
    // 引脚配置有问题（而不是唤醒词没喊对）。
    int16_t mic_level_peak_ = 0;
    int64_t mic_level_last_log_us_ = 0;
    // 唤醒诊断：5s 窗口内 MultiNet 处于 DETECTING 的 chunk 数（>0 说明音频
    // 流确实进了识别引擎）、窗口内平均幅值、最近一次识别命中的概率。
    int detecting_chunks_ = 0;
    int64_t mic_level_sum_ = 0;
    int mic_level_samples_ = 0;
    float last_prob_ = 0.0f;
    // 削波计数（|sample| > 30000）：占比高说明麦克风增益过大，语音被削平，
    // 表现为「有声音但永远喊不醒」。
    int mic_level_clip_ = 0;
    // 总喂入 chunk 数（用于算 DETECTING 占比）。
    int total_chunks_ = 0;

    void StoreWakeWordData(const std::vector<int16_t>& data);
    void ParseWakenetModelConfig();
};

#endif
