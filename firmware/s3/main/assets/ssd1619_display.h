#pragma once

#include <driver/spi_master.h>
#include <freertos/FreeRTOS.h>
#include <freertos/semphr.h>
#include <freertos/task.h>

#include "config.h"
#include "epaper_dashboard.h"
#include "lcd_display.h"

class Ssd1619Display : public LcdDisplay {
public:
    Ssd1619Display();
    ~Ssd1619Display() override;

    void SetStatus(const char* status) override;
    void SetEmotion(const char* emotion) override;
    void SetChatMessage(const char* role, const char* content) override;
    void ClearChatMessages() override;
    void SetupUI() override;
    void SetTheme(Theme* theme) override;
    void ShowNotification(const char* notification,
                          int duration_ms = 3000) override;
    EpaperDashboard& dashboard() { return dashboard_; }

    // 直接用 caller 提供的帧缓冲刷新画面。
    // frame: 50*300=15000 字节，1-bit/行/MSB 左白 1/黑 0。
    // partial=true: 差分快刷；false: 全刷清残影。
    void RawFrameRefresh(const uint8_t* frame, bool partial);

    // 面板是否正在波形刷新中（BUSY 活跃）。双屏共享 RST 时供 UC1679
    // 侧复位仲裁；面板未初始化前恒为 false。
    bool IsPanelBusy() const {
        if (busy_idle_level_ < 0 || panel_faulted_) return false;
        return gpio_get_level(EPD_BUSY_PIN) != busy_idle_level_;
    }

private:
    static constexpr size_t kStride = (DISPLAY_WIDTH + 7) / 8;
    static constexpr size_t kFrameBytes = kStride * DISPLAY_HEIGHT;

    spi_device_handle_t spi_ = nullptr;
    uint8_t* frame_ = nullptr;
    uint8_t* tx_frame_ = nullptr;
    uint8_t* panel_frame_ = nullptr;
    uint8_t* dma_buffer_ = nullptr;
    SemaphoreHandle_t mutex_ = nullptr;
    TaskHandle_t refresh_task_ = nullptr;
    bool refresh_pending_ = false;
    bool refresh_partial_pending_ = false;
    bool panel_initialized_ = false;
    bool panel_faulted_ = false;
    bool base_frame_valid_ = false;
    uint16_t partial_refresh_count_ = 0;
    bool assistant_response_active_ = false;
    int busy_idle_level_ = -1;
    EpaperDashboard dashboard_;

    static void FlushCallback(lv_display_t* display, const lv_area_t* area,
                              uint8_t* pixels);
    static void RefreshTaskEntry(void* arg);
    void RefreshTask();
    void RequestRefresh(bool partial);
    void InitializeSpi();
    void HardwareReset();
    bool WaitBusy(uint32_t timeout_ms = 10000);
    bool WaitRefreshComplete(int idle_level, uint32_t timeout_ms = 20000);
    void Command(uint8_t command, const uint8_t* data = nullptr, size_t length = 0);
    void Data(const uint8_t* data, size_t length);
    void InitializePanel();
    void SetRamWindow(int x_start = 0, int x_end = kStride - 1,
                      int y_start = 0, int y_end = DISPLAY_HEIGHT - 1);
    void Refresh(const uint8_t* data, bool partial);
};
