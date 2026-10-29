#include "custom_wake_word.h"
#include "audio_service.h"
#include "system_info.h"
#include "assets.h"
#include "settings.h"

#include <esp_log.h>
#include <esp_mn_iface.h>
#include <esp_mn_models.h>
#include <esp_mn_speech_commands.h>
#include <cJSON.h>

#define TAG "CustomWakeWord"

namespace {

// Split on ';', trim whitespace, drop empty pieces.
std::vector<std::string> SplitSemicolon(const char* s) {
    std::vector<std::string> out;
    if (s == nullptr) return out;
    std::string cur;
    for (const char* p = s; *p != '\0'; ++p) {
        if (*p == ';' || *p == '；') {
            if (!cur.empty()) out.push_back(cur);
            cur.clear();
        } else {
            cur.push_back(*p);
        }
    }
    if (!cur.empty()) out.push_back(cur);
    return out;
}

std::string Trim(const std::string& s) {
    const char* ws = " \t\r\n";
    const size_t b = s.find_first_not_of(ws);
    if (b == std::string::npos) return std::string();
    const size_t e = s.find_last_not_of(ws);
    return s.substr(b, e - b + 1);
}

}  // namespace

CustomWakeWord::CustomWakeWord()
    : wake_word_pcm_(), wake_word_opus_() {
}

CustomWakeWord::~CustomWakeWord() {
    if (multinet_model_data_ != nullptr && multinet_ != nullptr) {
        multinet_->destroy(multinet_model_data_);
        multinet_model_data_ = nullptr;
    }

    if (wake_word_encode_task_stack_ != nullptr) {
        heap_caps_free(wake_word_encode_task_stack_);
    }

    if (wake_word_encode_task_buffer_ != nullptr) {
        heap_caps_free(wake_word_encode_task_buffer_);
    }

    if (models_ != nullptr) {
        esp_srmodel_deinit(models_);
    }
}

void CustomWakeWord::ParseWakenetModelConfig() {
    // Read index.json
    auto& assets = Assets::GetInstance();
    void* ptr = nullptr;
    size_t size = 0;
    if (!assets.GetAssetData("index.json", ptr, size)) {
        ESP_LOGE(TAG, "Failed to read index.json");
        return;
    }
    cJSON* root = cJSON_ParseWithLength(static_cast<char*>(ptr), size);
    if (root == nullptr) {
        ESP_LOGE(TAG, "Failed to parse index.json");
        return;
    }
    cJSON* multinet_model = cJSON_GetObjectItem(root, "multinet_model");
    if (cJSON_IsObject(multinet_model)) {
        cJSON* language = cJSON_GetObjectItem(multinet_model, "language");
        cJSON* duration = cJSON_GetObjectItem(multinet_model, "duration");
        cJSON* threshold = cJSON_GetObjectItem(multinet_model, "threshold");
        cJSON* commands = cJSON_GetObjectItem(multinet_model, "commands");
        if (cJSON_IsString(language)) {
            language_ = language->valuestring;
        }
        if (cJSON_IsNumber(duration)) {
            duration_ = duration->valueint;
        }
        if (cJSON_IsNumber(threshold)) {
            threshold_ = threshold->valuedouble;
        }
        if (cJSON_IsArray(commands)) {
            for (int i = 0; i < cJSON_GetArraySize(commands); i++) {
                cJSON* command = cJSON_GetArrayItem(commands, i);
                if (cJSON_IsObject(command)) {
                    cJSON* command_name = cJSON_GetObjectItem(command, "command");
                    cJSON* text = cJSON_GetObjectItem(command, "text");
                    cJSON* action = cJSON_GetObjectItem(command, "action");
                    if (cJSON_IsString(command_name) && cJSON_IsString(text) && cJSON_IsString(action)) {
                        // A single entry may still carry several wake words
                        // separated by ';' (assets generated before the build
                        // script learned to split them). Split here as well, so a
                        // combined string can never reach MultiNet - it crashes
                        // inside phrase_init_from_phoneme_cn().
                        const auto words = SplitSemicolon(command_name->valuestring);
                        const auto texts = SplitSemicolon(text->valuestring);
                        const std::string fallback_text =
                            texts.empty() ? std::string(command_name->valuestring) : texts[0];
                        for (size_t k = 0; k < words.size(); ++k) {
                            const std::string w = Trim(words[k]);
                            if (w.empty()) continue;
                            const std::string t =
                                k < texts.size() ? Trim(texts[k]) : fallback_text;
                            commands_.push_back({w, t, action->valuestring});
                            ESP_LOGI(TAG, "Command: %s, Text: %s, Action: %s",
                                     w.c_str(), t.c_str(), action->valuestring);
                        }
                    }
                }
            }
        }
    }
    cJSON_Delete(root);
}


// Build the multinet command table from the compile-time Kconfig strings.
// Both strings may hold several entries separated by ';'. When the two lists
// have different lengths the shorter one falls back to its first entry, so a
// single pair still works exactly like before.
std::deque<CustomWakeWord::Command> CustomWakeWord::ParseCommandList(const char* pinyin,
                                                                     const char* display) {
    std::deque<Command> cmds;
    std::vector<std::string> words;
    for (const auto& raw : SplitSemicolon(pinyin)) {
        const std::string w = Trim(raw);
        if (!w.empty()) words.push_back(w);
    }
    std::vector<std::string> texts;
    for (const auto& raw : SplitSemicolon(display)) {
        const std::string t = Trim(raw);
        if (!t.empty()) texts.push_back(t);
    }
    if (words.empty()) return cmds;
    const std::string fallback_text = texts.empty() ? words[0] : texts[0];
    for (size_t i = 0; i < words.size(); ++i) {
        const std::string text = (i < texts.size()) ? texts[i] : fallback_text;
        cmds.push_back({words[i], text, "wake"});
    }
    return cmds;
}

bool CustomWakeWord::Initialize(AudioCodec* codec, srmodel_list_t* models_list) {
    codec_ = codec;
    commands_.clear();

    if (models_list == nullptr) {
        language_ = "cn";
        models_ = esp_srmodel_init("model");
#ifdef CONFIG_CUSTOM_WAKE_WORD
        threshold_ = CONFIG_CUSTOM_WAKE_WORD_THRESHOLD / 100.0f;
        // NVS 覆盖：MCP 工具 self.audio.set_wake_threshold 可现场调灵敏度
        // （无需重新编译固件）。值越小越灵敏、越容易误触发。重启后生效。
        {
            Settings s("ww", /*read_write=*/false);
            const int saved = s.GetInt("threshold", -1);
            if (saved > 0 && saved <= 100) threshold_ = saved / 100.0f;
        }
        // Multiple wake words are supported: separate the pinyin strings and the
        // display strings with ';' in sdkconfig, e.g.
        //   CONFIG_CUSTOM_WAKE_WORD="ni hao xiao wu;hu jiao xiao wu"
        //   CONFIG_CUSTOM_WAKE_WORD_DISPLAY="你好小悟;呼叫小悟"
        // A single entry (no ';') behaves exactly as before.
        commands_ = ParseCommandList(CONFIG_CUSTOM_WAKE_WORD,
                                     CONFIG_CUSTOM_WAKE_WORD_DISPLAY);
#endif
    } else {
        models_ = models_list;
        ParseWakenetModelConfig();
    }

    if (models_ == nullptr || models_->num == -1) {
        ESP_LOGE(TAG, "Failed to initialize wakenet model");
        return false;
    }

    // 初始化 multinet (命令词识别)
    mn_name_ = esp_srmodel_filter(models_, ESP_MN_PREFIX, language_.c_str());
    if (mn_name_ == nullptr) {
        ESP_LOGW(TAG, "Language '%s' multinet not found, falling back to any multinet model", language_.c_str());
        mn_name_ = esp_srmodel_filter(models_, ESP_MN_PREFIX, NULL);
    }
    if (mn_name_ == nullptr) {
        ESP_LOGE(TAG, "Failed to initialize multinet, mn_name is nullptr");
        ESP_LOGI(TAG, "Please refer to https://pcn7cs20v8cr.feishu.cn/wiki/CpQjwQsCJiQSWSkYEvrcxcbVnwh to add custom wake word");
        return false;
    }

    multinet_ = esp_mn_handle_from_name(mn_name_);
    multinet_model_data_ = multinet_->create(mn_name_, duration_);
    multinet_->set_det_threshold(multinet_model_data_, threshold_);
    ESP_LOGI(TAG, "唤醒阈值 threshold=%.2f（编译期 %d，可用 MCP "
                  "self.audio.set_wake_threshold 现场调整，重启生效）",
             threshold_, CONFIG_CUSTOM_WAKE_WORD_THRESHOLD);
    esp_mn_commands_clear();
    for (int i = 0; i < commands_.size(); i++) {
        esp_mn_commands_add(i + 1, commands_[i].command.c_str());
    }
    esp_mn_commands_update();
    
    multinet_->print_active_speech_commands(multinet_model_data_);
    return true;
}

void CustomWakeWord::OnWakeWordDetected(std::function<void(const std::string& wake_word)> callback) {
    wake_word_detected_callback_ = callback;
}

void CustomWakeWord::Start() {
    running_ = true;
}

void CustomWakeWord::Stop() {
    running_ = false;

    std::lock_guard<std::mutex> lock(input_buffer_mutex_);
    input_buffer_.clear();
}

void CustomWakeWord::Feed(const std::vector<int16_t>& data) {
    if (multinet_model_data_ == nullptr) {
        return;
    }

    // 麦克风电平表：每 5s 输出一次峰值。峰值长期为 0/接近 0 说明 I2S 或
    // 麦克风引脚接错（无数据），而不是唤醒词模型没匹配上。
    {
        const int64_t now_us = esp_timer_get_time();
        for (int16_t v : data) {
            const int32_t a = v < 0 ? -static_cast<int32_t>(v) : v;
            if (a > mic_level_peak_) mic_level_peak_ = static_cast<int16_t>(a);
            mic_level_sum_ += a;
            ++mic_level_samples_;
            if (a > 30000) ++mic_level_clip_;
        }
        if (now_us - mic_level_last_log_us_ >= 5000000) {
            const int avg = mic_level_samples_ > 0
                                ? static_cast<int>(mic_level_sum_ / mic_level_samples_)
                                : 0;
            const int clip_pct = mic_level_samples_ > 0
                                     ? static_cast<int>(mic_level_clip_ * 100 /
                                                        mic_level_samples_)
                                     : 0;
            // 判读提示：
            //   peak<200               -> 麦克风无数据（I2S/引脚问题）
            //   avg/peak > 0.4 或 clip>1% -> 增益过大/削波，语音被压平
            //   detecting 满格但 prob=0 -> 一直被当成"有语音"，噪声抬太高
            const char* hint = "";
            // VAD 锁定：绝大多数 chunk 都处在"正在识别"状态、几乎没有静音段，说明
            // 麦克风输入被持续的非语音能量占满（增益过大削波 / 喇叭声学串扰 / 环境
            // 持续噪声）。这会卡住唤醒词引擎——永远在"累积"、永远不出 DETECTED，
            // 表现为「有声音但呼叫小悟没反应」。优先降麦克风增益（input_gain）。
            const int detect_ratio = (total_chunks_ > 0)
                                        ? detecting_chunks_ * 100 / total_chunks_
                                        : 0;
            if (detect_ratio >= 90 && mic_level_peak_ > 6000) {
                hint = "(VAD锁定!麦克风持续有声音→唤醒词被压制，先降麦克风增益/增大喇叭间距)";
            } else if (detect_ratio >= 90) {
                hint = "(VAD长期 detecting 但电平不高：环境噪声/喇叭串音，安静环境下测试)";
            } else if (mic_level_peak_ < 200) {
                hint = "(疑似麦克风无数据!)";
            } else if (clip_pct >= 1) {
                hint = "(削波!麦克风增益过大)";
            } else if (mic_level_peak_ > 0 && avg * 10 / mic_level_peak_ > 4) {
                hint = "(底噪偏高，疑似增益过大/环境噪声)";
            }
            ESP_LOGI(TAG,
                     "Mic level check (5s): peak=%d avg=%d clip=%d%% "
                     "detecting=%d/%d last_prob=%.2f %s",
                     mic_level_peak_, avg, clip_pct, detecting_chunks_,
                     total_chunks_, last_prob_, hint);
            mic_level_peak_ = 0;
            mic_level_sum_ = 0;
            mic_level_samples_ = 0;
            mic_level_clip_ = 0;
            detecting_chunks_ = 0;
            total_chunks_ = 0;
            mic_level_last_log_us_ = now_us;
        }
    }

    std::lock_guard<std::mutex> lock(input_buffer_mutex_);
    // Check running state inside lock to avoid TOCTOU race with Stop()
    if (!running_) {
        return;
    }

    // If input channels is 2, we need to fetch the left channel data
    if (codec_->input_channels() == 2) {
        for (size_t i = 0; i < data.size(); i += 2) {
            input_buffer_.push_back(data[i]);
        }
    } else {
        input_buffer_.insert(input_buffer_.end(), data.begin(), data.end());
    }
    
    int chunksize = multinet_->get_samp_chunksize(multinet_model_data_);
    while (input_buffer_.size() >= chunksize) {
        std::vector<int16_t> chunk(input_buffer_.begin(), input_buffer_.begin() + chunksize);
        StoreWakeWordData(chunk);
        
        esp_mn_state_t mn_state = multinet_->detect(multinet_model_data_, chunk.data());
        ++total_chunks_;
        
        if (mn_state == ESP_MN_STATE_DETECTED) {
            esp_mn_results_t *mn_result = multinet_->get_results(multinet_model_data_);
            for (int i = 0; i < mn_result->num && running_; i++) {
                ESP_LOGI(TAG, "Custom wake word detected: command_id=%d, string=%s, prob=%f", 
                        mn_result->command_id[i], mn_result->string, mn_result->prob[i]);
                if (mn_result->prob[i] > last_prob_) last_prob_ = mn_result->prob[i];
                auto& command = commands_[mn_result->command_id[i] - 1];
                if (command.action == "wake") {
                    last_detected_wake_word_ = command.text;
                    running_ = false;
                    input_buffer_.clear();
                    
                    if (wake_word_detected_callback_) {
                        wake_word_detected_callback_(last_detected_wake_word_);
                    }
                }
            }
            multinet_->clean(multinet_model_data_);
        } else if (mn_state == ESP_MN_STATE_DETECTING) {
            // 模型正在累积语音（未出结果）。该计数 >0 说明音频流确实进了
            // MultiNet；长期为 0 说明前端没把语音喂进来（AFE/VAD/I2S 问题）。
            ++detecting_chunks_;
        } else if (mn_state == ESP_MN_STATE_TIMEOUT) {
            ESP_LOGD(TAG, "Command word detection timeout, cleaning state");
            multinet_->clean(multinet_model_data_);
        }
        
        if (!running_) {
            break;
        }
        input_buffer_.erase(input_buffer_.begin(), input_buffer_.begin() + chunksize);
    }
}

size_t CustomWakeWord::GetFeedSize() {
    if (multinet_model_data_ == nullptr) {
        return 0;
    }
    return multinet_->get_samp_chunksize(multinet_model_data_);
}

void CustomWakeWord::StoreWakeWordData(const std::vector<int16_t>& data) {
    // store audio data to wake_word_pcm_
    wake_word_pcm_.push_back(data);
    // keep about 2 seconds of data, detect duration is 30ms (sample_rate == 16000, chunksize == 512)
    while (wake_word_pcm_.size() > 2000 / 30) {
        wake_word_pcm_.pop_front();
    }
}

void CustomWakeWord::EncodeWakeWordData() {
    const size_t stack_size = 4096 * 7;
    wake_word_opus_.clear();
    if (wake_word_encode_task_stack_ == nullptr) {
        wake_word_encode_task_stack_ = (StackType_t*)heap_caps_malloc(stack_size, MALLOC_CAP_SPIRAM);
        assert(wake_word_encode_task_stack_ != nullptr);
    }
    if (wake_word_encode_task_buffer_ == nullptr) {
        wake_word_encode_task_buffer_ = (StaticTask_t*)heap_caps_malloc(sizeof(StaticTask_t), MALLOC_CAP_INTERNAL);
        assert(wake_word_encode_task_buffer_ != nullptr);
    }

    wake_word_encode_task_ = xTaskCreateStatic([](void* arg) {
        auto this_ = (CustomWakeWord*)arg;
        {
            auto start_time = esp_timer_get_time();
            // Create encoder
            esp_opus_enc_config_t opus_enc_cfg = AS_OPUS_ENC_CONFIG();
            void* encoder_handle = nullptr;
            auto ret = esp_opus_enc_open(&opus_enc_cfg, sizeof(esp_opus_enc_config_t), &encoder_handle);
            if (encoder_handle == nullptr) {
                ESP_LOGE(TAG, "Failed to create audio encoder, error code: %d", ret);
                std::lock_guard<std::mutex> lock(this_->wake_word_mutex_);
                this_->wake_word_opus_.push_back(std::vector<uint8_t>());
                this_->wake_word_cv_.notify_all();
                return;
            }
            // Get frame size
            int frame_size = 0;
            int outbuf_size = 0;
            esp_opus_enc_get_frame_size(encoder_handle, &frame_size, &outbuf_size);
            frame_size = frame_size / sizeof(int16_t);
            // Encode all PCM data
            int packets = 0;
            std::vector<int16_t> in_buffer;
            esp_audio_enc_in_frame_t in = {};
            esp_audio_enc_out_frame_t out = {};
            for (auto& pcm: this_->wake_word_pcm_) {
                if (in_buffer.empty()) {
                    in_buffer = std::move(pcm);
                } else {
                    in_buffer.reserve(in_buffer.size() + pcm.size());
                    in_buffer.insert(in_buffer.end(), pcm.begin(), pcm.end());
                }
                while (in_buffer.size() >= frame_size) {
                    std::vector<uint8_t> opus_buf(outbuf_size);
                    in.buffer = (uint8_t *)(in_buffer.data());
                    in.len = (uint32_t)(frame_size * sizeof(int16_t));
                    out.buffer = opus_buf.data();
                    out.len = outbuf_size;
                    out.encoded_bytes = 0;
                    ret = esp_opus_enc_process(encoder_handle, &in, &out);
                    if (ret == ESP_AUDIO_ERR_OK) {
                        std::lock_guard<std::mutex> lock(this_->wake_word_mutex_);
                        this_->wake_word_opus_.emplace_back(opus_buf.data(), opus_buf.data() + out.encoded_bytes);
                        this_->wake_word_cv_.notify_all();
                        packets++;
                    } else {
                        ESP_LOGE(TAG, "Failed to encode audio, error code: %d", ret);
                    }
                    in_buffer.erase(in_buffer.begin(), in_buffer.begin() + frame_size);
                }
            }
            this_->wake_word_pcm_.clear();
            // Close encoder
            esp_opus_enc_close(encoder_handle);
            auto end_time = esp_timer_get_time();
            ESP_LOGI(TAG, "Encode wake word opus %d packets in %ld ms", packets, (long)((end_time - start_time) / 1000));

            std::lock_guard<std::mutex> lock(this_->wake_word_mutex_);
            this_->wake_word_opus_.push_back(std::vector<uint8_t>());
            this_->wake_word_cv_.notify_all();
        }
        vTaskDelete(NULL);
    }, "encode_wake_word", stack_size, this, 2, wake_word_encode_task_stack_, wake_word_encode_task_buffer_);
}

bool CustomWakeWord::GetWakeWordOpus(std::vector<uint8_t>& opus) {
    std::unique_lock<std::mutex> lock(wake_word_mutex_);
    wake_word_cv_.wait(lock, [this]() {
        return !wake_word_opus_.empty();
    });
    opus.swap(wake_word_opus_.front());
    wake_word_opus_.pop_front();
    return !opus.empty();
}
