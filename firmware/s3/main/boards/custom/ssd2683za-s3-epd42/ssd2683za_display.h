#ifndef SSD2683ZA_DISPLAY_H
#define SSD2683ZA_DISPLAY_H

#include <driver/spi_master.h>
#include <freertos/FreeRTOS.h>
#include <freertos/semphr.h>
#include <freertos/task.h>
#include <soc/gpio_reg.h>

#include "config.h"
#include "epaper_dashboard.h"
#include "lcd_display.h"

class Ssd2683zaDisplay : public LcdDisplay {
public:
    Ssd2683zaDisplay();
    ~Ssd2683zaDisplay() override;
    void SetStatus(const char* status) override;
    void SetEmotion(const char* emotion) override;
    EpaperDashboard& dashboard() { return dashboard_; }

private:
    static constexpr size_t kFrameBytes = DISPLAY_WIDTH * DISPLAY_HEIGHT / 4;
    spi_device_handle_t spi_ = nullptr;
    uint8_t* frame_ = nullptr;
    uint8_t* tx_frame_ = nullptr;
    SemaphoreHandle_t mutex_ = nullptr;
    TaskHandle_t refresh_task_ = nullptr;
    TaskHandle_t busy_sniffer_task_ = nullptr;
    bool refresh_pending_ = false;
    int refresh_count_ = 0;  // 累计刷新次数，每 N 次做一次强力清屏
    EpaperDashboard dashboard_;
    bool voice_overlay_active_ = false;

    static void FlushCallback(lv_display_t* display, const lv_area_t* area, uint8_t* pixels);
    static void RefreshTaskEntry(void* arg);
    void RefreshTask();
    static void BusySnifferTaskEntry(void* arg);
    void BusySnifferTask();
    void InitializeSpi();
    void InitializePanel();
    void DumpGpioSnapshot(const char* label);
    void HardwareReset();
    bool WaitBusy(uint32_t timeout_ms = 45000);
    // Low-level SPI primitives — DO NOT touch CS pin. Caller is responsible
    // for wrapping one logical command+data sequence in CsLow()/CsHigh().
    void CsLow()  { WRITE_PERI_REG(GPIO_OUT_W1TC_REG, 1U << EPD_CS_PIN); }
    void CsHigh() { WRITE_PERI_REG(GPIO_OUT_W1TS_REG, 1U << EPD_CS_PIN); }
    void SendCommandRaw(uint8_t command);
    void SendDataRaw(uint8_t data);
    void SendBufferRaw(const uint8_t* data, size_t length);
    // High-level transaction wrappers — frame a complete SPI transfer with
    // correct CS semantics. These are the only public SPI entry points.
    void SendCommandOnly(uint8_t cmd);
    void SendCommandWithByte(uint8_t cmd, uint8_t data_byte);
    void SendCommandWithBuffer(uint8_t cmd, const uint8_t* data, size_t nbytes);
    void SendDataStream(const uint8_t* data, size_t length);
    void Refresh(const uint8_t* data);
    static uint8_t Rgb565To2bpp(uint16_t color);
};

#endif
