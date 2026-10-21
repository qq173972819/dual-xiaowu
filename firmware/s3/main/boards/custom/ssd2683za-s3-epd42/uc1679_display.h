#pragma once

#include <atomic>
#include <functional>

#include <driver/spi_master.h>
#include <freertos/FreeRTOS.h>
#include <freertos/semphr.h>
#include <freertos/task.h>

#include "config.h"

// SSD1683/UC8179 命令集兼容的 4.2" 400x300 黑/白/红电子纸驱动（FPC2）。
//
// 硬件接线（docs/eda/Netlist_schematic_main_1_2026-09-15.net）：
//   与 SSD1619 快刷屏共享 SPI(DIN=GPIO6/CLK=GPIO8)、DC=GPIO10、RST=GPIO11；
//   本屏独立 CS=GPIO9、BUSY=GPIO12。两路座子各有独立 GDR/RESE 升压泵，
//   由面板驱动芯片自管理，MCU 不需要电源使能脚。
//
// 使用模型：
//   三色屏没有可用的局部快刷波形，每次 ShowFrame() 都执行
//   "复位 -> 初始化 -> 写 BW/RED 平面 -> 全刷 -> 等 BUSY -> 深睡"。
//   深睡(0x07, retain RAM)后刷新不影响面板已显示画面，功耗为零。
//   每次刷新前自行复位，因此不受 SSD1619 侧共享 RST 硬复位的影响；
//   反向仲裁由 g_uc1679_refreshing + Ssd1619Display 侧等待完成。
//
// 帧 格式：
//   bw_plane/red_plane 各 15000 字节（400/8 × 300），行优先 MSB 左。
//   bw_plane:  1=白, 0=黑
//   red_plane: 1=红, 0=非红
//
// 可调参数（若实板点亮异常，按模组 UC1679 demo/datasheet 核对）：
//   kPanelSetting / kPowerSetting / kBooster / kBorderWaveform。
class Uc1679Display {
public:
    static constexpr int kWidth = DISPLAY_WIDTH;   // 400
    static constexpr int kHeight = DISPLAY_HEIGHT; // 300
    static constexpr size_t kStride = (kWidth + 7) / 8;        // 50
    static constexpr size_t kPlaneBytes = kStride * kHeight;   // 15000

    // color_screen_ready: SPI 总线必须已经由 Ssd1619Display::InitializeSpi
    // 初始化（spi_bus_initialize 只能调用一次）。
    // other_panel_busy: 查询 SSD1619 快刷屏是否在波形刷新中（共享 RST 仲裁）。
    explicit Uc1679Display(
        std::function<bool()> other_panel_busy = nullptr);
    ~Uc1679Display();

    // 探测面板是否在座（BUSY 线是否可采样 + 初始化命令是否成功）。
    // 双排座只插一个屏时返回 false，后续 ShowFrame 直接跳过。
    bool Init();

    bool Available() const { return available_; }

    // 刷新一帧（全刷，阻塞 ~16s，期间置位 g_uc1679_refreshing）。
    // bw_plane/red_plane 均为 kPlaneBytes；任一为 nullptr 时用全白/全无红。
    bool ShowFrame(const uint8_t* bw_plane, const uint8_t* red_plane);

    // 进入深睡（保留 RAM，显示保持）。
    void Sleep();

    // 面板是否正在刷新（BUSY 活跃）——供 SSD1619 侧复位仲裁。
    static bool IsRefreshing() { return g_uc1679_refreshing.load(); }

private:
    static constexpr size_t kDmaChunkBytes = 4096;

    // UC1679 命令（SSD16xx 风格命令集）
    static constexpr uint8_t kCmdPanelSetting   = 0x00;
    static constexpr uint8_t kCmdPowerSetting   = 0x01;
    static constexpr uint8_t kCmdPowerOff       = 0x02;
    static constexpr uint8_t kCmdPowerOn        = 0x04;
    static constexpr uint8_t kCmdBooster        = 0x06;
    static constexpr uint8_t kCmdDeepSleep      = 0x07;
    static constexpr uint8_t kCmdDataStartBw    = 0x10;  // B/W RAM 数据传输
    static constexpr uint8_t kCmdDataStartRed   = 0x13;  // RED RAM 数据传输
    static constexpr uint8_t kCmdMasterActivate = 0x12;  // 启动刷新
    static constexpr uint8_t kCmdPll            = 0x30;
    static constexpr uint8_t kCmdBorderWave     = 0x3C;
    static constexpr uint8_t kCmdVcomDc         = 0x82;
    static constexpr uint8_t kCmdResolution     = 0x61;

    spi_device_handle_t spi_ = nullptr;
    uint8_t* dma_buffer_ = nullptr;
    SemaphoreHandle_t mutex_ = nullptr;
    std::function<bool()> other_panel_busy_;
    bool available_ = false;
    bool panel_awake_ = false;
    int busy_idle_level_ = -1;

    void InitializeGpio();
    void InitializeSpiDevice();
    void HardwareReset();
    bool WaitBusy(uint32_t timeout_ms);
    bool WaitBusyCycle(uint32_t assert_timeout_ms, uint32_t idle_timeout_ms);
    // 复位前等待 SSD1619 快刷屏完成当前波形（共享 RST 仲裁）。
    void WaitForOtherPanelIdle();
    void Command(uint8_t command, const uint8_t* data = nullptr,
                 size_t length = 0);
    void Data(const uint8_t* data, size_t length);
    bool WakeAndConfigure();

    // 全局刷新标志：SSD1619 侧做硬复位前检查并等待，避免共享 RST
    // 打断三色屏正在进行的 16s 波形。
    static std::atomic<bool> g_uc1679_refreshing;
};
