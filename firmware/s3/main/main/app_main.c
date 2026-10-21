/*
 * app_main.c — ESP32-S3 + ES8311
 *
 * 双模式（board_config.h 中 FW_MODE_SINE_TEST 切换）：
 *   模式1（FW_MODE_SINE_TEST=1）：ES8311 持续输出 1kHz 正弦波
 *        —— 音频通路验证（示波器测 ES8311 LineOut / I2S DOUT）
 *   模式0（FW_MODE_SINE_TEST=0）：按键录放音（默认功能）
 *        —— KEY_OK 第1次按=录音，第2次按=停止并回放，回放中再按=停止
 *
 * 引脚（见 board_config.h，依据 hardware-io 文档）：
 *   I2C SDA=13/SCL=14，I2S MCLK=21/SCLK=18/LRCK=16/DOUT=15/DIN=17，
 *   功放 CTRL=34（pa_init 独占控制，默认常开），按键 KEY_OK=45
 *
 * 采样率 48kHz / 16bit / I2S 立体声槽位（ES8311 单声道 codec）。
 */
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <math.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"
#include "driver/gpio.h"
#include "driver/i2c_master.h"
#include "driver/i2s_std.h"
#include "esp_log.h"
#include "esp_check.h"
#include "esp_heap_caps.h"
#include "esp_system.h"
#include "esp_codec_dev.h"
#include "esp_codec_dev_defaults.h"
#include "esp_codec_dev_vol.h"
#include "board_config.h"

static const char *TAG = "es8311_rp";

static i2s_chan_handle_t s_i2s_tx = NULL;
static i2s_chan_handle_t s_i2s_rx = NULL;
static esp_codec_dev_handle_t s_codec = NULL;
/* 音频链路是否就绪：I2S + codec 都初始化成功才为 true。
 * 为 false 时（如板上没接 ES8311）不再 abort 崩溃，而是报错并保持待机，
 * 方便在无 codec 的板子上验证固件/调试。 */
static bool s_audio_ready = false;

/* 单次读写块大小（字节），静态分配避免占用任务栈（两种模式共用） */
#define IO_CHUNK_BYTES  4096
static int16_t s_chunk[IO_CHUNK_BYTES / sizeof(int16_t)];

/* ================= 硬件初始化（两种模式共用） ================= */

static esp_err_t i2s_init(void)
{
    i2s_chan_config_t chan_cfg = I2S_CHANNEL_DEFAULT_CONFIG(CODEC_I2S_PORT, I2S_ROLE_MASTER);
    chan_cfg.auto_clear = true; /* DMA 旧数据自动清零，避免爆音/残响 */
    ESP_RETURN_ON_ERROR(i2s_new_channel(&chan_cfg, &s_i2s_tx, &s_i2s_rx),
                        TAG, "i2s_new_channel failed");

    i2s_std_config_t std_cfg = {
        .clk_cfg = I2S_STD_CLK_DEFAULT_CONFIG(AUDIO_SAMPLE_RATE),
        .slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_16BIT,
                                                        I2S_SLOT_MODE_STEREO),
        .gpio_cfg = {
            .mclk = CODEC_I2S_MCLK_IO,
            .bclk = CODEC_I2S_BCLK_IO,
            .ws   = CODEC_I2S_WS_IO,
            .dout = CODEC_I2S_DOUT_IO,
            .din  = CODEC_I2S_DIN_IO,
        },
    };
    std_cfg.clk_cfg.mclk_multiple = I2S_MCLK_MULTIPLE_256; /* 48k × 256 = 12.288MHz */

    ESP_RETURN_ON_ERROR(i2s_channel_init_std_mode(s_i2s_tx, &std_cfg),
                        TAG, "i2s tx init failed");
    ESP_RETURN_ON_ERROR(i2s_channel_init_std_mode(s_i2s_rx, &std_cfg),
                        TAG, "i2s rx init failed");
    ESP_RETURN_ON_ERROR(i2s_channel_enable(s_i2s_tx), TAG, "i2s tx enable failed");
    ESP_RETURN_ON_ERROR(i2s_channel_enable(s_i2s_rx), TAG, "i2s rx enable failed");
    ESP_LOGI(TAG, "I2S ready: %dHz %dbit %dch, MCLK=%.3fMHz",
             AUDIO_SAMPLE_RATE, AUDIO_BITS, AUDIO_CHANNELS,
             AUDIO_SAMPLE_RATE * 256.0 / 1000000.0);
    return ESP_OK;
}

static esp_err_t codec_init(void)
{
    /* 1. I2C 总线 */
    i2c_master_bus_handle_t i2c_bus = NULL;
    i2c_master_bus_config_t i2c_cfg = {
        .i2c_port = CODEC_I2C_PORT,
        .sda_io_num = CODEC_I2C_SDA_IO,
        .scl_io_num = CODEC_I2C_SCL_IO,
        .clk_source = I2C_CLK_SRC_DEFAULT,
        .glitch_ignore_cnt = 7,
        .flags.enable_internal_pullup = true,
    };
    ESP_RETURN_ON_ERROR(i2c_new_master_bus(&i2c_cfg, &i2c_bus),
                        TAG, "i2c_new_master_bus failed");

    /* 2. ES8311 控制接口（I2C，地址 0x30） */
    audio_codec_i2c_cfg_t i2c_codec_cfg = {
        .port = CODEC_I2C_PORT,
        .addr = ES8311_CODEC_DEFAULT_ADDR,
        .bus_handle = i2c_bus,
    };
    const audio_codec_ctrl_if_t *ctrl_if = audio_codec_new_i2c_ctrl(&i2c_codec_cfg);
    if (!ctrl_if) {
        ESP_LOGE(TAG, "create i2c ctrl_if failed");
        return ESP_FAIL;
    }

    /* 3. 数据接口（I2S） */
    audio_codec_i2s_cfg_t i2s_codec_cfg = {
        .port = CODEC_I2S_PORT,
        .rx_handle = s_i2s_rx,
        .tx_handle = s_i2s_tx,
    };
    const audio_codec_data_if_t *data_if = audio_codec_new_i2s_data(&i2s_codec_cfg);
    if (!data_if) {
        ESP_LOGE(TAG, "create i2s data_if failed");
        return ESP_FAIL;
    }

    /* 4. GPIO 接口 */
    const audio_codec_gpio_if_t *gpio_if = audio_codec_new_gpio();
    if (!gpio_if) {
        ESP_LOGE(TAG, "create gpio_if failed");
        return ESP_FAIL;
    }

    /* 5. ES8311 codec：ADC+DAC 同时使能，从机模式，外部 MCLK
        pa_pin = -1：功放引脚不由驱动管理（驱动在 codec_new 时会先 DISABLE 拉低、
        open 成功才 ENABLE 拉高——会导致 GPIO38 "一闪而过"甚至停在低）。
        功放改为 pa_init() 独占控制：上电即拉高并保持常开。 */
    es8311_codec_cfg_t es8311_cfg = {
        .ctrl_if = ctrl_if,
        .gpio_if = gpio_if,
        .codec_mode = ESP_CODEC_DEV_WORK_MODE_BOTH,
        .master_mode = false,
        .use_mclk = true,
        .pa_pin = -1,              /* 功放由 pa_init() 独占控制，保持常开 */
        .pa_reverted = false,
        .hw_gain = {
            .pa_voltage = 5.0,        /* 功放供电 */
            .codec_dac_voltage = 3.3, /* codec DAC 供电 */
        },
        .no_dac_ref = true,  /* 录音时右声道留空（单声道 ADC） */
        .mclk_div = 256,     /* MCLK = 256 × LRCK */
    };
    const audio_codec_if_t *codec_if = es8311_codec_new(&es8311_cfg);
    if (!codec_if) {
        ESP_LOGE(TAG, "es8311_codec_new failed (ES8311 I2C 无应答?检查接线/供电)");
        return ESP_FAIL;
    }

    /* 6. 顶层 codec 设备（输入+输出） */
    esp_codec_dev_cfg_t dev_cfg = {
        .dev_type = ESP_CODEC_DEV_TYPE_IN_OUT,
        .codec_if = codec_if,
        .data_if = data_if,
    };
    s_codec = esp_codec_dev_new(&dev_cfg);
    if (!s_codec) {
        ESP_LOGE(TAG, "esp_codec_dev_new failed");
        return ESP_FAIL;
    }

    /* 7. 打开设备并设置采样参数 */
    esp_codec_dev_sample_info_t fs = {
        .bits_per_sample = AUDIO_BITS,
        .channel = AUDIO_CHANNELS,
        .channel_mask = 0x03,
        .sample_rate = AUDIO_SAMPLE_RATE,
    };
    if (esp_codec_dev_open(s_codec, &fs) != ESP_CODEC_DEV_OK) {
        ESP_LOGE(TAG, "esp_codec_dev_open failed");
        return ESP_FAIL;
    }
    ESP_LOGI(TAG, "ES8311 opened: %dHz %dbit %dch", AUDIO_SAMPLE_RATE, AUDIO_BITS, AUDIO_CHANNELS);

    /* 8. 音量与麦克风增益 */
    if (esp_codec_dev_set_out_vol(s_codec, 70) != ESP_CODEC_DEV_OK) {
        ESP_LOGW(TAG, "set out vol failed");
    }
    if (esp_codec_dev_set_in_gain(s_codec, 24.0f) != ESP_CODEC_DEV_OK) {
        ESP_LOGW(TAG, "set in gain failed");
    }
    return ESP_OK;
}

/* 功放默认常开（GPIO34 拉高）。
 * 注意：必须用 GPIO_MODE_INPUT_OUTPUT（打开输入缓冲），gpio_get_level
 * 才能回读真实 pad 电平；GPIO_MODE_OUTPUT 下输入缓冲关闭，回读恒为 0（无效）。
 *   pad readback=1 → pad 确实为高（若外部仍测 0，是测点/走线问题）
 *   pad readback=0 → pad 真被外部拉低/短路（PA 模块负载、下拉、短路） */
static void pa_init(void)
{
    gpio_set_direction(PA_CTRL_GPIO, GPIO_MODE_INPUT_OUTPUT);
    gpio_set_level(PA_CTRL_GPIO, 1);
    ESP_LOGI(TAG, "PA enabled (CTRL=GPIO%d high), pad readback=%d",
             PA_CTRL_GPIO, gpio_get_level(PA_CTRL_GPIO));
}

/* ===================================================================== */
/* 模式2：回声环回——ES8311 录到什么，直接回环输出到 OUT                    */
/* ===================================================================== */
#if FW_MODE == 2

static void audio_task(void *arg)
{
    if (!s_audio_ready || !s_codec) {
        ESP_LOGE(TAG, "audio not ready - loopback disabled");
        vTaskDelete(NULL);
        return;
    }
    ESP_LOGI(TAG, "loopback start: MIC -> ADC -> I2S RX -> I2S TX -> DAC -> OUT");

    while (1) {
        /* 读一块麦克风数据（阻塞，~21ms），原样写回（回声） */
        int ret = esp_codec_dev_read(s_codec, s_chunk, sizeof(s_chunk));
        if (ret == ESP_CODEC_DEV_OK) {
            if (esp_codec_dev_write(s_codec, s_chunk, sizeof(s_chunk)) != ESP_CODEC_DEV_OK) {
                ESP_LOGE(TAG, "loopback write failed");
            }
        } else {
            ESP_LOGE(TAG, "loopback read failed ret=%d", ret);
            vTaskDelay(pdMS_TO_TICKS(100));
        }
    }
}

/* ===================================================================== */
/* 模式3：1kHz 正弦波 + KEY+/KEY- 音量控制                               */
/* ===================================================================== */
#elif FW_MODE == 3

#define SINE_FREQ_HZ      1000
#define SINE_AMPLITUDE    0.30f   /* ±30% full scale ≈ -10.5dBFS，避免削波 */
#define SINE_TABLE_LEN    (AUDIO_SAMPLE_RATE / SINE_FREQ_HZ)   /* 1kHz@48k = 48 采样/周期 */

#define VOL_STEP          5       /* 每次按键音量步进（0~100） */
#define VOL_MIN           0
#define VOL_MAX           100

static int16_t s_sine_table[SINE_TABLE_LEN];

/* 音量按键任务：KEY+（GPIO0）/ KEY-（GPIO46），按下低电平，内部上拉 */
static void key_volume_task(void *arg)
{
    gpio_set_direction(KEY_PLUS_GPIO, GPIO_MODE_INPUT);
    gpio_set_pull_mode(KEY_PLUS_GPIO, GPIO_PULLUP_ONLY);
    gpio_set_direction(KEY_MINUS_GPIO, GPIO_MODE_INPUT);
    gpio_set_pull_mode(KEY_MINUS_GPIO, GPIO_PULLUP_ONLY);

    int vol = 70;
    if (s_codec) {
        esp_codec_dev_set_out_vol(s_codec, vol);
    }
    ESP_LOGI(TAG, "volume task started, vol=%d (KEY+ = GPIO%d, KEY- = GPIO%d)",
             vol, KEY_PLUS_GPIO, KEY_MINUS_GPIO);

    while (1) {
        if (gpio_get_level(KEY_PLUS_GPIO) == 0) {
            vTaskDelay(pdMS_TO_TICKS(KEY_DEBOUNCE_MS));
            if (gpio_get_level(KEY_PLUS_GPIO) == 0) {
                vol += VOL_STEP;
                if (vol > VOL_MAX) {
                    vol = VOL_MAX;
                }
                if (s_codec) {
                    esp_codec_dev_set_out_vol(s_codec, vol);
                }
                ESP_LOGI(TAG, "vol -> %d", vol);
                while (gpio_get_level(KEY_PLUS_GPIO) == 0) {
                    vTaskDelay(pdMS_TO_TICKS(20)); /* 等待松开，避免连发 */
                }
            }
        } else if (gpio_get_level(KEY_MINUS_GPIO) == 0) {
            vTaskDelay(pdMS_TO_TICKS(KEY_DEBOUNCE_MS));
            if (gpio_get_level(KEY_MINUS_GPIO) == 0) {
                vol -= VOL_STEP;
                if (vol < VOL_MIN) {
                    vol = VOL_MIN;
                }
                if (s_codec) {
                    esp_codec_dev_set_out_vol(s_codec, vol);
                }
                ESP_LOGI(TAG, "vol -> %d", vol);
                while (gpio_get_level(KEY_MINUS_GPIO) == 0) {
                    vTaskDelay(pdMS_TO_TICKS(20));
                }
            }
        }
        vTaskDelay(pdMS_TO_TICKS(10));
    }
}

/* 正弦波输出任务：持续写 1kHz 正弦，音量由 codec DAC 音量（KEY+/KEY-）控制 */
static void audio_task(void *arg)
{
    for (int i = 0; i < SINE_TABLE_LEN; i++) {
        float v = SINE_AMPLITUDE * 32767.0f * sinf(2.0f * (float)M_PI * i / SINE_TABLE_LEN);
        s_sine_table[i] = (int16_t)v;
    }
    ESP_LOGI(TAG, "sine output: %dHz amp %.1f%% @%dHz/%dbit/%dch, vol by KEY+/KEY-",
             SINE_FREQ_HZ, SINE_AMPLITUDE * 100.0f,
             AUDIO_SAMPLE_RATE, AUDIO_BITS, AUDIO_CHANNELS);

    if (!s_audio_ready || !s_codec) {
        ESP_LOGE(TAG, "audio not ready - sine output disabled");
        vTaskDelete(NULL);
        return;
    }

    size_t len = sizeof(s_chunk) / sizeof(s_chunk[0]);
    size_t idx = 0;
    while (1) {
        /* 立体声槽位：L/R 同值（ES8311 单声道，数据在左槽位） */
        for (size_t i = 0; i < len; i += 2) {
            int16_t v = s_sine_table[idx];
            s_chunk[i] = v;
            s_chunk[i + 1] = v;
            if (++idx >= SINE_TABLE_LEN) {
                idx = 0;
            }
        }
        if (esp_codec_dev_write(s_codec, s_chunk, sizeof(s_chunk)) != ESP_CODEC_DEV_OK) {
            ESP_LOGE(TAG, "sine write failed");
            vTaskDelay(pdMS_TO_TICKS(100));
        }
    }
}

/* ===================================================================== */
/* 模式1：ES8311 持续输出 1kHz 正弦波（音频通路测试）                      */
/* ===================================================================== */
#elif FW_MODE == 1

#define SINE_FREQ_HZ      1000
#define SINE_AMPLITUDE    0.30f   /* ±30% full scale ≈ -10.5dBFS，避免削波 */
#define SINE_TABLE_LEN    (AUDIO_SAMPLE_RATE / SINE_FREQ_HZ)   /* 1kHz@48k = 48 采样/周期 */

static int16_t s_sine_table[SINE_TABLE_LEN];

static void audio_task(void *arg)
{
    /* 预生成正弦表 */
    for (int i = 0; i < SINE_TABLE_LEN; i++) {
        float v = SINE_AMPLITUDE * 32767.0f * sinf(2.0f * (float)M_PI * i / SINE_TABLE_LEN);
        s_sine_table[i] = (int16_t)v;
    }
    ESP_LOGI(TAG, "sine test: %dHz, amp %.1f%% @%dHz/%dbit/%dch",
             SINE_FREQ_HZ, SINE_AMPLITUDE * 100.0f,
             AUDIO_SAMPLE_RATE, AUDIO_BITS, AUDIO_CHANNELS);

    if (!s_audio_ready || !s_codec) {
        ESP_LOGE(TAG, "audio not ready - sine output disabled");
        vTaskDelete(NULL);
        return;
    }

    size_t len = sizeof(s_chunk) / sizeof(s_chunk[0]);
    size_t idx = 0;
    while (1) {
        /* 立体声槽位：L/R 同值（ES8311 单声道，数据在左槽位） */
        for (size_t i = 0; i < len; i += 2) {
            int16_t v = s_sine_table[idx];
            s_chunk[i] = v;
            s_chunk[i + 1] = v;
            if (++idx >= SINE_TABLE_LEN) {
                idx = 0;
            }
        }
        if (esp_codec_dev_write(s_codec, s_chunk, sizeof(s_chunk)) != ESP_CODEC_DEV_OK) {
            ESP_LOGE(TAG, "sine write failed");
            vTaskDelay(pdMS_TO_TICKS(100));
        }
    }
}

/* ===================================================================== */
/* 模式0：按键录放音（默认功能）                                          */
/* ===================================================================== */
#else /* FW_MODE == 0 */

/* ---------------- 事件队列 ---------------- */
#define EV_BTN_PRESS  1
static QueueHandle_t s_ev_queue;

/* ---------------- 录音缓冲（PSRAM） ---------------- */
static int16_t *s_rec_buf = NULL;   /* 立体声帧原始数据 */
static size_t   s_rec_cap = 0;      /* 缓冲容量（字节） */
static size_t   s_rec_len = 0;      /* 已录字节数 */

/* ---------------- 状态机 ---------------- */
typedef enum {
    ST_IDLE = 0,
    ST_RECORDING,
    ST_PLAYBACK,
} audio_state_t;

/* ================= 按键任务 ================= */

static void button_task(void *arg)
{
    gpio_set_direction(KEY_OK_GPIO, GPIO_MODE_INPUT);
    gpio_set_pull_mode(KEY_OK_GPIO, GPIO_PULLUP_ONLY);
    ESP_LOGI(TAG, "KEY_OK (GPIO%d) task started, press to record/play", KEY_OK_GPIO);

    int released = 1;
    while (1) {
        int level = gpio_get_level(KEY_OK_GPIO); /* 按下=0 */
        if (level == 0 && released) {
            vTaskDelay(pdMS_TO_TICKS(KEY_DEBOUNCE_MS));
            if (gpio_get_level(KEY_OK_GPIO) == 0) {
                released = 0;
                uint32_t ev = EV_BTN_PRESS;
                xQueueSend(s_ev_queue, &ev, 0);
                ESP_LOGI(TAG, "KEY_OK pressed");
            }
        } else if (level == 1) {
            released = 1;
        }
        vTaskDelay(pdMS_TO_TICKS(10));
    }
}

/* ================= 录音/回放任务 ================= */

static void record_alloc_buffer(void)
{
    size_t bytes_per_sec = AUDIO_SAMPLE_RATE * (AUDIO_BITS / 8) * AUDIO_CHANNELS; /* 192KB/s */
    size_t wanted = bytes_per_sec * RECORD_MAX_SECONDS;
    size_t free_psram = heap_caps_get_free_size(MALLOC_CAP_SPIRAM);

    if (free_psram <= 64 * 1024) {
        ESP_LOGE(TAG, "PSRAM unavailable/full (free=%u), cannot record",
                 (unsigned)free_psram);
        s_rec_cap = 0;
        return;
    }
    s_rec_cap = wanted;
    if (s_rec_cap > free_psram - 64 * 1024) { /* 预留少量余量 */
        s_rec_cap = free_psram - 64 * 1024;
    }
    s_rec_buf = heap_caps_malloc(s_rec_cap, MALLOC_CAP_SPIRAM);
    if (!s_rec_buf) {
        ESP_LOGE(TAG, "PSRAM alloc %u bytes failed (free=%u)", (unsigned)s_rec_cap, (unsigned)free_psram);
        s_rec_cap = 0;
        return;
    }
    ESP_LOGI(TAG, "record buffer: %u bytes (~%.1fs @%dHz %dbit %dch)",
             (unsigned)s_rec_cap, s_rec_cap / (float)bytes_per_sec,
             AUDIO_SAMPLE_RATE, AUDIO_BITS, AUDIO_CHANNELS);
}

static void audio_task(void *arg)
{
    audio_state_t state = ST_IDLE;
    uint32_t ev = 0;
    TickType_t last_log = 0;

    while (1) {
        switch (state) {
        case ST_IDLE:
            if (xQueueReceive(s_ev_queue, &ev, portMAX_DELAY) == pdTRUE) {
                if (!s_audio_ready) {
                    ESP_LOGE(TAG, "audio not ready (I2S/codec init failed), cannot record - check I2C wiring/ES8311");
                    continue; /* 保持待机，不崩溃 */
                }
                record_alloc_buffer();
                if (!s_rec_buf) {
                    continue; /* 分配失败，回到待机 */
                }
                s_rec_len = 0;
                state = ST_RECORDING;
                last_log = xTaskGetTickCount();
                ESP_LOGI(TAG, "RECORDING... (KEY_OK to stop)");
            }
            break;

        case ST_RECORDING: {
            /* 读一块麦克风数据（阻塞，~21ms） */
            int ret = esp_codec_dev_read(s_codec, s_chunk, sizeof(s_chunk));
            if (ret == ESP_CODEC_DEV_OK) {
                size_t n = sizeof(s_chunk);
                if (s_rec_len + n <= s_rec_cap) {
                    memcpy((uint8_t *)s_rec_buf + s_rec_len, s_chunk, n);
                    s_rec_len += n;
                }
            } else {
                ESP_LOGE(TAG, "read failed ret=%d", ret);
            }

            /* 每秒打印进度 */
            TickType_t now = xTaskGetTickCount();
            if (now - last_log >= pdMS_TO_TICKS(1000)) {
                last_log = now;
                ESP_LOGI(TAG, "  rec %u KB / %u KB (%u%%)",
                         (unsigned)(s_rec_len / 1024), (unsigned)(s_rec_cap / 1024),
                         (unsigned)(s_rec_len * 100 / s_rec_cap));
            }

            /* 按键停止 或 缓冲写满 → 进入回放 */
            if (xQueueReceive(s_ev_queue, &ev, 0) == pdTRUE || s_rec_len >= s_rec_cap) {
                ESP_LOGI(TAG, "RECORD STOP: %u bytes (%.2fs)",
                         (unsigned)s_rec_len, s_rec_len / (float)(AUDIO_SAMPLE_RATE * 4));
                state = ST_PLAYBACK;
            }
            break;
        }

        case ST_PLAYBACK: {
            size_t written = 0;
            while (written < s_rec_len) {
                size_t n = s_rec_len - written;
                if (n > sizeof(s_chunk)) {
                    n = sizeof(s_chunk);
                }
                int ret = esp_codec_dev_write(s_codec, (uint8_t *)s_rec_buf + written, n);
                if (ret != ESP_CODEC_DEV_OK) {
                    ESP_LOGE(TAG, "write failed ret=%d", ret);
                    break;
                }
                written += n;
                /* 回放中按键 → 停止 */
                if (xQueueReceive(s_ev_queue, &ev, 0) == pdTRUE) {
                    ESP_LOGI(TAG, "playback stopped by user at %u/%u bytes",
                             (unsigned)written, (unsigned)s_rec_len);
                    break;
                }
            }
            ESP_LOGI(TAG, "PLAYBACK done: %u bytes", (unsigned)written);
            free(s_rec_buf);
            s_rec_buf = NULL;
            s_rec_len = 0;
            s_rec_cap = 0;
            state = ST_IDLE;
            ESP_LOGI(TAG, "IDLE - press KEY_OK to record again");
            break;
        }
        }
    }
}

#endif /* FW_MODE */

/* ================= 入口 ================= */

void app_main(void)
{
    ESP_LOGI(TAG, "=============================================");
    ESP_LOGI(TAG, " es8311_record_play  %s", APP_VERSION_STR);
    ESP_LOGI(TAG, " built %s %s, IDF %s", __DATE__, __TIME__, IDF_VER);
    ESP_LOGI(TAG, "=============================================");

    /* 复位原因：1=POWERON 2=EXT(EN/按键复位) 3=SW 4=PANIC(崩溃) 5=INT_WDT 6=TASK_WDT
       9=BROWNOUT(欠压) 11=USB(USB-JTAG) 15=CPU_LOCKUP —— 排查看门狗/崩溃/供电的关键信息 */
    ESP_LOGI(TAG, "reset reason: %d", (int)esp_reset_reason());

#if FW_MODE == 1
    ESP_LOGI(TAG, "MODE: SINE TEST - ES8311 outputs %dHz continuously", SINE_FREQ_HZ);
#elif FW_MODE == 2
    ESP_LOGI(TAG, "MODE: LOOPBACK - ES8311 mic -> OUT (echo)");
#elif FW_MODE == 3
    ESP_LOGI(TAG, "MODE: SINE %dHz + VOL keys (KEY+ GPIO%d / KEY- GPIO%d)",
             SINE_FREQ_HZ, KEY_PLUS_GPIO, KEY_MINUS_GPIO);
#else
    ESP_LOGI(TAG, "MODE: record/playback - KEY_OK: record/stop/play");
#endif

#if FW_MODE == 0
    s_ev_queue = xQueueCreate(8, sizeof(uint32_t));
    if (!s_ev_queue) {
        ESP_LOGE(TAG, "queue create failed");
        abort();
    }
#endif

    pa_init();                 /* 功放常开（pa_init 独占控制 GPIO34，驱动不再接管） */
    if (i2s_init() == ESP_OK && codec_init() == ESP_OK) {
        s_audio_ready = true;
        ESP_LOGI(TAG, "audio chain ready");
    } else {
        /* 不 abort：降级运行，方便无 codec 的板子调试 */
        ESP_LOGE(TAG, "audio init failed - running WITHOUT audio (check I2C/ES8311 wiring)");
    }
    /* 初始化流程后再次确认功放保持拉高（常开） */
    gpio_set_level(PA_CTRL_GPIO, 1);
    ESP_LOGI(TAG, "PA CTRL final: %d", gpio_get_level(PA_CTRL_GPIO));

#if FW_MODE == 0
    xTaskCreate(button_task, "btn", 3072, NULL, 5, NULL);
    xTaskCreate(audio_task, "audio", 8192, NULL, 5, NULL);
#elif FW_MODE == 3
    xTaskCreate(key_volume_task, "keys", 3072, NULL, 5, NULL);
    xTaskCreate(audio_task, "audio", 8192, NULL, 5, NULL);
#else
    xTaskCreate(audio_task, "audio", 8192, NULL, 5, NULL);
#endif
}
