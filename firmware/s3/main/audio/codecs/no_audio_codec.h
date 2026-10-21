#ifndef _NO_AUDIO_CODEC_H
#define _NO_AUDIO_CODEC_H

#include "audio_codec.h"

#include <driver/gpio.h>
#include <driver/i2s_pdm.h>
#include <mutex>

class NoAudioCodec : public AudioCodec {
protected:
    std::mutex data_if_mutex_;
    // 麦克风 I2S 采样是 32 位槽（24 位有效数据左对齐），转成 int16 时的
    // 右移位数。默认 12（沿用原实现，适配灵敏度极低的小麦克风）；
    // 24->16 的标准转换是 16。位移越小增益越大：>>12 相当于比标准多放大
    // 16 倍，底噪会被抬到与语音同量级（AFE 的 VAD 会一直判定"有语音"，
    // MultiNet 反而出不了结果）。ICS-43434 等标准 MEMS 麦请用 16。
    int mic_shift_bits_ = 12;

    virtual int Write(const int16_t* data, int samples) override;
    virtual int Read(int16_t* dest, int samples) override;
    virtual void EnableInput(bool enable) override;
    virtual void EnableOutput(bool enable) override;

public:
    void SetMicShiftBits(int bits) {
        if (bits >= 0 && bits <= 24) mic_shift_bits_ = bits;
    }
    int mic_shift_bits() const { return mic_shift_bits_; }

    virtual ~NoAudioCodec();
};

class NoAudioCodecDuplex : public NoAudioCodec {
public:
    NoAudioCodecDuplex(int input_sample_rate, int output_sample_rate, gpio_num_t bclk, gpio_num_t ws, gpio_num_t dout, gpio_num_t din);
};

class NoAudioCodecSimplex : public NoAudioCodec {
public:
    NoAudioCodecSimplex(int input_sample_rate, int output_sample_rate, gpio_num_t spk_bclk, gpio_num_t spk_ws, gpio_num_t spk_dout, gpio_num_t mic_sck, gpio_num_t mic_ws, gpio_num_t mic_din);
    NoAudioCodecSimplex(int input_sample_rate, int output_sample_rate, gpio_num_t spk_bclk, gpio_num_t spk_ws, gpio_num_t spk_dout, i2s_std_slot_mask_t spk_slot_mask, gpio_num_t mic_sck, gpio_num_t mic_ws, gpio_num_t mic_din, i2s_std_slot_mask_t mic_slot_mask);

    // 麦克风时隙自动探测。
    //
    // 数字麦（ICS-43434 / INMP441）的 LR/SEL 引脚决定它在 WS 的左半帧还是
    // 右半帧发数据；焊反或转接板接错时，固件读到的就是另一个空时隙 —— 表现
    // 为 I2S 数据恒为 0（唤醒词日志 peak=0 avg=0）。这里在启动阶段读一小段
    // 原始 32 位采样：若峰值接近 0，就把 RX 通道按另一个时隙重建再测一次，
    // 取有数据的那个。
    //
    // 返回最终使用的时隙掩码。仅在输入未使能时执行，且只跑一次。
    i2s_std_slot_mask_t ProbeMicSlot(uint32_t timeout_ms = 400, bool force = false);

    i2s_std_slot_mask_t mic_slot_mask() const { return mic_slot_mask_; }
    // 最近一次探测到的原始 I2S 峰值（0 = 麦克风完全没有数据）
    int32_t mic_raw_peak() const { return mic_raw_peak_; }

private:
    bool RebuildRx(i2s_std_slot_mask_t mask);
    int32_t SampleRawPeak(uint32_t timeout_ms);

    i2s_std_slot_mask_t mic_slot_mask_ = I2S_STD_SLOT_LEFT;
    gpio_num_t mic_sck_ = GPIO_NUM_NC;
    gpio_num_t mic_ws_ = GPIO_NUM_NC;
    gpio_num_t mic_din_ = GPIO_NUM_NC;
    uint32_t mic_sample_rate_ = 16000;
    int32_t mic_raw_peak_ = 0;
    bool mic_probed_ = false;
};

class NoAudioCodecSimplexPdm : public NoAudioCodec {
public:
    NoAudioCodecSimplexPdm(int input_sample_rate, int output_sample_rate, gpio_num_t spk_bclk, gpio_num_t spk_ws, gpio_num_t spk_dout, gpio_num_t mic_sck,  gpio_num_t mic_din);
    NoAudioCodecSimplexPdm(int input_sample_rate, int output_sample_rate, gpio_num_t spk_bclk, gpio_num_t spk_ws, gpio_num_t spk_dout, i2s_std_slot_mask_t spk_slot_mask, gpio_num_t mic_sck,  gpio_num_t mic_din);
    int Read(int16_t* dest, int samples);
};

#endif // _NO_AUDIO_CODEC_H
