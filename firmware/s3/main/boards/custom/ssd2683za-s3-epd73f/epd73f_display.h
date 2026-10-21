#pragma once

#include <atomic>
#include <cstdint>

#include <driver/spi_master.h>
#include <freertos/FreeRTOS.h>
#include <freertos/semphr.h>
#include <freertos/task.h>

#include "config.h"
#include "epd_7in3f_panel.h"
#include "lcd_display.h"

// 7.3 寸七彩屏主显示类（板型 ssd2683za-s3-epd73f）。
//
// 组成：
//   - Epd7in3fPanel：底层面板驱动（复位/初始化/全刷/深睡）。
//   - LcdDisplay：LVGL 基类。默认关闭动画聊天 UI（EPD73F_ENABLE_CHAT_UI=0，
//     与 SSD1619 板一致的事件式无 UI 模式），此时屏幕内容只来自
//     InkSight 看板帧 / 清屏 / 色条测试。把宏改成 1 可启用标准聊天界面，
//     RGB565 会被量化到 7 色并按 EPD73F_UI_REFRESH_MIN_INTERVAL_MS 节流。
//
// 刷新模型：
//   七彩屏没有局部快刷，任何内容变化都是 35s 全刷。所有物理刷新都排在
//   后台任务里串行执行；强制帧（InkSight/清屏/色条）立即刷，LVGL 界面帧
//   按 UI_REFRESH_MIN_INTERVAL_MS 节流 + 内容变化检测。
class Epd73fDisplay : public LcdDisplay {
public:
    Epd73fDisplay();
    ~Epd73fDisplay() override;

    // ---- Display 接口（聊天 UI 关闭时全部为 no-op，避免触碰空 LVGL 对象；
    //      开启时走 LcdDisplay 标准实现，量化到 7 色后台慢刷）----
    void SetStatus(const char* status) override;
    void SetEmotion(const char* emotion) override;
    void SetChatMessage(const char* role, const char* content) override;
    void ShowActivationUi(const char* message, const char* code) override;
    void HideActivationUi() override;
    void ClearChatMessages() override;
    void SetupUI() override;
    void SetTheme(Theme* theme) override;
    void ShowNotification(const char* notification,
                          int duration_ms = 3000) override;

    // ---- 七彩屏专属 ----
    // 异步显示 InkSight 2bpp 平面（各 kPlaneBytes=48000，行优先 MSB 左；
    // bw: 1=白 0=黑；yellow/red: 1=该色 0=无。yellow/red 可为 nullptr）。
    // 平面会先拷贝到 PSRAM 暂存区，物理刷新在后台任务执行（约 35s）。
    bool ShowInkSightPlanes(const uint8_t* bw_plane,
                            const uint8_t* yellow_plane,
                            const uint8_t* red_plane);
    // 异步显示 InkSight **规范索引 4bpp 帧**（kFrameBytes=192000 字节，
    // 每字节 2 像素、高半字节在左）。规范索引：0=黑 1=白 2=黄 3=红 4=蓝 5=绿。
    // 比三平面路径多出蓝/绿两个通道（六色面板的完整能力），帧直接交给
    // Epd7in3fPanel::ShowNormalizedFrame，由其按面板型号换算色索引。
    // 帧会先拷到 PSRAM 暂存区（首次调用时分配），物理刷新在后台任务执行。
    bool ShowInkSightFrame(const uint8_t* norm_frame);
    // 异步整屏填充单色（清屏）。
    bool ClearScreen(uint8_t color_index = Epd7in3fPanel::kColorWhite);
    // 异步显示 7 色色条（点亮验证）。
    bool ShowColorBars();

    bool IsPanelBusy() const { return Epd7in3fPanel::IsRefreshing(); }
    // 等待已排队的刷新真正完成。间歇模式必须等墨水屏波形结束后才能
    // 进入 ESP 深度睡眠，否则画面会停在刷新中途。
    bool WaitForRefreshComplete(uint32_t timeout_ms = 90000) const;
    Epd7in3fPanel& panel() { return panel_; }

private:
    enum class PendingMode : uint8_t {
        kNone,
        kInkSight,       // 三平面（bw/yellow/red）2bpp 路径
        kInkSightFrame,  // 规范索引 4bpp 帧（六色完整能力）
        kClear,
        kColorBars,
        kUi
    };

    static void FlushCallback(lv_display_t* display, const lv_area_t* area,
                              uint8_t* pixels);
    static void RefreshTaskEntry(void* arg);
    void RefreshTask();
    void RequestUiRefresh();
    // RGB565 -> 7 色索引（最近邻，调色板见 .cc）。
    static uint8_t Quantize(uint8_t r5, uint8_t g6, uint8_t b5);

    Epd7in3fPanel panel_;
    // 192000 字节打包 7 色帧（PSRAM）。color_frame_ 是"当前应有画面"，
    // tx_frame_ 是"面板上实际显示的画面"，两者 diff 决定是否需要物理刷。
    uint8_t* color_frame_ = nullptr;
    uint8_t* tx_frame_ = nullptr;
    // InkSight 平面暂存（3 x 48000，PSRAM）。
    uint8_t* staging_bw_ = nullptr;
    uint8_t* staging_yellow_ = nullptr;
    uint8_t* staging_red_ = nullptr;
    bool has_yellow_ = false;
    bool has_red_ = false;
    // 4bpp 帧暂存（kFrameBytes=192000，PSRAM）。仅在走 4bpp 通道时懒分配，
    // 避免只用三平面路径的固件白白占掉 192KB PSRAM。
    uint8_t* staging_frame_ = nullptr;

    SemaphoreHandle_t mutex_ = nullptr;
    TaskHandle_t refresh_task_ = nullptr;
    std::atomic<PendingMode> pending_mode_{PendingMode::kNone};
    std::atomic<bool> pending_force_{false};
    std::atomic<bool> ui_dirty_{false};
    std::atomic<bool> refresh_in_progress_{false};
    int64_t last_ui_refresh_ms_ = 0;

    static constexpr size_t kLvglBufferBytes =
        DISPLAY_WIDTH * 24 * sizeof(uint16_t);
};
