#include "epd_7in3f_panel.h"

#include <algorithm>
#include <cctype>
#include <cstring>
#include <string>

#include <esp_heap_caps.h>
#include <esp_log.h>
#include <esp_rom_sys.h>

namespace {
constexpr char kTag[] = "Epd7in3fPanel";

// SPI 时钟：参考工程（epd.h）用 digitalWrite 软件 SPI，实测约 200kHz；
// 这里保守取 2MHz（192KB 约 0.8s，相对 12~35s 波形可忽略）。
// 怀疑硬件 SPI 有时序/速率问题时可用 SetSpiMode(true) 切到软件 bit-bang。
constexpr int kSpiClockHz = 2 * 1000 * 1000;

// 帧内一个字节的两个半字节：高半字节 = 偶数列（左）像素。
inline uint8_t PackPixelPair(uint8_t left_index, uint8_t right_index) {
    return static_cast<uint8_t>((left_index << 4) | (right_index & 0x0F));
}
}  // namespace

std::atomic<bool> Epd7in3fPanel::g_refreshing{false};
bool Epd7in3fPanel::g_default_bitbang = false;

Epd7in3fPanel::~Epd7in3fPanel() {
    if (spi_) spi_bus_remove_device(spi_);
    if (bus_owned_) spi_bus_free(EPD_SPI_NUM);
    if (mutex_) vSemaphoreDelete(mutex_);
    heap_caps_free(dma_buffer_);
}

void Epd7in3fPanel::InitializeGpio() {
    // CS 输出拉高（未选中）；DC/RST 输出拉高。
    gpio_config_t outputs = {};
    outputs.pin_bit_mask =
        (1ULL << EPD_CS_PIN) | (1ULL << EPD_DC_PIN) | (1ULL << EPD_RST_PIN);
    outputs.mode = GPIO_MODE_OUTPUT;
    ESP_ERROR_CHECK(gpio_config(&outputs));
    gpio_set_level(EPD_CS_PIN, 1);
    gpio_set_level(EPD_DC_PIN, 1);
    gpio_set_level(EPD_RST_PIN, 1);

    // BUSY 输入上拉。官方 demo 语义为忙=0、空闲=1，浮空上拉读 1 恰好
    // 等于"没插屏"的空闲电平，不会误触发等待。
    gpio_config_t busy = {};
    busy.pin_bit_mask = 1ULL << EPD_BUSY_PIN;
    busy.mode = GPIO_MODE_INPUT;
    busy.pull_up_en = GPIO_PULLUP_ENABLE;
    ESP_ERROR_CHECK(gpio_config(&busy));
}

bool Epd7in3fPanel::InitializeBusAndDevice() {
    // 本板型的七彩屏是 EPD SPI 总线（SPI3_HOST）上唯一的设备，总线由本
    // 驱动负责初始化；SD 卡在独立的 SPI2_HOST。
    spi_bus_config_t bus = {};
    bus.mosi_io_num = EPD_MOSI_PIN;
    bus.miso_io_num = -1;
    bus.sclk_io_num = EPD_SCK_PIN;
    bus.max_transfer_sz = kDmaChunkBytes;
    const esp_err_t bus_err = spi_bus_initialize(EPD_SPI_NUM, &bus, SPI_DMA_CH_AUTO);
    if (bus_err != ESP_OK) {
        ESP_LOGE(kTag, "spi_bus_initialize failed: %s", esp_err_to_name(bus_err));
        return false;
    }
    bus_owned_ = true;

    spi_device_interface_config_t device = {};
    device.clock_speed_hz = kSpiClockHz;
    device.mode = 0;
    device.spics_io_num = -1;  // CS 手动控制，便于观察时序
    device.queue_size = 2;
    const esp_err_t err = spi_bus_add_device(EPD_SPI_NUM, &device, &spi_);
    if (err != ESP_OK) {
        ESP_LOGE(kTag, "spi_bus_add_device failed: %s", esp_err_to_name(err));
        spi_ = nullptr;
        return false;
    }
    return true;
}

void Epd7in3fPanel::HardwareReset() {
    // 官方 Reset()：高 20ms -> 低 1ms -> 高 20ms。这里把低电平拉宽到 10ms、
    // 复位后释放拉长到 50ms —— 7.3" 大屏上电/复位释放较慢（尤其经转接板
    // 供电不足时），留足余量避免初始化序列被丢掉。
    gpio_set_level(EPD_RST_PIN, 1);
    vTaskDelay(pdMS_TO_TICKS(20));
    gpio_set_level(EPD_RST_PIN, 0);
    vTaskDelay(pdMS_TO_TICKS(10));
    gpio_set_level(EPD_RST_PIN, 1);
    vTaskDelay(pdMS_TO_TICKS(50));
}

bool Epd7in3fPanel::SetSpiMode(bool bitbang) {
    if (!available_) {
        bitbang_ = bitbang;  // 未初始化时只记状态，Init() 会按它初始化
        return true;
    }
    if (bitbang == bitbang_) return true;
    xSemaphoreTake(mutex_, portMAX_DELAY);
    if (bitbang) {
        // 释放硬件 SPI，把 MOSI/SCK 收回到 GPIO 输出。
        if (spi_) {
            spi_bus_remove_device(spi_);
            spi_ = nullptr;
        }
        if (bus_owned_) {
            spi_bus_free(EPD_SPI_NUM);
            bus_owned_ = false;
        }
        gpio_reset_pin(EPD_MOSI_PIN);
        gpio_reset_pin(EPD_SCK_PIN);
        gpio_config_t out = {};
        out.pin_bit_mask = (1ULL << EPD_MOSI_PIN) | (1ULL << EPD_SCK_PIN);
        out.mode = GPIO_MODE_OUTPUT;
        out.pull_up_en = GPIO_PULLUP_DISABLE;
        out.pull_down_en = GPIO_PULLDOWN_DISABLE;
        ESP_ERROR_CHECK(gpio_config(&out));
        gpio_set_level(EPD_SCK_PIN, 0);
        gpio_set_level(EPD_MOSI_PIN, 0);
        bitbang_ = true;
        ESP_LOGW(kTag, "SPI 已切换为软件 bit-bang（1:1 复刻参考工程 epd.h，"
                       "每字节一个 CS 脉冲，约 200kHz）");
    } else {
        bitbang_ = false;
        if (InitializeBusAndDevice()) {
            ESP_LOGW(kTag, "SPI 已切换回硬件 SPI（%d Hz）", kSpiClockHz);
        } else {
            ESP_LOGE(kTag, "切回硬件 SPI 失败，保持 bit-bang");
            bitbang_ = true;
        }
    }
    xSemaphoreGive(mutex_);
    return bitbang_ == bitbang;
}

void Epd7in3fPanel::BitbangWriteByte(uint8_t byte) {
    // 与参考工程 DEV_SPI_WriteByte 一致：CS 拉低 -> 从 MSB 开始逐位
    // DIN=bit / SCK=1 / SCK=0 -> CS 拉高（每字节一个 CS 脉冲）。
    gpio_set_level(EPD_CS_PIN, 0);
    for (int i = 7; i >= 0; --i) {
        gpio_set_level(EPD_MOSI_PIN, (byte >> i) & 0x01);
        esp_rom_delay_us(1);
        gpio_set_level(EPD_SCK_PIN, 1);
        esp_rom_delay_us(1);
        gpio_set_level(EPD_SCK_PIN, 0);
    }
    gpio_set_level(EPD_CS_PIN, 1);
}

void Epd7in3fPanel::BitbangWrite(const uint8_t* data, size_t length) {
    if (bitbang_) {
        for (size_t i = 0; i < length; ++i) BitbangWriteByte(data[i]);
        return;
    }
    Data(data, length);
}

bool Epd7in3fPanel::WaitBusy(uint32_t timeout_ms) {
    // 官方 EPD_7IN3F_BusyHigh：BUSY=0 期间等待，回到 1 为空闲。
    const int idle_level = EPD73F_BUSY_IDLE_LEVEL;
    const TickType_t start = xTaskGetTickCount();
    while (gpio_get_level(EPD_BUSY_PIN) != idle_level) {
        if ((xTaskGetTickCount() - start) * portTICK_PERIOD_MS >= timeout_ms) {
            ESP_LOGE(kTag, "BUSY timeout after %lu ms (level=%d, idle=%d)",
                     static_cast<unsigned long>(timeout_ms),
                     gpio_get_level(EPD_BUSY_PIN), idle_level);
            return false;
        }
        vTaskDelay(pdMS_TO_TICKS(10));
    }
    return true;
}

Epd7in3fPanel::BusyProbe Epd7in3fPanel::SampleBusy(uint32_t duration_ms) {
    BusyProbe probe{};
    int last = gpio_get_level(EPD_BUSY_PIN);
    probe.last_level = last;
    if (last) probe.saw_high = true;
    else probe.saw_low = true;
    const TickType_t start = xTaskGetTickCount();
    while ((xTaskGetTickCount() - start) * portTICK_PERIOD_MS < duration_ms) {
        vTaskDelay(pdMS_TO_TICKS(10));
        const int level = gpio_get_level(EPD_BUSY_PIN);
        if (level != last) ++probe.transitions;
        if (level) probe.saw_high = true;
        else probe.saw_low = true;
        last = level;
    }
    probe.last_level = last;
    return probe;
}

bool Epd7in3fPanel::WaitBusyOrDelay(uint32_t timeout_ms, uint32_t fixed_delay_ms,
                                    const char* stage) {
    if (busy_reliable_) {
        if (WaitBusy(timeout_ms)) return true;
        // 第一次超时后即判定 BUSY 不可靠（转接板/排线可能没把 BUSY 引出，
        // 或面板处于深睡不驱动 BUSY），后续统一用固定延时推进时序，
        // 避免因一条不可靠的握手线彻底刷不了屏。
        busy_reliable_ = false;
        ESP_LOGW(kTag,
                 "%s: BUSY 线 %lums 未回到空闲，判定 BUSY 不可靠 —— "
                 "后续改用固定延时推进时序（仍能刷屏，只是无法感知波形结束）",
                 stage, static_cast<unsigned long>(timeout_ms));
    }
    vTaskDelay(pdMS_TO_TICKS(fixed_delay_ms));
    return true;
}

void Epd7in3fPanel::Command(uint8_t command, const uint8_t* data,
                            size_t length) {
    if (bitbang_) {
        gpio_set_level(EPD_DC_PIN, 0);
        esp_rom_delay_us(2);
        BitbangWriteByte(command);
        if (data && length) {
            gpio_set_level(EPD_DC_PIN, 1);
            esp_rom_delay_us(2);
            for (size_t i = 0; i < length; ++i) BitbangWriteByte(data[i]);
        }
        return;
    }
    if (!spi_) return;
    gpio_set_level(EPD_DC_PIN, 0);
    esp_rom_delay_us(1);
    gpio_set_level(EPD_CS_PIN, 0);
    spi_transaction_t transaction = {};
    transaction.length = 8;
    transaction.tx_buffer = &command;
    ESP_ERROR_CHECK(spi_device_polling_transmit(spi_, &transaction));
    gpio_set_level(EPD_CS_PIN, 1);
    if (data && length) Data(data, length);
}

void Epd7in3fPanel::Data(const uint8_t* data, size_t length) {
    if (bitbang_) {
        gpio_set_level(EPD_DC_PIN, 1);
        esp_rom_delay_us(2);
        for (size_t i = 0; i < length; ++i) BitbangWriteByte(data[i]);
        return;
    }
    if (!spi_) return;
    gpio_set_level(EPD_DC_PIN, 1);
    gpio_set_level(EPD_CS_PIN, 0);
    while (length > 0) {
        const size_t chunk = std::min(length, kDmaChunkBytes);
        std::memcpy(dma_buffer_, data, chunk);
        spi_transaction_t transaction = {};
        transaction.length = chunk * 8;
        transaction.tx_buffer = dma_buffer_;
        const esp_err_t err = spi_device_polling_transmit(spi_, &transaction);
        if (err != ESP_OK) {
            gpio_set_level(EPD_CS_PIN, 1);
            ESP_LOGE(kTag, "SPI data transmit failed: %s, chunk=%u",
                     esp_err_to_name(err), static_cast<unsigned>(chunk));
            return;
        }
        data += chunk;
        length -= chunk;
    }
    gpio_set_level(EPD_CS_PIN, 1);
}

bool Epd7in3fPanel::Init() {
    if (available_) return true;
    bitbang_ = g_default_bitbang;
    mutex_ = xSemaphoreCreateMutex();
    dma_buffer_ = static_cast<uint8_t*>(
        heap_caps_malloc(kDmaChunkBytes, MALLOC_CAP_DMA | MALLOC_CAP_INTERNAL));
    assert(mutex_ && dma_buffer_);
    InitializeGpio();
    if (bitbang_) {
        gpio_reset_pin(EPD_MOSI_PIN);
        gpio_reset_pin(EPD_SCK_PIN);
        gpio_config_t out = {};
        out.pin_bit_mask = (1ULL << EPD_MOSI_PIN) | (1ULL << EPD_SCK_PIN);
        out.mode = GPIO_MODE_OUTPUT;
        ESP_ERROR_CHECK(gpio_config(&out));
        gpio_set_level(EPD_SCK_PIN, 0);
        gpio_set_level(EPD_MOSI_PIN, 0);
        ESP_LOGW(kTag, "以软件 bit-bang SPI 启动（复刻参考工程时序）");
    } else if (!InitializeBusAndDevice()) {
        return false;
    }

    xSemaphoreTake(mutex_, portMAX_DELAY);
    // 前置诊断：把 RST 拉低让面板停在复位态 —— 此时面板不应驱动 BUSY，
    // 读到的电平就是这条线上「别人」的电平。GPIO 已使能内部上拉，正常情况下
    // 应该读到 1；若读到 0，说明 BUSY(GPIO12) 被外部拉死（FPC2 座仍插着
    // SSD1619 屏、或与 FPC3 并联的转接板把 BUSY 接地），这会彻底淹掉面板
    // 自己的 BUSY 信号，表现为「屏永远在忙、永远不出画面」。
    gpio_set_level(EPD_RST_PIN, 0);
    vTaskDelay(pdMS_TO_TICKS(50));
    {
        const BusyProbe idle_probe = SampleBusy(300);
        if (idle_probe.last_level == 0 && idle_probe.saw_high == false) {
            ESP_LOGE(kTag,
                     "硬件告警：面板处于复位态时 BUSY(GPIO%d) 仍恒为 0 —— "
                     "该线被外部拉死（多半是 FPC2 座上还插着屏，或 8P->50P "
                     "转接板把 BUSY 接地）。FPC2 与 FPC3 是并联的同一组信号，"
                     "接 FPC3 时 FPC2 座必须空置，否则屏幕不可能有画面。",
                     EPD_BUSY_PIN);
        }
    }

    // 探测：复位后采样 BUSY 线是否真的被面板驱动。
    //   · 出现低电平或电平跳变 -> 面板在响应（detected_=true）
    //   · 全程恒为 idle(1) 且无跳变 -> 只是上拉的浮空电平，不能作为判据
    HardwareReset();
    const BusyProbe probe = SampleBusy(1500);
    detected_ = probe.saw_low || probe.transitions > 0;
    if (detected_) {
        if (!WaitBusy(10000)) {
            busy_reliable_ = false;
            ESP_LOGW(kTag,
                     "BUSY 在复位后 10s 内未回到空闲（采样: low=%d high=%d "
                     "跳变=%d 末电平=%d）——转接板/排线的 BUSY 可能未正确引出，"
                     "后续改用固定延时时序",
                     probe.saw_low ? 1 : 0, probe.saw_high ? 1 : 0,
                     probe.transitions, probe.last_level);
        }
    } else {
        // BUSY 悬空：老固件会据此判定「面板未连接」并彻底放弃刷屏。实测
        // 部分 8P->50P 转接板不引出 BUSY，屏本身是好的，所以这里只降级
        // 为「不可靠」，仍然照常刷屏，靠固定延时保证波形走完。
        busy_reliable_ = false;
        ESP_LOGW(kTag,
                 "BUSY 线全程恒为 %d（无跳变）——面板未驱动 BUSY，"
                 "可能没插屏 / 未上电 / 转接板未引出 BUSY。仍按固定时序尝试刷屏",
                 probe.last_level);
    }

    // SPI 就绪即认为可用：即使 BUSY 不可靠也要允许刷屏，否则一处握手线
    // 问题会让整块屏完全没反应，无法定位。
    available_ = true;
    // 探测完成即深睡（官方 Sleep()：0x07 0xA5 后 RST 拉低），
    // 显示保持、功耗趋近 0；后续 ShowFrame 会重新复位初始化。
    const uint8_t deep_sleep = 0xA5;
    Command(0x07, &deep_sleep, 1);
    vTaskDelay(pdMS_TO_TICKS(10));
    gpio_set_level(EPD_RST_PIN, 0);
    ESP_LOGI(kTag,
             "7.3\" 7-color panel %s: %dx%d, frame=%u bytes "
             "(DIN=GPIO%d CLK=GPIO%d CS=GPIO%d DC=GPIO%d RST=GPIO%d "
             "BUSY=GPIO%d, idle=%d, busy_reliable=%d)",
             detected_ ? "detected" : "not detected (BUSY floating)",
             kWidth, kHeight, static_cast<unsigned>(kFrameBytes),
             EPD_MOSI_PIN, EPD_SCK_PIN, EPD_CS_PIN, EPD_DC_PIN,
             EPD_RST_PIN, EPD_BUSY_PIN, EPD73F_BUSY_IDLE_LEVEL,
             busy_reliable_ ? 1 : 0);
    if (!detected_) {
        ESP_LOGW(kTag,
                 "若屏幕完全没有画面：检查 FPC3 8pin 座与 8P->50P 转接板、"
                 "50pin FPC 是否插好，且 FPC2 座必须空置（FPC2 与 FPC3 并联，"
                 "插了别的屏会抢占 BUSY=GPIO%d / CS=GPIO%d）；"
                 "若 FPC2 座插的是 SSD1619 快刷屏，请改用 epd42 板型固件",
                 EPD_CS_PIN, EPD_BUSY_PIN);
    }
    xSemaphoreGive(mutex_);
    return true;
}

const char* Epd7in3fPanel::VariantName(Variant variant) {
    return variant == Variant::kSpectra6 ? "6color(E/Spectra6)"
                                         : "7color(F/ACeP)";
}

bool Epd7in3fPanel::TakeProbeResult(Variant* out) {
    if (!probe_hit_) return false;
    probe_hit_ = false;
    if (out) *out = probe_variant_;
    return true;
}

bool Epd7in3fPanel::ParseVariant(const std::string& text, Variant* out) {
    std::string s;
    for (char c : text) {
        // Variant names contain letters beyond 'f' (for example "6color").
        // Filtering with isxdigit() silently turned them into "6cr", causing
        // both advertised MCP values to be rejected. Keep all alphanumerics,
        // while still ignoring spaces/punctuation in spoken or UI input.
        if (isalnum(static_cast<unsigned char>(c)) == 0) {
            continue;
        }
        s.push_back(static_cast<char>(tolower(static_cast<unsigned char>(c))));
    }
    if (s.empty()) return false;
    if (s == "6color" || s == "6" || s == "e6" || s == "e" || s == "spectra6" ||
        s == "s6" || s == "liuse") {
        if (out) *out = Variant::kSpectra6;
        return true;
    }
    if (s == "7color" || s == "7" || s == "f" || s == "acep" || s == "acep7" ||
        s == "qicai") {
        if (out) *out = Variant::kAcep7;
        return true;
    }
    return false;
}

// 7.3" e-Paper (E) —— E Ink Spectra 6 六色。
// 移植自 E:\electron\project\7寸firmware\firmware\test\epd7in3.h
// （EPD_7in3E_init，原工程 Arduino/非 S3，此处改为 ESP-IDF SPI 直驱）。
// 与七彩 (F) 的关键差异：
//   0x01 只有 1 字节（F 是 6 字节）
//   0x06 = 6F 1F 17 49（F 是 6F 1F 1F 22）
//   0x30 = 0x03（PLL，F 是 0x3C）
//   0x84 = 0x01（F 是 0x00）
//   不写 0x13 / 0x41 / 0x82 / 0x86 / 0xE0 / 0xE6
bool Epd7in3fPanel::InitSpectra6() {
    // CMDH：读写使能寄存器（官方魔法字节）。
    const uint8_t cmdh[] = {0x49, 0x55, 0x20, 0x08, 0x09, 0x18};
    Command(0xAA, cmdh, sizeof(cmdh));

    const uint8_t r01 = 0x3F;
    Command(0x01, &r01, 1);
    const uint8_t r00[] = {0x5F, 0x69};
    Command(0x00, r00, sizeof(r00));
    const uint8_t r03[] = {0x00, 0x54, 0x00, 0x44};
    Command(0x03, r03, sizeof(r03));
    const uint8_t r05[] = {0x40, 0x1F, 0x1F, 0x2C};
    Command(0x05, r05, sizeof(r05));
    const uint8_t r06[] = {0x6F, 0x1F, 0x17, 0x49};
    Command(0x06, r06, sizeof(r06));
    const uint8_t r08[] = {0x6F, 0x1F, 0x1F, 0x22};
    Command(0x08, r08, sizeof(r08));
    const uint8_t r30 = 0x03;             // PLL
    Command(0x30, &r30, 1);
    const uint8_t r50 = 0x3F;             // CDI
    Command(0x50, &r50, 1);
    const uint8_t r60[] = {0x02, 0x00};   // TCON
    Command(0x60, r60, sizeof(r60));
    // 分辨率 800x480（0x0320 x 0x01E0）。
    const uint8_t r61[] = {0x03, 0x20, 0x01, 0xE0};
    Command(0x61, r61, sizeof(r61));
    const uint8_t r84 = 0x01;
    Command(0x84, &r84, 1);
    const uint8_t re3 = 0x2F;             // PWS
    Command(0xE3, &re3, 1);
    return true;
}

// 7.3" e-Paper (F) —— ACeP 七彩（原实现，逐条移植自官方 epd7in3f demo）。
bool Epd7in3fPanel::InitAcep7() {
    // CMDH：读写使能寄存器（官方魔法字节）。
    const uint8_t cmdh[] = {0x49, 0x55, 0x20, 0x08, 0x09, 0x18};
    Command(0xAA, cmdh, sizeof(cmdh));

    // PSR / PWR 等（官方序列原样）。
    const uint8_t r01[] = {0x3F, 0x00, 0x32, 0x2A, 0x0E, 0x2A};
    Command(0x01, r01, sizeof(r01));
    const uint8_t r00[] = {0x5F, 0x69};
    Command(0x00, r00, sizeof(r00));
    const uint8_t r03[] = {0x00, 0x54, 0x00, 0x44};
    Command(0x03, r03, sizeof(r03));
    const uint8_t r05[] = {0x40, 0x1F, 0x1F, 0x2C};
    Command(0x05, r05, sizeof(r05));
    const uint8_t r06[] = {0x6F, 0x1F, 0x1F, 0x22};
    Command(0x06, r06, sizeof(r06));
    const uint8_t r08[] = {0x6F, 0x1F, 0x1F, 0x22};
    Command(0x08, r08, sizeof(r08));
    const uint8_t r13[] = {0x00, 0x04};  // IPC
    Command(0x13, r13, sizeof(r13));
    const uint8_t r30 = 0x3C;            // PLL
    Command(0x30, &r30, 1);
    const uint8_t r41 = 0x00;            // TSE
    Command(0x41, &r41, 1);
    const uint8_t r50 = 0x3F;            // CDI
    Command(0x50, &r50, 1);
    const uint8_t r60[] = {0x02, 0x00};  // TCON
    Command(0x60, r60, sizeof(r60));
    // 分辨率 800x480（0x0320 x 0x01E0）。
    const uint8_t r61[] = {0x03, 0x20, 0x01, 0xE0};
    Command(0x61, r61, sizeof(r61));
    const uint8_t r82 = 0x1E;            // VCOM_DC? 官方 AGID 前
    Command(0x82, &r82, 1);
    const uint8_t r84 = 0x00;
    Command(0x84, &r84, 1);
    const uint8_t r86 = 0x00;            // AGID
    Command(0x86, &r86, 1);
    const uint8_t re3 = 0x2F;            // PWS
    Command(0xE3, &re3, 1);
    const uint8_t re0 = 0x00;            // CCSET
    Command(0xE0, &re0, 1);
    const uint8_t re6 = 0x00;            // TSSET
    Command(0xE6, &re6, 1);
    return true;
}

bool Epd7in3fPanel::WakeAndConfigure() {
    // 深睡前 RST 被拉低，这里释放并重新复位、写入对应型号的初始化序列。
    gpio_set_level(EPD_RST_PIN, 1);
    vTaskDelay(pdMS_TO_TICKS(50));
    HardwareReset();
    // 复位后等 BUSY 空闲；BUSY 不可靠时退化为 800ms 固定延时后继续
    // （200ms 对 7.3" 大屏不够，屏还没起来就把初始化序列丢了 —— 这正是
    //  BUSY 恒高、屏毫无反应的常见原因）。
    WaitBusyOrDelay(10000, 800, "wake/reset");

    const bool six_color = variant_ == Variant::kSpectra6;
    ESP_LOGI(kTag, "初始化序列: 7.3\" %s（%s）",
             six_color ? "e-Paper (E) Spectra 6 六色" : "e-Paper (F) ACeP 七彩",
             busy_reliable_ ? "BUSY 可靠" : "BUSY 不可靠-固定延时");
    return six_color ? InitSpectra6() : InitAcep7();
}

bool Epd7in3fPanel::TurnOnDisplay() {
    // 官方 TurnOnDisplay()：上电 -> 0x12 刷新（带 1 字节 dummy 数据）->
    // 下电，每步等待 BUSY 回到空闲。
    Command(0x04);  // POWER_ON
    // 上电后给足 1s：大屏电荷泵升压需要时间，欠压时更慢。
    WaitBusyOrDelay(10000, 1000, "power on");

    // 诊断：面板若真的收到命令并开始上电，BUSY 会被拉低；全程恒高（无跳变）
    // 说明面板根本没响应（未上电 / 未连接 / CS 没选中 / 型号序列不对）。
    // 采样 5s（欠压屏响应会明显变慢，1.5s 容易漏判）。
    {
        const BusyProbe p = SampleBusy(5000);
        // 只要出现过高电平或跳变，就说明面板在真正驱动这条 BUSY 线。
        if (p.saw_low || p.transitions > 0) busy_responded_ = true;
        ESP_LOGW(kTag,
                 "诊断 POWER_ON 后 BUSY 采样 5s: low=%d high=%d 跳变=%d "
                 "末电平=%d -> %s",
                 p.saw_low, p.saw_high, p.transitions, p.last_level,
                 (p.saw_low || p.transitions > 0)
                     ? "面板有响应（确定已连接上电）"
                     : "面板无响应！检查屏供电/FPC3 与 8P->50P 转接板/"
                       "50pin 排线、FPC2 座必须空置、型号是否选对");
    }

    if (variant_ == Variant::kSpectra6) {
        // 六色 (E) 在上电后必须再写一次 0x06（官方 EPD_7in3E_Show 原样），
        // 七彩 (F) 不需要这一步。
        const uint8_t r06[] = {0x6F, 0x1F, 0x17, 0x49};
        Command(0x06, r06, sizeof(r06));
    }

    Command(0x12);   // DISPLAY_REFRESH
    const uint8_t dummy = 0x00;
    Data(&dummy, 1);
    {
        const BusyProbe p = SampleBusy(3000);
        if (p.saw_low || p.transitions > 0) busy_responded_ = true;
        ESP_LOGW(kTag,
                 "诊断 DISPLAY_REFRESH 后 BUSY 采样 3s: low=%d high=%d 跳变=%d "
                 "末电平=%d -> %s",
                 p.saw_low, p.saw_high, p.transitions, p.last_level,
                 (p.saw_low || p.transitions > 0) ? "正在刷波形（屏幕应可见闪动）"
                                                  : "未进入刷新，屏幕不会有任何变化");
    }
    // 全刷波形：七彩标称 35s；六色标称 12s（18s 固定延时兜底）。
    WaitBusyOrDelay(kRefreshTimeoutMs,
                    variant_ == Variant::kSpectra6 ? kFixedRefreshDelayMsS6
                                                   : kFixedRefreshDelayMs,
                    "full refresh");

    Command(0x02);   // POWER_OFF
    Data(&dummy, 1);
    WaitBusyOrDelay(10000, 300, "power off");
    return true;
}

bool Epd7in3fPanel::ShowFrame(const uint8_t* packed_frame) {
    if (!available_ || !spi_) return false;
    if (!packed_frame) return false;
    // 全刷约 35s；置位后其它刷新请求直接跳过。
    if (g_refreshing.exchange(true)) {
        ESP_LOGW(kTag, "ShowFrame already in progress, skip");
        return false;
    }
    xSemaphoreTake(mutex_, portMAX_DELAY);
    bool ok = ShowFrameOnce(packed_frame);

    // 自动型号探测：首次开机（NVS 无 variant）时，若这一轮 BUSY 全程没有
    // 任何跳变 —— 说明面板根本没被这套初始化序列驱动起来（型号不匹配时
    // 屏会一直把 BUSY 拉低、完全无画面）——就自动换成另一种序列再刷一次。
    if (auto_probe_) {
        auto_probe_ = false;  // 无论成败都只探测一轮，避免每次刷新翻倍
        if (!busy_responded_) {
            const Variant next = variant_ == Variant::kSpectra6
                                     ? Variant::kAcep7
                                     : Variant::kSpectra6;
            ESP_LOGW(kTag,
                     "自动型号探测：%s 序列下 BUSY 全程无跳变（屏未响应）——"
                     "改用 %s 序列重新刷一次（本次会花两倍时间）",
                     VariantName(variant_), VariantName(next));
            variant_ = next;
            busy_responded_ = false;
            ok = ShowFrameOnce(packed_frame);
            if (busy_responded_) {
                probe_variant_ = variant_;
                probe_hit_ = true;
                ESP_LOGW(kTag, "自动型号探测命中：本屏 = %s（已交给板级写入 NVS）",
                         VariantName(variant_));
            } else {
                ESP_LOGE(kTag,
                         "自动型号探测失败：两种序列下 BUSY 均无跳变。"
                         "BUSY=GPIO%d（上拉输入）恒为 %d —— 若恒为 0，多半是 "
                         "FPC2 座仍插着屏（与 FPC3 并联抢 BUSY）或转接板把 BUSY "
                         "拉死；若恒为 1，则是 BUSY 未引出/屏未上电。"
                         "硬件确认前屏幕不会有画面。",
                         EPD_BUSY_PIN, gpio_get_level(EPD_BUSY_PIN));
            }
        }
    }
    xSemaphoreGive(mutex_);
    g_refreshing.store(false);
    return ok;
}

bool Epd7in3fPanel::ShowFrameOnce(const uint8_t* packed_frame) {
    bool ok = false;
    busy_responded_ = false;
    do {
        if (!WakeAndConfigure()) {
            ESP_LOGE(kTag, "面板初始化失败");
            break;
        }
        // 全帧数据：192000 字节。
        Command(0x10);
        Data(packed_frame, kFrameBytes);
        ok = TurnOnDisplay();
        if (ok) {
            ESP_LOGI(kTag, "7-color full refresh complete");
        }
        // 官方 Sleep()：0x07 0xA5 -> 延时 -> RST 拉低。显示保持。
        const uint8_t deep_sleep = 0xA5;
        Command(0x07, &deep_sleep, 1);
        vTaskDelay(pdMS_TO_TICKS(10));
        gpio_set_level(EPD_RST_PIN, 0);
    } while (false);
    return ok;
}

bool Epd7in3fPanel::ShowPlanes(const uint8_t* bw_plane,
                               const uint8_t* yellow_plane,
                               const uint8_t* red_plane) {
    if (!available_) return false;
    if (!bw_plane) return false;
    // 帧缓冲放 PSRAM（192KB 不能占用内部 RAM）。
    static uint8_t* packed = nullptr;
    if (!packed) {
        packed = static_cast<uint8_t*>(
            heap_caps_malloc(kFrameBytes, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
        if (!packed) {
            ESP_LOGE(kTag, "PSRAM OOM for %u-byte frame",
                     static_cast<unsigned>(kFrameBytes));
            return false;
        }
    }
    // 色索引按面板型号取（六色 2=黄 3=红；七彩 4=红 5=黄）。
    const uint8_t idx_red = variant_ == Variant::kSpectra6 ? kS6Red : kColorRed;
    const uint8_t idx_yellow =
        variant_ == Variant::kSpectra6 ? kS6Yellow : kColorYellow;
    const uint8_t idx_white =
        variant_ == Variant::kSpectra6 ? kS6White : kColorWhite;
    const uint8_t idx_black =
        variant_ == Variant::kSpectra6 ? kS6Black : kColorBlack;
    // 逐像素映射：红 > 黄 > 黑白。平面行优先 MSB 左（1=有效）。
    for (int y = 0; y < kHeight; ++y) {
        const uint8_t* bw_row = bw_plane + static_cast<size_t>(y) * kPlaneStride;
        const uint8_t* yellow_row =
            yellow_plane ? yellow_plane + static_cast<size_t>(y) * kPlaneStride
                         : nullptr;
        const uint8_t* red_row =
            red_plane ? red_plane + static_cast<size_t>(y) * kPlaneStride
                      : nullptr;
        uint8_t* out_row = packed + static_cast<size_t>(y) * kStride;
        for (int x = 0; x < kWidth; x += 2) {
            const auto pixel = [&](int px) -> uint8_t {
                const uint8_t mask = static_cast<uint8_t>(0x80 >> (px & 7));
                if (red_row && (red_row[px >> 3] & mask))
                    return idx_red;
                if (yellow_row && (yellow_row[px >> 3] & mask))
                    return idx_yellow;
                return (bw_row[px >> 3] & mask) ? idx_white : idx_black;
            };
            out_row[x >> 1] = PackPixelPair(pixel(x), pixel(x + 1));
        }
    }
    return ShowFrame(packed);
}

// 规范色索引（0黑1白2黄3红4蓝5绿）-> 本面板原生索引的 16 项查表。
// 六色 Spectra 6 与规范编号一致，直接透传；ACeP 七彩面板编号不同
// （0黑1白2绿3蓝4红5黄6橙），要换算。6..15 一律落到白。
static void BuildNormalizedIndexLut(bool spectra6, uint8_t lut[16]) {
    for (int i = 0; i < 16; ++i) lut[i] = Epd7in3fPanel::kS6White;
    lut[0] = Epd7in3fPanel::kS6Black;
    lut[1] = Epd7in3fPanel::kS6White;
    if (spectra6) {
        lut[2] = Epd7in3fPanel::kS6Yellow;
        lut[3] = Epd7in3fPanel::kS6Red;
        lut[4] = Epd7in3fPanel::kS6Blue;
        lut[5] = Epd7in3fPanel::kS6Green;
    } else {
        lut[2] = Epd7in3fPanel::kColorYellow;
        lut[3] = Epd7in3fPanel::kColorRed;
        lut[4] = Epd7in3fPanel::kColorBlue;
        lut[5] = Epd7in3fPanel::kColorGreen;
    }
}

bool Epd7in3fPanel::ShowNormalizedFrame(const uint8_t* norm_frame) {
    if (!available_ || !norm_frame) return false;
    static uint8_t* remapped = nullptr;
    if (!remapped) {
        remapped = static_cast<uint8_t*>(
            heap_caps_malloc(kFrameBytes, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
        if (!remapped) {
            ESP_LOGE(kTag, "PSRAM OOM for %u-byte remap buffer",
                     static_cast<unsigned>(kFrameBytes));
            return false;
        }
    }
    uint8_t lut[16];
    BuildNormalizedIndexLut(variant_ == Variant::kSpectra6, lut);
    for (size_t i = 0; i < kFrameBytes; ++i) {
        const uint8_t b = norm_frame[i];
        remapped[i] = static_cast<uint8_t>((lut[b >> 4] << 4) | lut[b & 0x0F]);
    }

    const Variant before = variant_;
    bool ok = ShowFrame(remapped);

    // 首次开机的自动型号探测可能在 ShowFrame 内部换掉了 variant_ ——
    // 那样刚才的索引换算就作废了（颜色会错），按新型号再算一遍并重刷。
    if (variant_ != before) {
        ESP_LOGW(kTag, "型号探测切到了 %s，按新索引重映射并重刷",
                 VariantName(variant_));
        BuildNormalizedIndexLut(variant_ == Variant::kSpectra6, lut);
        for (size_t i = 0; i < kFrameBytes; ++i) {
            const uint8_t b = norm_frame[i];
            remapped[i] =
                static_cast<uint8_t>((lut[b >> 4] << 4) | lut[b & 0x0F]);
        }
        ok = ShowFrame(remapped);
    }
    return ok;
}

bool Epd7in3fPanel::Clear(uint8_t color_index) {
    if (!available_) return false;
    static uint8_t* packed = nullptr;
    if (!packed) {
        packed = static_cast<uint8_t*>(
            heap_caps_malloc(kFrameBytes, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
        if (!packed) return false;
    }
    const uint8_t pair = PackPixelPair(color_index, color_index);
    std::memset(packed, pair, kFrameBytes);
    return ShowFrame(packed);
}

bool Epd7in3fPanel::ShowColorBars() {
    if (!available_) return false;
    static uint8_t* packed = nullptr;
    if (!packed) {
        packed = static_cast<uint8_t*>(
            heap_caps_malloc(kFrameBytes, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
        if (!packed) return false;
    }
    // 色块测试条：每列 100px，共 8 列。
    // 六色：黑/黄/红/蓝/绿/白/白/白；七彩：黑/蓝/绿/橙/红/黄/白/白。
    constexpr int kColumnPixels = kWidth / 8;  // 100
    const uint8_t columns_s6[8] = {kS6Black, kS6Yellow, kS6Red, kS6Blue,
                                   kS6Green, kS6White,  kS6White, kS6White};
    const uint8_t columns_f7[8] = {kColorBlack, kColorBlue,  kColorGreen,
                                   kColorOrange, kColorRed, kColorYellow,
                                   kColorWhite, kColorWhite};
    const uint8_t* columns =
        variant_ == Variant::kSpectra6 ? columns_s6 : columns_f7;
    for (int y = 0; y < kHeight; ++y) {
        uint8_t* out_row = packed + static_cast<size_t>(y) * kStride;
        for (int x = 0; x < kWidth; x += 2) {
            const int column_left = x / kColumnPixels;
            const int column_right = (x + 1) / kColumnPixels;
            out_row[x >> 1] =
                PackPixelPair(columns[column_left], columns[column_right]);
        }
    }
    return ShowFrame(packed);
}

bool Epd7in3fPanel::BlindTest() {
    if (!available_) {
        ESP_LOGW(kTag, "盲刷自检：面板未初始化，跳过");
        return false;
    }
    static uint8_t* packed = nullptr;
    if (!packed) {
        packed = static_cast<uint8_t*>(
            heap_caps_malloc(kFrameBytes, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
        if (!packed) return false;
    }
    const bool six = variant_ == Variant::kSpectra6;
    const uint8_t idx_black = six ? kS6Black : kColorBlack;
    const uint8_t idx_white = six ? kS6White : kColorWhite;
    ESP_LOGW(kTag,
             "盲刷自检开始（%s）：全程忽略 BUSY、按固定延时刷 全黑 -> 全白 -> "
             "色条 三帧。屏幕应有两次明显整体翻转。若毫无变化 = 命令没进屏"
             "（供电/接线/脚序问题）；若有翻转但颜色不对 = 换另一种型号再试",
             VariantName(variant_));

    struct Step {
        const char* name;
        uint8_t left;
        uint8_t right;
        bool bars;
    };
    const uint8_t columns_s6[8] = {kS6Black, kS6Yellow, kS6Red, kS6Blue,
                                   kS6Green, kS6White,  kS6White, kS6White};
    const uint8_t columns_f7[8] = {kColorBlack, kColorBlue,  kColorGreen,
                                   kColorOrange, kColorRed, kColorYellow,
                                   kColorWhite, kColorWhite};
    const uint8_t* columns = six ? columns_s6 : columns_f7;
    const Step steps[3] = {{"全黑", idx_black, idx_black, false},
                           {"全白", idx_white, idx_white, false},
                           {"色条", 0, 0, true}};
    for (const Step& step : steps) {
        if (step.bars) {
            constexpr int kColumnPixels = kWidth / 8;
            for (int y = 0; y < kHeight; ++y) {
                uint8_t* out_row = packed + static_cast<size_t>(y) * kStride;
                for (int x = 0; x < kWidth; x += 2) {
                    out_row[x >> 1] = PackPixelPair(columns[x / kColumnPixels],
                                                    columns[(x + 1) / kColumnPixels]);
                }
            }
        } else {
            std::memset(packed, PackPixelPair(step.left, step.right),
                        kFrameBytes);
        }
        ESP_LOGW(kTag, "盲刷自检：开始刷 [%s]（请盯住屏幕）", step.name);
        ShowFrame(packed);
        ESP_LOGW(kTag, "盲刷自检：[%s] 刷新流程结束", step.name);
        vTaskDelay(pdMS_TO_TICKS(5000));
    }
    ESP_LOGW(kTag,
             "盲刷自检结束。屏幕全程无任何变化 -> 屏没收到命令，查供电/排线/"
             "脚序（用 self.epaper.probe_pins 核对）；有翻转 -> 命令已进屏，"
             "用 self.epaper.set_panel_variant 换另一种型号再跑一次");
    return true;
}

void Epd7in3fPanel::Sleep() {
    if (!available_) return;
    const uint8_t deep_sleep = 0xA5;
    Command(0x07, &deep_sleep, 1);
    vTaskDelay(pdMS_TO_TICKS(10));
    gpio_set_level(EPD_RST_PIN, 0);
}

bool Epd7in3fPanel::ProbePins(uint32_t hold_ms) {
    if (!available_) {
        ESP_LOGW(kTag, "接线自检：面板未初始化，跳过");
        return false;
    }
    if (IsRefreshing()) {
        ESP_LOGW(kTag, "接线自检：正在刷屏，请等刷新结束后再试");
        return false;
    }
    struct ProbeCtx {
        Epd7in3fPanel* panel;
        uint32_t hold_ms;
    };
    auto* ctx = new ProbeCtx{this, hold_ms};
    auto entry = [](void* arg) {
        auto* c = static_cast<ProbeCtx*>(arg);
        c->panel->ProbePinsTask(c->hold_ms);
        delete c;
        vTaskDelete(nullptr);
    };
    if (xTaskCreate(entry, "epd_probe", 4096, ctx, 4, nullptr) != pdTRUE) {
        delete ctx;
        return false;
    }
    return true;
}

void Epd7in3fPanel::ProbePinsTask(uint32_t hold_ms) {
    xSemaphoreTake(mutex_, portMAX_DELAY);
    const gpio_num_t pins[3] = {EPD_RST_PIN, EPD_DC_PIN, EPD_CS_PIN};
    const char* names[3] = {"RST", "DC", "CS"};
    ESP_LOGW(kTag,
             "接线自检开始：每 %u ms 翻一档，共 6 档。拿万用表量转接板侧 "
             "BUSY/RST/DC/CS/SCLK/DIN/5V/GND 各脚对 GND 的电压，与下面日志对照",
             static_cast<unsigned>(hold_ms));
    for (int i = 0; i < 3; ++i) {
        for (int level = 0; level <= 1; ++level) {
            gpio_set_level(pins[i], level);
            ESP_LOGW(kTag, "探针 %s(GPIO%d) = %d -> 转接板 %s 脚此刻应为 %s V "
                           "(BUSY=%d)",
                     names[i], static_cast<int>(pins[i]), level, names[i],
                     level ? "3.3" : "0", gpio_get_level(EPD_BUSY_PIN));
            vTaskDelay(pdMS_TO_TICKS(hold_ms));
        }
    }
    gpio_set_level(EPD_CS_PIN, 1);
    gpio_set_level(EPD_DC_PIN, 1);
    gpio_set_level(EPD_RST_PIN, 1);
    ESP_LOGW(kTag,
             "接线自检结束（CS=1 DC=1 RST=1 已恢复）。判读：电压跟随=该脚通；"
             "完全不动=断线/虚焊/排线没插到底；变化出现在别的脚=排线反插（脚序镜像）");
    xSemaphoreGive(mutex_);
}
