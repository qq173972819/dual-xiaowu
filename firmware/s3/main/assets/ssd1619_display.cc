#include "ssd1619_display.h"

#include <algorithm>
#include <cassert>
#include <cstring>

#include <esp_heap_caps.h>
#include <esp_log.h>
#include <esp_rom_sys.h>

#include "assets/lang_config.h"
#include "esp_lvgl_port.h"

#if EPD_ENABLE_COLOR_SCREEN
#include "uc1679_display.h"
#endif

namespace {
constexpr char kTag[] = "Ssd1619Display";
constexpr size_t kLvglBufferBytes = DISPLAY_WIDTH * 24 * sizeof(uint16_t);
constexpr size_t kDmaChunkBytes = 4096;

// SSD1619 partial waveform — 2 驱动相位，比原版 1 相位强得多，
// 墨水粒子被充分翻转，残影大幅减少同时不会闪烁。
// LUT0/LUT3 (unchanged) = VSS 全程，不变像素不驱动不闪烁。
// LUT1 (B→W): VSH→VSS 两相位，黑粒子翻到白侧
// LUT2 (W→B): VSL→VSS 两相位，白粒子翻到黑侧
// 0x32 寄存器正好 70 字节（5×7 LUT + 7×5 timing + VCOM）。
constexpr uint8_t kPartialLut[70] = {
    0x00,0x00,0x00,0x00,0x00,0x00,0x00,  // LUT0: unchanged black
    0xa0,0x00,0x00,0x00,0x00,0x00,0x00,  // LUT1: B→W (phase1=VSH, phase2=VSS)
    0x50,0x00,0x00,0x00,0x00,0x00,0x00,  // LUT2: W→B (phase1=VSL, phase2=VSS)
    0x00,0x00,0x00,0x00,0x00,0x00,0x00,  // LUT3: unchanged white
    0x00,0x00,0x00,0x00,0x00,0x00,0x00,  // LUT4: VCOM
    // group timing (7 groups × 5 bytes each)
    0x0a,0x00,0x00,0x00,0x00,
    0x00,0x00,0x00,0x00,0x00,
    0x00,0x00,0x00,0x00,0x00,
    0x00,0x00,0x00,0x00,0x00,
    0x00,0x00,0x00,0x00,0x00,
    0x00,0x00,0x00,0x00,0x00,
    0x00,0x00,0x00,0x00,0x00,
};
}

Ssd1619Display::Ssd1619Display()
    : LcdDisplay(nullptr, nullptr, DISPLAY_WIDTH, DISPLAY_HEIGHT) {
    InitializeSpi();
    mutex_ = xSemaphoreCreateMutex();
    frame_ = static_cast<uint8_t*>(heap_caps_malloc(kFrameBytes, MALLOC_CAP_SPIRAM));
    tx_frame_ = static_cast<uint8_t*>(heap_caps_malloc(kFrameBytes, MALLOC_CAP_SPIRAM));
    panel_frame_ = static_cast<uint8_t*>(heap_caps_malloc(kFrameBytes, MALLOC_CAP_SPIRAM));
    dma_buffer_ = static_cast<uint8_t*>(heap_caps_malloc(
        kDmaChunkBytes, MALLOC_CAP_DMA | MALLOC_CAP_INTERNAL));
    assert(mutex_ && frame_ && tx_frame_ && panel_frame_ && dma_buffer_);
    std::memset(frame_, 0xff, kFrameBytes);  // SSD1619: 1=white, 0=black.
    std::memcpy(tx_frame_, frame_, kFrameBytes);
    std::memcpy(panel_frame_, frame_, kFrameBytes);

    lv_init();
    lvgl_port_cfg_t port_cfg = ESP_LVGL_PORT_INIT_CONFIG();
    port_cfg.task_priority = 2;
    port_cfg.timer_period_ms = 50;
    ESP_ERROR_CHECK(lvgl_port_init(&port_cfg));

    lvgl_port_lock(0);
    display_ = lv_display_create(DISPLAY_WIDTH, DISPLAY_HEIGHT);
    lv_display_set_color_format(display_, LV_COLOR_FORMAT_RGB565);
    lv_display_set_default(display_);
    lv_display_set_user_data(display_, this);
    lv_display_set_flush_cb(display_, FlushCallback);
    auto* lvgl_buffer = static_cast<uint8_t*>(
        heap_caps_malloc(kLvglBufferBytes, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
    assert(lvgl_buffer);
    lv_display_set_buffers(display_, lvgl_buffer, nullptr, kLvglBufferBytes,
                           LV_DISPLAY_RENDER_MODE_PARTIAL);
    lvgl_port_unlock();

    xTaskCreatePinnedToCore(RefreshTaskEntry, "ssd1619_refresh", 4096, this, 3,
                            &refresh_task_, 1);
    // Do not physically refresh the initial all-white buffer. The dashboard
    // below must be the first RAM image sent to the panel; otherwise a failed
    // or overlapping second update leaves a perfectly valid but blank screen.
    dashboard_.SetRefreshCallback(
        [this](bool partial) { RequestRefresh(partial); });
    dashboard_.Show(EpaperDashboard::Page::kHome);
}

Ssd1619Display::~Ssd1619Display() {
    if (refresh_task_) vTaskDelete(refresh_task_);
    if (spi_) spi_bus_remove_device(spi_);
    if (mutex_) vSemaphoreDelete(mutex_);
    heap_caps_free(frame_);
    heap_caps_free(tx_frame_);
    heap_caps_free(panel_frame_);
    heap_caps_free(dma_buffer_);
}

void Ssd1619Display::SetEmotion(const char* emotion) { (void)emotion; }

void Ssd1619Display::SetStatus(const char* status) {
    if (status && std::strcmp(status, Lang::Strings::LISTENING) == 0) {
        // LISTENING also follows the end of TTS. That transition must not
        // reopen the conversation after the user has returned to the menu.
        if (assistant_response_active_)
            assistant_response_active_ = false;
        else
            dashboard_.BeginConversation();
    } else if (status && std::strcmp(status, Lang::Strings::STANDBY) == 0) {
        assistant_response_active_ = false;
    }
    // CONNECTING/SPEAKING/STANDBY and clock/status updates intentionally do
    // not invalidate e-paper. Conversation text updates arrive separately.
}

void Ssd1619Display::SetChatMessage(const char* role, const char* content) {
    if (!role || !content || content[0] == '\0') return;
    if (std::strcmp(role, "user") == 0 ||
        std::strcmp(role, "assistant") == 0) {
        // TTS delivers complete sentences. Each visible sentence is committed
        // using the same-page SSD1619 partial waveform. If the user returned
        // home, cache late packets without allowing them to steal the menu.
        const bool visible = dashboard_.IsConversation();
        dashboard_.SetConversationMessage(role, content, visible);
        if (std::strcmp(role, "assistant") == 0) {
            assistant_response_active_ = true;
            if (visible)
                ESP_LOGI(kTag, "Assistant sentence queued for partial refresh");
        }
    }
}

void Ssd1619Display::ClearChatMessages() {
    // Idle transitions in Application clear LCD bubbles. Preserve the last
    // completed e-paper conversation until the user returns to the menu.
}

void Ssd1619Display::SetupUI() {
    // Never create LcdDisplay's animated status/chat UI on e-paper. It has a
    // clock and status widgets that invalidate continuously and would both
    // cover the dashboard and wear the panel with repeated full refreshes.
    setup_ui_called_ = true;
}

void Ssd1619Display::SetTheme(Theme* theme) {
    // Assets refreshes the selected theme after Wi-Fi connects. The inherited
    // LcdDisplay implementation styles mute/status/battery widgets, but those
    // widgets intentionally do not exist on this event-driven e-paper UI.
    // Keep the theme selection without touching null LCD objects or scheduling
    // a physical display refresh.
    current_theme_ = theme;
}

void Ssd1619Display::ShowNotification(const char* notification,
                                      int duration_ms) {
    // Network scans and short-lived LCD toasts must not replace the e-paper
    // home menu or cause a full refresh. They remain available in serial logs.
    (void)notification;
    (void)duration_ms;
}

void Ssd1619Display::InitializeSpi() {
    gpio_config_t outputs = {};
    outputs.pin_bit_mask = (1ULL << EPD_CS_PIN) | (1ULL << EPD_DC_PIN) |
                           (1ULL << EPD_RST_PIN);
    outputs.mode = GPIO_MODE_OUTPUT;
    ESP_ERROR_CHECK(gpio_config(&outputs));
    gpio_set_level(EPD_CS_PIN, 1);
    gpio_set_level(EPD_DC_PIN, 1);
    gpio_set_level(EPD_RST_PIN, 1);

    gpio_config_t busy = {};
    busy.pin_bit_mask = 1ULL << EPD_BUSY_PIN;
    busy.mode = GPIO_MODE_INPUT;
    busy.pull_up_en = GPIO_PULLUP_ENABLE;
    ESP_ERROR_CHECK(gpio_config(&busy));

    spi_bus_config_t bus = {};
    bus.mosi_io_num = EPD_MOSI_PIN;
    bus.miso_io_num = -1;
    bus.sclk_io_num = EPD_SCK_PIN;
    bus.max_transfer_sz = kFrameBytes + 8;
    ESP_ERROR_CHECK(spi_bus_initialize(EPD_SPI_NUM, &bus, SPI_DMA_CH_AUTO));

    spi_device_interface_config_t device = {};
    device.clock_speed_hz = 4 * 1000 * 1000;
    device.mode = 0;
    // Drive FPC1 CS2 explicitly. This also makes CS timing observable and
    // avoids relying on the SPI GPIO matrix for GPIO39.
    device.spics_io_num = -1;
    device.queue_size = 2;
    ESP_ERROR_CHECK(spi_bus_add_device(EPD_SPI_NUM, &device, &spi_));
    ESP_LOGI(kTag, "SSD1619 SPI ready: %dx%d, frame=%u bytes", DISPLAY_WIDTH,
             DISPLAY_HEIGHT, static_cast<unsigned>(kFrameBytes));
}

void Ssd1619Display::HardwareReset() {
    // 共享 RST（GPIO11）复位会同时打断三色屏正在进行的 ~16s 全刷波形。
    // 三色屏刷新期间先让它完成（最多 ~25s），再做硬复位。
#if EPD_ENABLE_COLOR_SCREEN
    if (Uc1679Display::IsRefreshing()) {
        ESP_LOGW(kTag, "Shared RST: waiting for UC1679 refresh to finish");
        const TickType_t start = xTaskGetTickCount();
        while (Uc1679Display::IsRefreshing()) {
            if ((xTaskGetTickCount() - start) * portTICK_PERIOD_MS >= 25000) {
                ESP_LOGE(kTag,
                         "UC1679 refresh wait timeout; resetting anyway "
                         "(color panel may show artifacts)");
                break;
            }
            vTaskDelay(pdMS_TO_TICKS(50));
        }
    }
#endif
    // The 4.2-inch SSD1619 reference sequence uses three reset pulses.
    for (int i = 0; i < 3; ++i) {
        gpio_set_level(EPD_RST_PIN, 1);
        vTaskDelay(pdMS_TO_TICKS(20));
        gpio_set_level(EPD_RST_PIN, 0);
        vTaskDelay(pdMS_TO_TICKS(2));
    }
    gpio_set_level(EPD_RST_PIN, 1);
    vTaskDelay(pdMS_TO_TICKS(20));
}

bool Ssd1619Display::WaitBusy(uint32_t timeout_ms) {
    const TickType_t start = xTaskGetTickCount();
    const int idle_level = busy_idle_level_ >= 0
                               ? busy_idle_level_
                               : (EPD_BUSY_LEVEL ? 0 : 1);
    while (gpio_get_level(EPD_BUSY_PIN) != idle_level) {
        if ((xTaskGetTickCount() - start) * portTICK_PERIOD_MS >= timeout_ms) {
            ESP_LOGE(kTag, "BUSY timeout after %lu ms (level=%d)",
                     static_cast<unsigned long>(timeout_ms),
                     gpio_get_level(EPD_BUSY_PIN));
            return false;
        }
        vTaskDelay(pdMS_TO_TICKS(10));
    }
    return true;
}

bool Ssd1619Display::WaitRefreshComplete(int idle_level, uint32_t timeout_ms) {
    // Some SSD1619 carrier boards expose BUSY directly, while others invert it.
    // Use the level immediately before 0x20 as idle and require a complete
    // idle -> busy -> idle transition instead of assuming a fixed polarity.
    const TickType_t assert_start = xTaskGetTickCount();
    while (gpio_get_level(EPD_BUSY_PIN) == idle_level) {
        if ((xTaskGetTickCount() - assert_start) * portTICK_PERIOD_MS >= 500) {
            ESP_LOGE(kTag,
                     "SSD1619 BUSY did not leave idle level %d after 0x20 "
                     "(CS=GPIO%d BUSY=GPIO%d). Stopping refresh retries.",
                     idle_level, EPD_CS_PIN, EPD_BUSY_PIN);
            return false;
        }
        vTaskDelay(pdMS_TO_TICKS(1));
    }

    const TickType_t release_start = xTaskGetTickCount();
    while (gpio_get_level(EPD_BUSY_PIN) != idle_level) {
        if ((xTaskGetTickCount() - release_start) * portTICK_PERIOD_MS >=
            timeout_ms) {
            ESP_LOGE(kTag,
                     "SSD1619 BUSY timeout: idle=%d current=%d after %lu ms",
                     idle_level, gpio_get_level(EPD_BUSY_PIN),
                     static_cast<unsigned long>(timeout_ms));
            return false;
        }
        vTaskDelay(pdMS_TO_TICKS(10));
    }
    return true;
}

void Ssd1619Display::Command(uint8_t command, const uint8_t* data, size_t length) {
    gpio_set_level(EPD_DC_PIN, 0);
    gpio_set_level(EPD_CS_PIN, 0);
    spi_transaction_t transaction = {};
    transaction.length = 8;
    transaction.tx_buffer = &command;
    ESP_ERROR_CHECK(spi_device_polling_transmit(spi_, &transaction));
    gpio_set_level(EPD_CS_PIN, 1);
    if (data && length) Data(data, length);
}

void Ssd1619Display::Data(const uint8_t* data, size_t length) {
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
            panel_faulted_ = true;
            return;
        }
        data += chunk;
        length -= chunk;
    }
    gpio_set_level(EPD_CS_PIN, 1);
}

void Ssd1619Display::SetRamWindow(int x_start, int x_end,
                                  int y_start, int y_end) {
    const uint8_t x_window[] = {static_cast<uint8_t>(x_start),
                                static_cast<uint8_t>(x_end)};
    const uint8_t y_window[] = {static_cast<uint8_t>(y_start & 0xff),
                                static_cast<uint8_t>(y_start >> 8),
                                static_cast<uint8_t>(y_end & 0xff),
                                static_cast<uint8_t>(y_end >> 8)};
    Command(0x44, x_window, sizeof(x_window));
    Command(0x45, y_window, sizeof(y_window));
    const uint8_t x = static_cast<uint8_t>(x_start);
    const uint8_t y[] = {static_cast<uint8_t>(y_start & 0xff),
                         static_cast<uint8_t>(y_start >> 8)};
    Command(0x4e, &x, 1);
    Command(0x4f, y, 2);
}

void Ssd1619Display::InitializePanel() {
    HardwareReset();
    // Sample the actual module's idle level. Do not rely on BUSY polarity
    // printed in a controller datasheet because FPC/adaptor boards may invert it.
    busy_idle_level_ = gpio_get_level(EPD_BUSY_PIN);
    ESP_LOGI(kTag, "BUSY idle level detected: %d", busy_idle_level_);
    Command(0x12);  // SWRESET
    vTaskDelay(pdMS_TO_TICKS(20));
    WaitBusy();

    // Do not depend on OTP/power-on defaults. A controller can acknowledge
    // SPI and toggle BUSY while its analogue gate/source supplies are still
    // disabled, which looks exactly like a successful refresh with no image.
    // 300 gate lines: 299 = 0x012B, GD=0, SM=0, TB=0.
    const uint8_t driver_output[] = {0x2b, 0x01, 0x00};
    Command(0x01, driver_output, sizeof(driver_output));

    // SSD1619 4.2-inch reference booster soft-start values.
    const uint8_t booster[] = {0xd7, 0xd6, 0x9d};
    Command(0x0c, booster, sizeof(booster));

    // Use the controller's internal temperature sensor for OTP waveform
    // compensation. Without this selection some modules never start a LUT.
    const uint8_t temperature = 0x80;
    Command(0x18, &temperature, 1);

    const uint8_t entry_mode = 0x03;  // X+, Y+
    Command(0x11, &entry_mode, 1);
    const uint8_t border = 0x05;
    Command(0x3c, &border, 1);
    const uint8_t display_update[] = {0x40, 0x00};
    Command(0x21, display_update, sizeof(display_update));
    SetRamWindow();

    // Explicitly enable clock and analogue power before writing display RAM.
    // 0xC0 = enable clock + analogue. BUSY must return to the sampled idle
    // level before RAM traffic begins.
    const uint8_t power_on = 0xc0;
    Command(0x22, &power_on, 1);
    Command(0x20);
    vTaskDelay(pdMS_TO_TICKS(20));
    WaitBusy();
    panel_initialized_ = true;
    ESP_LOGI(kTag, "SSD1619 analogue power/init sequence complete");
}

void Ssd1619Display::Refresh(const uint8_t* data, bool partial) {
    if (panel_faulted_) return;
    if (!panel_initialized_) InitializePanel();

    if (partial) {
        int x_start = static_cast<int>(kStride);
        int x_end = -1;
        int y_start = DISPLAY_HEIGHT;
        int y_end = -1;
        for (int y = 0; y < DISPLAY_HEIGHT; ++y) {
            for (int x = 0; x < static_cast<int>(kStride); ++x) {
                const size_t offset = static_cast<size_t>(y) * kStride + x;
                if (panel_frame_[offset] != data[offset]) {
                    x_start = std::min(x_start, x);
                    x_end = std::max(x_end, x);
                    y_start = std::min(y_start, y);
                    y_end = std::max(y_end, y);
                }
            }
        }
        if (x_end < x_start || y_end < y_start) return;

        HardwareReset();
        vTaskDelay(pdMS_TO_TICKS(10));
        if (!WaitBusy()) {
            panel_faulted_ = true;
            return;
        }
        const uint8_t partial_border = 0x80;
        Command(0x3c, &partial_border, 1);
        const uint8_t partial_ram_option[] = {0x00, 0x00};
        Command(0x21, partial_ram_option, sizeof(partial_ram_option));
        // 上传 2 相位 partial LUT（比原版 1 相位驱动强，残影少且不闪）。
        Command(0x32, kPartialLut, sizeof(kPartialLut));
        SetRamWindow(x_start, x_end, y_start, y_end);
        Command(0x24);
        const size_t row_bytes = static_cast<size_t>(x_end - x_start + 1);
        for (int y = y_start; y <= y_end; ++y) {
            Data(data + static_cast<size_t>(y) * kStride + x_start, row_bytes);
        }
        const uint8_t update = 0xc7;
        Command(0x22, &update, 1);
        Command(0x20);
        if (WaitRefreshComplete(busy_idle_level_)) {
            ++partial_refresh_count_;
            // RED RAM is used as the previous-image reference by the
            // differential LUT. Synchronize only the updated window so the
            // next sentence compares against what is now physically visible.
            SetRamWindow(x_start, x_end, y_start, y_end);
            Command(0x26);
            for (int y = y_start; y <= y_end; ++y) {
                Data(data + static_cast<size_t>(y) * kStride + x_start,
                     row_bytes);
            }
            std::memcpy(panel_frame_, data, kFrameBytes);
            ESP_LOGI(kTag, "Partial refresh complete (%u), area=(%d,%d)-(%d,%d)",
                     static_cast<unsigned>(partial_refresh_count_),
                     x_start * 8, y_start, (x_end + 1) * 8 - 1, y_end);
        } else {
            panel_faulted_ = true;
        }
        return;
    }

    // The reference partial sequence includes a hardware reset and programs
    // only the registers needed by the OTP partial waveform.  Re-run the
    // complete analogue/controller setup before returning to a full waveform.
    if (partial_refresh_count_ != 0) {
        panel_initialized_ = false;
        InitializePanel();
        if (panel_faulted_) return;
    }

    // Restore the full-refresh settings after a partial update.
    const uint8_t full_border = 0x05;
    Command(0x3c, &full_border, 1);
    const uint8_t full_ram_option[] = {0x40, 0x00};
    Command(0x21, full_ram_option, sizeof(full_ram_option));
    SetRamWindow();
    Command(0x24);
    Data(data, kFrameBytes);
    // The 4.2-inch SSD1619 reference sequence writes both RAM planes for a
    // monochrome full refresh.
    SetRamWindow();
    Command(0x26);
    Data(data, kFrameBytes);
    const uint8_t update = 0xf7;
    Command(0x22, &update, 1);
    Command(0x20);
    if (WaitRefreshComplete(busy_idle_level_)) {
        base_frame_valid_ = true;
        partial_refresh_count_ = 0;
        std::memcpy(panel_frame_, data, kFrameBytes);
        ESP_LOGI(kTag, "Full refresh complete");
    } else {
        panel_faulted_ = true;
    }
}

void Ssd1619Display::RawFrameRefresh(const uint8_t* frame, bool partial) {
    Refresh(frame, partial);
}

void Ssd1619Display::FlushCallback(lv_display_t* display, const lv_area_t* area,
                                   uint8_t* pixels) {
    auto* self = static_cast<Ssd1619Display*>(lv_display_get_user_data(display));
    if (!self || !self->refresh_task_) {
        lv_display_flush_ready(display);
        return;
    }
    const auto* source = reinterpret_cast<const uint16_t*>(pixels);
    const int source_width = area->x2 - area->x1 + 1;
    xSemaphoreTake(self->mutex_, portMAX_DELAY);
    for (int y = std::max(0, static_cast<int>(area->y1));
         y <= std::min(DISPLAY_HEIGHT - 1, static_cast<int>(area->y2)); ++y) {
        for (int x = std::max(0, static_cast<int>(area->x1));
             x <= std::min(DISPLAY_WIDTH - 1, static_cast<int>(area->x2)); ++x) {
            const uint16_t color = source[(y - area->y1) * source_width + x - area->x1];
            const int r = ((color >> 11) & 0x1f) * 255 / 31;
            const int g = ((color >> 5) & 0x3f) * 255 / 63;
            const int b = (color & 0x1f) * 255 / 31;
            const bool white = (77 * r + 150 * g + 29 * b) >= (128 * 256);
            const size_t index = static_cast<size_t>(y) * kStride + x / 8;
            const uint8_t mask = 0x80 >> (x & 7);
            if (white) self->frame_[index] |= mask;
            else self->frame_[index] &= static_cast<uint8_t>(~mask);
        }
    }
    xSemaphoreGive(self->mutex_);
    lv_display_flush_ready(display);
}

void Ssd1619Display::RequestRefresh(bool partial) {
    if (!refresh_task_) return;
    xSemaphoreTake(mutex_, portMAX_DELAY);
    // If several LVGL events are coalesced, any requested full refresh wins.
    refresh_partial_pending_ = refresh_pending_
                                   ? (refresh_partial_pending_ && partial)
                                   : partial;
    refresh_pending_ = true;
    xSemaphoreGive(mutex_);
    xTaskNotifyGive(refresh_task_);
}

void Ssd1619Display::RefreshTaskEntry(void* arg) {
    static_cast<Ssd1619Display*>(arg)->RefreshTask();
}

void Ssd1619Display::RefreshTask() {
    while (true) {
        ulTaskNotifyTake(pdTRUE, portMAX_DELAY);
        // LVGL flushes a partial buffer in several strips. Wait until all
        // strips have landed, then perform one physical e-paper refresh.
        // 300ms was tuned for conversation / book pages with heavy full-screen
        // updates.  The speaking page only changes a short transcript label,
        // so we can safely commit faster and make the conversation feel snappier.
        vTaskDelay(pdMS_TO_TICKS(150));
        bool should_refresh = false;
        bool use_partial = false;
        xSemaphoreTake(mutex_, portMAX_DELAY);
        if (refresh_pending_) {
            refresh_pending_ = false;
            use_partial = refresh_partial_pending_;
            refresh_partial_pending_ = false;
            if (std::memcmp(tx_frame_, frame_, kFrameBytes) != 0) {
                std::memcpy(tx_frame_, frame_, kFrameBytes);
                should_refresh = true;
            }
        }
        xSemaphoreGive(mutex_);
        // Do not hold the framebuffer mutex during the several-second panel
        // waveform; LVGL may continue composing the next frame meanwhile.
        if (should_refresh) {
            size_t black_bits = 0;
            for (size_t i = 0; i < kFrameBytes; ++i) {
                black_bits += static_cast<size_t>(__builtin_popcount(
                    static_cast<unsigned>(~tx_frame_[i] & 0xff)));
            }
            ESP_LOGI(kTag, "LVGL frame ready: black_pixels=%u/%u",
                     static_cast<unsigned>(black_bits),
                     static_cast<unsigned>(DISPLAY_WIDTH * DISPLAY_HEIGHT));
            // Cross-page navigation performs the cleaning full refresh. Keep
            // all same-page conversation/menu/book updates partial so a long
            // response or reading session never flashes every N updates.
            use_partial = use_partial && base_frame_valid_;
            Refresh(tx_frame_, use_partial);
        }
    }
}
