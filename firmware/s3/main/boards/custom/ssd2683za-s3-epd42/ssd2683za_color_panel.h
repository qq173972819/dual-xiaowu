#pragma once

#include <atomic>
#include <functional>

#include <driver/spi_master.h>
#include <freertos/FreeRTOS.h>
#include <freertos/semphr.h>

#include "config.h"

// SSD2683ZA color panel on FPC2 (size from FPC2_WIDTH/HEIGHT, default
// 400x300). The SPI bus, DC and RST are shared with the SSD1619 on FPC1; this
// class owns only FPC2 CS/BUSY and never creates another SPI bus or LVGL display.
class Ssd2683zaColorPanel {
public:
    static constexpr int kWidth = FPC2_WIDTH;
    static constexpr int kHeight = FPC2_HEIGHT;
    static constexpr size_t kFrameBytes = kWidth * kHeight / 4;

    explicit Ssd2683zaColorPanel(std::function<bool()> other_panel_busy);
    ~Ssd2683zaColorPanel();

    bool Init();
    bool Available() const { return available_; }
    bool ShowFrame(const uint8_t* packed_frame);
    static bool IsRefreshing() { return refreshing_.load(); }

private:
    static constexpr size_t kDmaChunkBytes = 4096;
    // SSD2683ZA reference driver (firmware/src/epd_driver.cpp) waits while
    // BUSY is LOW; the idle level is HIGH.
    static constexpr int kBusyActiveLevel = 0;

    spi_device_handle_t spi_ = nullptr;
    SemaphoreHandle_t mutex_ = nullptr;
    uint8_t* dma_buffer_ = nullptr;
    std::function<bool()> other_panel_busy_;
    bool available_ = false;
    bool panel_awake_ = false;

    static std::atomic<bool> refreshing_;
    void WaitForOtherPanelIdle();
    void HardwareReset();
    bool WaitBusy(uint32_t timeout_ms);
    bool InitializePanel();
    void SendCommandRaw(uint8_t command);
    void SendDataRaw(const uint8_t* data, size_t length);
    void SendCommandOnly(uint8_t command);
    void SendCommandWithByte(uint8_t command, uint8_t value);
    void SendCommandWithBuffer(uint8_t command, const uint8_t* data,
                               size_t length);
    void SendDataStream(const uint8_t* data, size_t length);
    void SleepPanel();
};
