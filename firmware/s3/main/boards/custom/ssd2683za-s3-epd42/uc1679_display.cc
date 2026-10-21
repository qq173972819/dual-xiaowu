#include "uc1679_display.h"

#include <algorithm>
#include <cstring>

#include <esp_heap_caps.h>
#include <esp_log.h>
#include <esp_rom_sys.h>

namespace {
constexpr char kTag[] = "Uc1679Display";

// ---- 可调面板参数：4.2" 400x300 黑白红，UC1679 参考序列 ----
// PSR: RES[7:6]=10 (400x300), LUT from OTP(bit5=0), BWR mode(bit4=1),
//      UD=1, SHL=1, SHD_N=1, RST_N=1。
// 若模组 demo 使用 0x8F / 0x9F / 0xEF 等不同值，以模组 demo 为准。
constexpr uint8_t kPanelSetting = 0x0F;
// PWR: VGHS/VGLS ±16V 组合、VSST 槽位等（与常见 4.2" 三色 demo 一致）。
constexpr uint8_t kPowerSetting[5] = {0x03, 0x00, 0x2B, 0x2B, 0x03};
// Booster soft-start（与 SSD1619 板同款 boost 拓扑，参考值一致）。
constexpr uint8_t kBooster[3] = {0x17, 0x17, 0x17};
// Border waveform：全刷时浮动边界跟随 LUT。
constexpr uint8_t kPll = 0x3C;
constexpr uint8_t kBorderWave = 0x97;
constexpr uint8_t kVcomDc = 0x12;
}  // namespace

std::atomic<bool> Uc1679Display::g_uc1679_refreshing{false};

Uc1679Display::Uc1679Display(std::function<bool()> other_panel_busy)
    : other_panel_busy_(std::move(other_panel_busy)) {
    mutex_ = xSemaphoreCreateMutex();
    dma_buffer_ = static_cast<uint8_t*>(
        heap_caps_malloc(kDmaChunkBytes, MALLOC_CAP_DMA | MALLOC_CAP_INTERNAL));
    assert(mutex_ && dma_buffer_);
    InitializeGpio();
    InitializeSpiDevice();
}

Uc1679Display::~Uc1679Display() {
    if (spi_) spi_bus_remove_device(spi_);
    if (mutex_) vSemaphoreDelete(mutex_);
    heap_caps_free(dma_buffer_);
}

void Uc1679Display::InitializeGpio() {
    gpio_config_t outputs = {};
    outputs.pin_bit_mask = 1ULL << EPD_COLOR_CS_PIN;
    outputs.mode = GPIO_MODE_OUTPUT;
    ESP_ERROR_CHECK(gpio_config(&outputs));
    gpio_set_level(EPD_COLOR_CS_PIN, 1);
    // DC/RST 由 Ssd1619Display::InitializeGpio 配置为输出并置高，此处不重复
    // 配置方向，只确保本驱动按同一语义使用。

    gpio_config_t busy = {};
    busy.pin_bit_mask = 1ULL << EPD_COLOR_BUSY_PIN;
    busy.mode = GPIO_MODE_INPUT;
    busy.pull_up_en = GPIO_PULLUP_ENABLE;
    ESP_ERROR_CHECK(gpio_config(&busy));
}

void Uc1679Display::InitializeSpiDevice() {
    // 总线由 Ssd1619Display 先行初始化（board 构造顺序保证），此处只挂设备。
    spi_device_interface_config_t device = {};
    device.clock_speed_hz = 4 * 1000 * 1000;
    device.mode = 0;
    device.spics_io_num = -1;  // CS 由本驱动手动控制，与时序一致可观察
    device.queue_size = 2;
    const esp_err_t err = spi_bus_add_device(EPD_SPI_NUM, &device, &spi_);
    if (err != ESP_OK) {
        ESP_LOGE(kTag, "spi_bus_add_device failed: %s", esp_err_to_name(err));
        spi_ = nullptr;
    }
}

void Uc1679Display::WaitForOtherPanelIdle() {
    if (!other_panel_busy_ || !other_panel_busy_()) return;
    ESP_LOGW(kTag, "Shared RST: waiting for SSD1619 refresh to finish");
    const TickType_t start = xTaskGetTickCount();
    while (other_panel_busy_()) {
        if ((xTaskGetTickCount() - start) * portTICK_PERIOD_MS >= 25000) {
            ESP_LOGE(kTag,
                     "SSD1619 refresh wait timeout; resetting anyway "
                     "(BW panel partial waveform may be disturbed)");
            break;
        }
        vTaskDelay(pdMS_TO_TICKS(50));
    }
}

void Uc1679Display::HardwareReset() {
    // 共享 RST（GPIO11）：复位三色屏会同时复位 SSD1619。仅在 SSD1619
    // 不在面板刷新窗口时执行（其 BUSY 空闲）；初始化阶段 SSD1619 亦空闲。
    WaitForOtherPanelIdle();
    for (int i = 0; i < 3; ++i) {
        gpio_set_level(EPD_RST_PIN, 1);
        vTaskDelay(pdMS_TO_TICKS(20));
        gpio_set_level(EPD_RST_PIN, 0);
        vTaskDelay(pdMS_TO_TICKS(2));
    }
    gpio_set_level(EPD_RST_PIN, 1);
    vTaskDelay(pdMS_TO_TICKS(20));
}

bool Uc1679Display::WaitBusy(uint32_t timeout_ms) {
    if (busy_idle_level_ < 0) return true;  // 尚未采样，跳过等待
    const TickType_t start = xTaskGetTickCount();
    while (gpio_get_level(EPD_COLOR_BUSY_PIN) != busy_idle_level_) {
        if ((xTaskGetTickCount() - start) * portTICK_PERIOD_MS >= timeout_ms) {
            ESP_LOGE(kTag, "BUSY timeout after %lu ms (level=%d)",
                     static_cast<unsigned long>(timeout_ms),
                     gpio_get_level(EPD_COLOR_BUSY_PIN));
            return false;
        }
        vTaskDelay(pdMS_TO_TICKS(10));
    }
    return true;
}

bool Uc1679Display::WaitBusyCycle(uint32_t assert_timeout_ms,
                                  uint32_t idle_timeout_ms) {
    // A floating/disconnected BUSY line can stay at its idle level forever.
    // Waiting only for "idle" would then report a successful refresh without
    // the panel ever starting its waveform. Require both edges explicitly.
    const TickType_t start = xTaskGetTickCount();
    while (gpio_get_level(EPD_COLOR_BUSY_PIN) == busy_idle_level_) {
        if ((xTaskGetTickCount() - start) * portTICK_PERIOD_MS >=
            assert_timeout_ms) {
            ESP_LOGE(kTag,
                     "BUSY never asserted (idle=%d, timeout=%lu ms)",
                     busy_idle_level_,
                     static_cast<unsigned long>(assert_timeout_ms));
            return false;
        }
        vTaskDelay(pdMS_TO_TICKS(5));
    }
    return WaitBusy(idle_timeout_ms);
}

void Uc1679Display::Command(uint8_t command, const uint8_t* data,
                            size_t length) {
    if (!spi_) return;
    gpio_set_level(EPD_DC_PIN, 0);
    gpio_set_level(EPD_COLOR_CS_PIN, 0);
    spi_transaction_t transaction = {};
    transaction.length = 8;
    transaction.tx_buffer = &command;
    ESP_ERROR_CHECK(spi_device_polling_transmit(spi_, &transaction));
    gpio_set_level(EPD_COLOR_CS_PIN, 1);
    if (data && length) Data(data, length);
}

void Uc1679Display::Data(const uint8_t* data, size_t length) {
    if (!spi_) return;
    gpio_set_level(EPD_DC_PIN, 1);
    gpio_set_level(EPD_COLOR_CS_PIN, 0);
    while (length > 0) {
        const size_t chunk = std::min(length, kDmaChunkBytes);
        std::memcpy(dma_buffer_, data, chunk);
        spi_transaction_t transaction = {};
        transaction.length = chunk * 8;
        transaction.tx_buffer = dma_buffer_;
        const esp_err_t err = spi_device_polling_transmit(spi_, &transaction);
        if (err != ESP_OK) {
            gpio_set_level(EPD_COLOR_CS_PIN, 1);
            ESP_LOGE(kTag, "SPI data transmit failed: %s, chunk=%u",
                     esp_err_to_name(err), static_cast<unsigned>(chunk));
            return;
        }
        data += chunk;
        length -= chunk;
    }
    gpio_set_level(EPD_COLOR_CS_PIN, 1);
}

bool Uc1679Display::WakeAndConfigure() {
    if (panel_awake_) return true;
    HardwareReset();
    if (!WaitBusy(2000)) return false;

    Command(kCmdPanelSetting, &kPanelSetting, 1);
    Command(kCmdPowerSetting, kPowerSetting, sizeof(kPowerSetting));
    Command(kCmdBooster, kBooster, sizeof(kBooster));
    Command(kCmdPll, &kPll, 1);
    const uint8_t resolution[] = {static_cast<uint8_t>(kWidth >> 8),
                                  static_cast<uint8_t>(kWidth & 0xff),
                                  static_cast<uint8_t>(kHeight >> 8),
                                  static_cast<uint8_t>(kHeight & 0xff)};
    Command(kCmdResolution, resolution, sizeof(resolution));
    Command(kCmdBorderWave, &kBorderWave, 1);
    Command(kCmdVcomDc, &kVcomDc, 1);

    // 上电（时钟 + 模拟），BUSY 回到采样空闲电平后再写 RAM。
    Command(kCmdPowerOn);
    if (!WaitBusyCycle(2000, 5000)) return false;
    panel_awake_ = true;
    return true;
}

bool Uc1679Display::Init() {
    xSemaphoreTake(mutex_, portMAX_DELAY);
    // 采样本模块 BUSY 空闲电平（FPC/适配板可能反相，与 SSD1619 同策略）。
    busy_idle_level_ = gpio_get_level(EPD_COLOR_BUSY_PIN);
    const bool ok = WakeAndConfigure();
    if (ok) {
        available_ = true;
        // 探测成功即深睡，保持显示、零功耗。
        const uint8_t sleep_mode = 0x01;  // retain RAM
        Command(kCmdDeepSleep, &sleep_mode, 1);
        panel_awake_ = false;
        ESP_LOGI(kTag,
                 "SSD1683-compatible color panel ready: CS=GPIO%d BUSY=GPIO%d "
                 "(idle_level=%d)",
                 EPD_COLOR_CS_PIN, EPD_COLOR_BUSY_PIN, busy_idle_level_);
    } else {
        available_ = false;
        ESP_LOGW(kTag,
                 "SSD1683-compatible color panel not responding (CS=GPIO%d BUSY=GPIO%d). "
                 "Dual-screen degraded to BW-only; set EPD_ENABLE_COLOR_SCREEN=0 "
                 "to silence this probe.",
                 EPD_COLOR_CS_PIN, EPD_COLOR_BUSY_PIN);
    }
    xSemaphoreGive(mutex_);
    return ok;
}

void Uc1679Display::Sleep() {
    if (!available_ || !panel_awake_) return;
    const uint8_t sleep_mode = 0x01;
    Command(kCmdDeepSleep, &sleep_mode, 1);
    panel_awake_ = false;
}

bool Uc1679Display::ShowFrame(const uint8_t* bw_plane,
                              const uint8_t* red_plane) {
    if (!available_ || !spi_) return false;
    // 全刷 ~16s；置位后 SSD1619 侧复位/初始化会等待本屏完成。
    if (g_uc1679_refreshing.exchange(true)) {
        ESP_LOGW(kTag, "ShowFrame already in progress, skip");
        return false;
    }
    xSemaphoreTake(mutex_, portMAX_DELAY);
    bool ok = false;
    do {
        if (!WakeAndConfigure()) break;

        // 兜底平面放 PSRAM（30KB 不能占用内部 RAM）；正常路径调用方总是
        // 传入完整平面，这里仅为防御 nullptr。
        static uint8_t* fallback_bw = nullptr;    // 全 0xFF = 全白
        static uint8_t* fallback_no_red = nullptr;  // 全 0x00 = 无红色
        if (!fallback_bw) {
            fallback_bw = static_cast<uint8_t*>(
                heap_caps_malloc(kPlaneBytes, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
            fallback_no_red = static_cast<uint8_t*>(
                heap_caps_malloc(kPlaneBytes, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
            if (fallback_bw) std::memset(fallback_bw, 0xFF, kPlaneBytes);
            if (fallback_no_red) std::memset(fallback_no_red, 0x00, kPlaneBytes);
        }
        if (!bw_plane) bw_plane = fallback_bw;
        if (!red_plane) red_plane = fallback_no_red;
        if (!bw_plane || !red_plane) {
            ESP_LOGE(kTag, "PSRAM OOM for fallback planes");
            break;
        }

        // B/W 平面：1=白 0=黑
        Command(kCmdDataStartBw);
        Data(bw_plane, kPlaneBytes);
        // RED 平面：1=红 0=非红
        Command(kCmdDataStartRed);
        Data(red_plane, kPlaneBytes);

        // 启动全刷波形并等待完成（必须检测到 BUSY：空闲 -> 忙 -> 空闲）。
        Command(kCmdMasterActivate);
        ok = WaitBusyCycle(2000, 30000);
        if (!ok) {
            ESP_LOGE(kTag, "Full refresh timeout");
        } else {
            ESP_LOGI(kTag, "Color full refresh complete");
        }
        Sleep();
    } while (false);
    xSemaphoreGive(mutex_);
    g_uc1679_refreshing.store(false);
    return ok;
}
