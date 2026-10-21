/*
 * board_config.h — 引脚与音频参数定义
 *
 * 依据工作区 hardware-io 文档（硬件信号分配）：
 *   - Codec 控制: I2C SDA=GPIO13, SCL=GPIO14
 *   - I2S: MCLK=GPIO21, SCLK(BCLK)=GPIO18, LRCK(WS)=GPIO16,
 *          DOUT=GPIO15 (→ES8311 DSDIN), DIN=GPIO17 (←ES8311 DSOUT)
 *   - 功放: CTRL=GPIO34（拉高使能，本版本默认常开）
 *          ※ 硬件实测 CTRL=GPIO34；hardware-io 文档写的 GPIO38 有误（38 非 CTRL）
 *   - 按键: KEY_OK=GPIO45（按下为低电平）
 *   - 预留（本项目不使用，IO 已占用避免冲突）:
 *       SD 卡: GPIO1/2/4/5；墨水屏: GPIO6/8/9/10/11/12；
 *       KEY+=GPIO0, KEY-=GPIO46, ADC_BAT=GPIO7
 */
#pragma once

#include "driver/i2c_master.h"
#include "driver/i2s_std.h"

/* ---------------- 固件工作模式 ----------------
 * 0 = 按键录放音（默认功能）
 * 1 = 正弦波持续输出测试（ES8311 输出 1kHz 正弦）
 * 2 = 回声环回（ES8311 录到什么，直接回环输出到 OUT）
 * 3 = 1kHz 正弦波 + KEY+/KEY- 音量控制
 * 切换后需重新编译。 */
#define FW_MODE   3

#if FW_MODE == 2
#define APP_VERSION_STR    "v0.4.0-loopback"
#elif FW_MODE == 3
#define APP_VERSION_STR    "v0.6.0-sine-volkeys"
#elif FW_MODE == 1
#define APP_VERSION_STR    "v0.3.0-sine-test"
#else
#define APP_VERSION_STR    "v0.5.0-ctrl34-recordplay"
#endif

/* ---------------- Codec I2C ---------------- */
#define CODEC_I2C_PORT      I2C_NUM_0
#define CODEC_I2C_SDA_IO    13
#define CODEC_I2C_SCL_IO    14

/* ---------------- Codec I2S ---------------- */
#define CODEC_I2S_PORT      I2S_NUM_0
#define CODEC_I2S_MCLK_IO   21
#define CODEC_I2S_BCLK_IO   18
#define CODEC_I2S_WS_IO     16
#define CODEC_I2S_DOUT_IO   15   /* ESP32 输出 → ES8311 DSDIN */
#define CODEC_I2S_DIN_IO    17   /* ESP32 输入 ← ES8311 DSOUT */

/* ---------------- 功放 ---------------- */
#define PA_CTRL_GPIO        34   /* 模拟功放使能，拉高使能（默认常开）。
                                    硬件实测为 GPIO34（hardware-io 文档写 38 有误） */

/* ---------------- 按键 ---------------- */
#define KEY_OK_GPIO         45   /* 确认键，按下为低电平（内部上拉） */
#define KEY_PLUS_GPIO       0    /* 音量加（KEY+），按下为低电平（内部上拉） */
#define KEY_MINUS_GPIO      46   /* 音量减（KEY-），按下为低电平（内部上拉） */
#define KEY_DEBOUNCE_MS     50   /* 按键消抖时间 */

/* ---------------- 音频参数 ---------------- */
#define AUDIO_SAMPLE_RATE   48000
#define AUDIO_BITS          16
#define AUDIO_CHANNELS      2    /* I2S 立体声槽位（ES8311 为单声道 codec，
                                    数据在左槽位，右槽位为空/参考） */

/* 录音缓冲（PSRAM）：
 * 立体声 16bit @48k = 192 KB/s
 * 默认 30s ≈ 5.76MB（N16R8 板 8MB PSRAM 足够，运行时按实际空闲量取较小值） */
#define RECORD_MAX_SECONDS  30
