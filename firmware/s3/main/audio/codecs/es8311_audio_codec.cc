#include "es8311_audio_codec.h"

#include <esp_err.h>
#include <esp_log.h>
#include <esp_timer.h>
#include "settings.h"
#define TAG "Es8311AudioCodec"

void Es8311AudioCodec::CreateDuplexChannels(gpio_num_t mclk, gpio_num_t bclk,
    gpio_num_t ws, gpio_num_t dout, gpio_num_t din) {
    assert(input_sample_rate_ == output_sample_rate_);
    i2s_chan_config_t chan_cfg = {
        .id = I2S_NUM_0,
        .role = I2S_ROLE_MASTER,
        .dma_desc_num = AUDIO_CODEC_DMA_DESC_NUM,
        .dma_frame_num = AUDIO_CODEC_DMA_FRAME_NUM,
        .auto_clear_after_cb = true,
        .auto_clear_before_cb = false,
        .intr_priority = 0,
    };
    ESP_ERROR_CHECK(i2s_new_channel(&chan_cfg, &tx_handle_, &rx_handle_));

    i2s_std_config_t std_cfg = {
        .clk_cfg = {
            .sample_rate_hz = (uint32_t)output_sample_rate_,
            .clk_src = I2S_CLK_SRC_DEFAULT,
            .mclk_multiple = I2S_MCLK_MULTIPLE_256,
        },
        .slot_cfg = {
            .data_bit_width = I2S_DATA_BIT_WIDTH_16BIT,
            .slot_bit_width = I2S_SLOT_BIT_WIDTH_AUTO,
            .slot_mode = I2S_SLOT_MODE_STEREO,
            .slot_mask = I2S_STD_SLOT_BOTH,
            .ws_width = I2S_DATA_BIT_WIDTH_16BIT,
            .ws_pol = false,
            .bit_shift = true,
        },
        .gpio_cfg = {
            .mclk = mclk, .bclk = bclk, .ws = ws, .dout = dout, .din = din,
            .invert_flags = { .mclk_inv = false, .bclk_inv = false, .ws_inv = false }
        }
    };
    ESP_ERROR_CHECK(i2s_channel_init_std_mode(tx_handle_, &std_cfg));
    ESP_ERROR_CHECK(i2s_channel_init_std_mode(rx_handle_, &std_cfg));
    ESP_ERROR_CHECK(i2s_channel_enable(tx_handle_));
    ESP_ERROR_CHECK(i2s_channel_enable(rx_handle_));
    ESP_LOGI(TAG, "Duplex channels created");
}

Es8311AudioCodec::Es8311AudioCodec(void* i2c_master_handle, i2c_port_t i2c_port, int input_sample_rate, int output_sample_rate,
    gpio_num_t mclk, gpio_num_t bclk, gpio_num_t ws, gpio_num_t dout, gpio_num_t din,
    gpio_num_t pa_pin, uint8_t es8311_addr, bool use_mclk, bool pa_inverted, float pa_voltage) {
    duplex_ = true;
    input_reference_ = false;
    input_channels_ = 1;
    input_sample_rate_ = input_sample_rate;
    output_sample_rate_ = output_sample_rate;
    pa_pin_ = pa_pin;
    pa_inverted_ = pa_inverted;
    // ZTS6216 sensitivity is -38 dBV/Pa.  The ES8311 analog PGA is already at
    // 30 dB (REG14=0x1A, set by the driver).  The previous default of 42 dB of
    // *digital* input gain pushed the total mic path to ~72 dB, which clips
    // normal room speech (peak slamming into int16 max) and keeps MultiNet's VAD
    // permanently in the "detecting" state — so the wake word ("呼叫小悟") never
    // reaches a DETECTED state and prob stays 0.00.  Use a conservative digital
    // gain and let it be tuned from the field via NVS (key "audio"/"input_gain")
    // or the MCP tool, with no reflash required.
    input_gain_ = 18.0f;
    {
        Settings settings("audio", /*read_write=*/false);
        const int32_t saved = settings.GetInt("input_gain", 18);
        // Guard against absurd values that would clip or go silent.
        if (saved >= 0 && saved <= 43) {
            input_gain_ = static_cast<float>(saved);
        }
    }

    assert(input_sample_rate_ == output_sample_rate_);
    CreateDuplexChannels(mclk, bclk, ws, dout, din);

    audio_codec_i2s_cfg_t i2s_cfg = {
        .port = I2S_NUM_0,
        .rx_handle = rx_handle_,
        .tx_handle = tx_handle_,
    };
    data_if_ = audio_codec_new_i2s_data(&i2s_cfg);
    assert(data_if_ != NULL);

    audio_codec_i2c_cfg_t i2c_cfg = {
        .port = i2c_port,
        .addr = es8311_addr,
        .bus_handle = i2c_master_handle,
    };
    ctrl_if_ = audio_codec_new_i2c_ctrl(&i2c_cfg);
    assert(ctrl_if_ != NULL);

    gpio_if_ = audio_codec_new_gpio();
    assert(gpio_if_ != NULL);

    es8311_codec_cfg_t es8311_cfg = {};
    es8311_cfg.ctrl_if = ctrl_if_;
    es8311_cfg.gpio_if = gpio_if_;
    es8311_cfg.codec_mode = ESP_CODEC_DEV_WORK_MODE_BOTH;
    es8311_cfg.pa_pin = pa_pin;
    es8311_cfg.use_mclk = use_mclk;
    es8311_cfg.hw_gain.pa_voltage = pa_voltage;
    es8311_cfg.hw_gain.codec_dac_voltage = 3.3;
    es8311_cfg.pa_reverted = pa_inverted_;
    codec_if_ = es8311_codec_new(&es8311_cfg);

    if (codec_if_ == nullptr) {
        ESP_LOGE(TAG, "Failed to create Es8311AudioCodec");
    } else {
        ESP_LOGI(TAG,
                 "ES8311 ready: MCLK=%d BCLK=%d WS=%d DOUT=%d DIN=%d PA=%d, PA supply=%.1fV",
                 mclk, bclk, ws, dout, din, pa_pin, pa_voltage);
    }
}

Es8311AudioCodec::~Es8311AudioCodec() {
    esp_codec_dev_delete(dev_);
    audio_codec_delete_codec_if(codec_if_);
    audio_codec_delete_ctrl_if(ctrl_if_);
    audio_codec_delete_gpio_if(gpio_if_);
    audio_codec_delete_data_if(data_if_);
}

void Es8311AudioCodec::UpdateDeviceState() {
    if ((input_enabled_ || output_enabled_) && dev_ == nullptr) {
        esp_codec_dev_cfg_t dev_cfg = {
            .dev_type = ESP_CODEC_DEV_TYPE_IN_OUT,
            .codec_if = codec_if_,
            .data_if = data_if_,
        };
        dev_ = esp_codec_dev_new(&dev_cfg);
        assert(dev_ != NULL);

        esp_codec_dev_sample_info_t fs = {
            .bits_per_sample = 16,
            .channel = 1,
            .channel_mask = 0,
            .sample_rate = (uint32_t)input_sample_rate_,
            .mclk_multiple = 0,
        };
        ESP_ERROR_CHECK(esp_codec_dev_open(dev_, &fs));

        // esp_codec_dev turns a one-channel stream into MONO/LEFT I2S.  The
        // ES8311/board playback path was validated with normal two-slot I2S,
        // so keep RX mono for the microphone but restore TX to L+R slots.
        // Write() below duplicates every mono sample into both slots.
        ESP_ERROR_CHECK(i2s_channel_disable(tx_handle_));
        i2s_std_slot_config_t tx_slot_cfg =
            I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_16BIT,
                                                I2S_SLOT_MODE_STEREO);
        tx_slot_cfg.slot_mask = I2S_STD_SLOT_BOTH;
        ESP_ERROR_CHECK(i2s_channel_reconfig_std_slot(tx_handle_, &tx_slot_cfg));
        ESP_ERROR_CHECK(i2s_channel_enable(tx_handle_));
        ESP_LOGI(TAG, "Speaker TX forced to STEREO/BOTH; mono PCM duplicated to L/R");

        ESP_ERROR_CHECK(esp_codec_dev_set_in_gain(dev_, input_gain_));
        ESP_ERROR_CHECK(esp_codec_dev_set_out_vol(dev_, 100));

        // Log actual DAC volume register value for debugging
        int dac_vol_reg = 0;
        esp_codec_dev_read_reg(dev_, 0x32, &dac_vol_reg);
        ESP_LOGI(TAG, "Codec opened. DAC REG32 (volume)=0x%02X, max_vol=100", dac_vol_reg);

        // Board-specific: ZTS6216 (ZILLTEK) is an ANALOG-output MEMS mic
        // (-38 dBV/Pa, needs VDD 1.5~3.6V and a DC-blocking cap at its OUT).
        // ES8311 REG14 (0x14) bit layout, per ES8311 User Guide:
        //   bit6   = DMIC_ON  (0 = analog mic)  <- the ONLY mic-type select
        //   bit5:4 = LINSEL   (1 = Mic1p-Mic1n differential)
        //   bit3:0 = PGAGAIN  (0..10 -> 0..30 dB analog PGA gain, 3 dB steps)
        // The esp_codec_dev driver already writes REG14=0x1A after open,
        // i.e. AMIC + Mic1p-Mic1n + PGA 30 dB — exactly what this board needs.
        // NOTE: a previous hack cleared bit3 here believing it was a DMIC
        // select. It is actually PGAGAIN LSB: 0x1A -> 0x12 silently cut the
        // analog gain from 30 dB to 6 dB and the mic read only noise floor
        // (-62 dBFS). Keep REG14 as the driver sets it.
        int reg14 = 0;
        int reg0d = 0;
        int reg0e = 0;
        int reg12 = 0;
        esp_codec_dev_read_reg(dev_, 0x0D, &reg0d);
        esp_codec_dev_read_reg(dev_, 0x0E, &reg0e);
        esp_codec_dev_read_reg(dev_, 0x12, &reg12);
        esp_codec_dev_read_reg(dev_, 0x14, &reg14);
        int reg16 = 0;
        int reg17 = 0;
        esp_codec_dev_read_reg(dev_, 0x16, &reg16);
        esp_codec_dev_read_reg(dev_, 0x17, &reg17);
        ESP_LOGI(TAG,
                 "Codec power check: REG0D=0x%02X REG0E=0x%02X REG12=0x%02X; "
                 "mic: REG14=0x%02X REG16=0x%02X REG17=0x%02X",
                 reg0d, reg0e, reg12, reg14, reg16, reg17);
    } else if (!input_enabled_ && !output_enabled_ && dev_ != nullptr) {
        ESP_ERROR_CHECK(esp_codec_dev_close(dev_));
        esp_codec_dev_delete(dev_);
        dev_ = nullptr;
    }
    if (pa_pin_ != GPIO_NUM_NC) {
        int level = output_enabled_ ? 1 : 0;
        const int driven_level = pa_inverted_ ? !level : level;
        // Keep the input buffer enabled so gpio_get_level() reports the
        // physical CTRL pad state rather than only the requested latch value.
        ESP_ERROR_CHECK_WITHOUT_ABORT(gpio_set_direction(pa_pin_, GPIO_MODE_INPUT_OUTPUT));
        gpio_set_level(pa_pin_, driven_level);
        ESP_LOGI(TAG, "NS4150B PA CTRL: GPIO%d=%d (output %s)",
                 pa_pin_, gpio_get_level(pa_pin_),
                 output_enabled_ ? "enabled" : "disabled");
    }
}

void Es8311AudioCodec::SetOutputVolume(int volume) {
    std::lock_guard<std::mutex> lock(data_if_mutex_);
    if (dev_ != nullptr) {
        ESP_ERROR_CHECK(esp_codec_dev_set_out_vol(dev_, volume));
    }
    AudioCodec::SetOutputVolume(volume);
}

void Es8311AudioCodec::SetInputGain(float gain) {
    input_gain_ = gain;
    if (dev_ != nullptr) {
        ESP_ERROR_CHECK(esp_codec_dev_set_in_gain(dev_, input_gain_));
    }
    Settings settings("audio", /*read_write=*/true);
    settings.SetInt("input_gain", static_cast<int32_t>(input_gain_));
    ESP_LOGI(TAG, "Set input gain to %.1f dB (persisted to NVS)", input_gain_);
}

void Es8311AudioCodec::EnableInput(bool enable) {
    std::lock_guard<std::mutex> lock(data_if_mutex_);
    if (codec_if_ == nullptr) return;
    if (enable == input_enabled_) return;
    AudioCodec::EnableInput(enable);
    UpdateDeviceState();
}

void Es8311AudioCodec::EnableOutput(bool enable) {
    std::lock_guard<std::mutex> lock(data_if_mutex_);
    if (codec_if_ == nullptr) return;
    if (enable == output_enabled_) return;
    AudioCodec::EnableOutput(enable);
    UpdateDeviceState();
    // After UpdateDeviceState() opens the device, re-apply volume to ensure
    // the DAC hardware register (REG32) reflects the current output_volume_.
    if (enable && dev_ != nullptr) {
        ESP_ERROR_CHECK(esp_codec_dev_set_out_vol(dev_, output_volume_));
        int dac_vol_reg = 0;
        esp_codec_dev_read_reg(dev_, 0x32, &dac_vol_reg);
        ESP_LOGI(TAG, "EnableOutput(true): DAC REG32=0x%02X (vol=%d)", dac_vol_reg, output_volume_);
    }
}

int Es8311AudioCodec::Read(int16_t* dest, int samples) {
    // The return value tells AudioService whether real PCM was available.
    // Reporting success when nothing was read makes AudioInputTask() spin
    // without ever blocking, which starves the idle task and trips the task
    // watchdog on CPU0 (seen with ES8311/I2S not delivering data).
    if (!input_enabled_ || dev_ == nullptr) {
        return 0;
    }
    esp_err_t ret = esp_codec_dev_read(dev_, (void*)dest, samples * sizeof(int16_t));
    if (ret != ESP_OK) {
        // Throttled: this can happen every 10 ms while the link is down.
        uint32_t now_ms = (uint32_t)(esp_timer_get_time() / 1000ULL);
        if (now_ms - last_input_error_log_ms_ >= 3000) {
            last_input_error_log_ms_ = now_ms;
            ESP_LOGW(TAG, "Input read failed: %s (input_enabled=%d, dev=%p)",
                     esp_err_to_name(ret), (int)input_enabled_, (void*)dev_);
        }
        return 0;
    }
    return samples;
}

int Es8311AudioCodec::Write(const int16_t* data, int samples) {
    if (output_enabled_ && samples > 0) {
        std::vector<int16_t> stereo(static_cast<size_t>(samples) * 2);
        int peak = 0;
        for (int i = 0; i < samples; ++i) {
            stereo[2 * i] = data[i];
            stereo[2 * i + 1] = data[i];
            const int magnitude = data[i] == INT16_MIN ? 32768 : std::abs(static_cast<int>(data[i]));
            if (magnitude > peak) peak = magnitude;
        }

        size_t bytes_written = 0;
        const esp_err_t ret = i2s_channel_write(
            tx_handle_, stereo.data(), stereo.size() * sizeof(int16_t),
            &bytes_written, portMAX_DELAY);
        ESP_ERROR_CHECK_WITHOUT_ABORT(ret);

        static uint32_t log_divider = 0;
        if ((log_divider++ % 50) == 0) {
            ESP_LOGI(TAG, "[SPK-PCM] mono_samples=%d peak=%d stereo_bytes=%u/%u",
                     samples, peak, static_cast<unsigned>(bytes_written),
                     static_cast<unsigned>(stereo.size() * sizeof(int16_t)));
        }
    }
    return samples;
}
