#pragma once

#include <atomic>
#include <cstddef>
#include <cstdint>
#include <string>

#include <driver/spi_master.h>
#include <freertos/FreeRTOS.h>
#include <freertos/semphr.h>

#include "config.h"

// 7.3 寸七彩电子纸驱动（Waveshare 7.3" e-Paper (F)，800x480，7 色）。
//
// 时序来源：E-Paper_code/Arduino/epd7in3f 官方 demo 逐条移植
// （CMDH=0xAA 寄存器解锁 + 0x00/0x01/0x03/0x05/0x06/0x08/0x13/0x30/0x41/
//   0x50/0x60/0x61/0x82/0x84/0x86/0xE3/0xE0/0xE6 初始化序列）。
//
// 硬件接线（3屏驱动_main_2_2026-09-23.net，FPC3 8pin 座 -> 8P 转 50P 转接板）：
//   与 FPC2 共享 DIN=GPIO6 / CLK=GPIO8 / DC=GPIO10 / RST=GPIO11；
//   CS=GPIO9（CS1，FPC3-4 与 FPC2-12 并联）、BUSY=GPIO12（BUSY1，FPC3-1
//   与 FPC2-9 并联）。插上七彩屏时 FPC2 座必须空置。
//
// 帧格式（EPD_7IN3F_Display）：
//   0x10 后连续发送 800*480/2 = 192000 字节；每字节打包 2 个像素，
//   高半字节 = 左（偶数列）像素。3-bit 色索引：
//     0=黑 1=白 2=绿 3=蓝 4=红 5=黄 6=橙（7=不可用，残留）。
//
// 使用模型：
//   无局部快刷波形，每次 ShowFrame() 都执行
//   "复位 -> 初始化 -> 写全帧 -> 上电 -> 0x12 全刷(约35s) -> 下电 -> 深睡"。
//   深睡前把 RST 拉低（官方 Sleep()），功耗趋近 0 且显示保持；
//   下一次 ShowFrame() 先释放 RST 重新复位初始化。
//
// 可调参数（若实板点亮异常，对照模组 demo 核对）：
//   InitializeRegisters() 中的寄存器序列、kSpiClockHz。
class Epd7in3fPanel {
public:
    static constexpr int kWidth = 800;
    static constexpr int kHeight = 480;
    static constexpr size_t kStride = kWidth / 2;                  // 400
    static constexpr size_t kFrameBytes = kStride * kHeight;       // 192000

// 3-bit 色索引（每像素 3 位，打包时取低 3 位）
// —— ACeP 七彩 (F)：0=黑 1=白 2=绿 3=蓝 4=红 5=黄 6=橙
    static constexpr uint8_t kColorBlack  = 0x0;
    static constexpr uint8_t kColorWhite  = 0x1;
    static constexpr uint8_t kColorGreen  = 0x2;
    static constexpr uint8_t kColorBlue   = 0x3;
    static constexpr uint8_t kColorRed    = 0x4;
    static constexpr uint8_t kColorYellow = 0x5;
    static constexpr uint8_t kColorOrange = 0x6;

    // —— Spectra 6 六色 (E)：0=黑 1=白 2=黄 3=红 4=蓝 5=绿
    static constexpr uint8_t kS6Black  = 0x0;
    static constexpr uint8_t kS6White  = 0x1;
    static constexpr uint8_t kS6Yellow = 0x2;
    static constexpr uint8_t kS6Red    = 0x3;
    static constexpr uint8_t kS6Blue   = 0x4;
    static constexpr uint8_t kS6Green  = 0x5;

    // 面板型号。7.3" 有两种：
    //   kSpectra6 = 7.3" e-Paper (E)，E Ink Spectra 6，六色，全刷约 12s
    //   kAcep7    = 7.3" e-Paper (F)，ACeP，七彩，全刷约 35s
    // 两者分辨率与帧格式完全相同（800x480、4bpp、192000 字节），
    // 差别只在初始化寄存器序列与色索引映射 —— 用错型号会完全点不亮。
    enum class Variant { kSpectra6, kAcep7 };

    // 运行时切换面板型号（配合 MCP 工具，免重编译即可试两种屏）。
    void SetVariant(Variant variant) { variant_ = variant; }
    Variant variant() const { return variant_; }
    // "6color"/"6"/"e6" -> kSpectra6；"7color"/"7"/"f" -> kAcep7；其它返回 false。
    static bool ParseVariant(const std::string& text, Variant* out);
    static const char* VariantName(Variant variant);

    // 自动型号探测：NVS 里没有 variant（首次开机/换屏）时开启。
    // 开启后第一次刷新若 BUSY 全程无跳变（说明初始化序列与屏不匹配，
    // 屏卡在忙状态），自动换另一种序列再刷一次，并把命中的型号通过
    // TakeProbeResult() 交给板级写入 NVS，之后不再探测。
    void EnableAutoProbe() { auto_probe_ = true; }
    void DisableAutoProbe() { auto_probe_ = false; }
    bool TakeProbeResult(Variant* out);

    Epd7in3fPanel() = default;
    ~Epd7in3fPanel();

    // 初始化 GPIO/SPI 总线并探测面板（复位 + 等待 BUSY 空闲）。
    // 探测失败（BUSY 线无响应）返回 false，Available() 为 false，
    // 后续 ShowFrame 直接跳过。
    bool Init();

    bool Available() const { return available_; }

    // 复位后 BUSY 线是否出现过被面板驱动的低电平（true = 面板有响应）。
    bool Detected() const { return detected_; }
    // BUSY 线是否可信（false = 走固定延时时序）。
    bool BusyReliable() const { return busy_reliable_; }

    // 刷新一帧打包好的 7 色图像（kFrameBytes，2 像素/字节，高半字节在左）。
    // 阻塞约 35~40s（全刷波形 + 上电/下电）。同一时刻仅允许一次。
    bool ShowFrame(const uint8_t* packed_frame);

    // 刷新一帧**规范色索引**的 4bpp 图像（kFrameBytes，2 像素/字节，
    // 高半字节在左）。规范索引与 Spectra 6 面板原生编号一致：
    //   0=黑 1=白 2=黄 3=红 4=蓝 5=绿（6..15 视为白）
    // 内部按当前面板型号重映射（六色板直接透传，ACeP 七彩把黄/红/蓝/绿
    // 换算成自己的编号），因此后端只需输出一套索引。
    bool ShowNormalizedFrame(const uint8_t* norm_frame);

    // InkSight 2bpp 流映射：把 1bpp BW 平面（1=白 0=黑）与可选的黄色/
    // 红色平面转换为 7 色打包帧并刷新。平面尺寸必须为 kPlaneBytes =
    // (800/8)*480 = 48000 字节（行优先 MSB 左）。优先级：红 > 黄 > 黑白。
    static constexpr size_t kPlaneStride = kWidth / 8;             // 100
    static constexpr size_t kPlaneBytes = kPlaneStride * kHeight;  // 48000
    bool ShowPlanes(const uint8_t* bw_plane, const uint8_t* yellow_plane,
                    const uint8_t* red_plane);

    // 整屏填充单色并刷新（清屏/测试用）。
    bool Clear(uint8_t color_index = kColorWhite);

    // 显示 7 色色块测试条（每列 100px，共 8 列：黑/蓝/绿/橙/红/黄/白/白）。
    bool ShowColorBars();

    // 接线自检（排障用）：后台任务里依次把 RST/DC/CS 拉低、拉高各 hold_ms，
    // 日志实时打印当前电平与 BUSY 读数。用万用表在转接板侧量同名丝印脚，
    // 若电平不跟随变化 -> 排线断线；若变化出现在相邻脚 -> 脚序镜像（排线反插）。
    bool ProbePins(uint32_t hold_ms = 3000);

    // SPI 发送方式切换（排障用）。
    //   false = 硬件 SPI（默认，2MHz，整块 DMA 传输，CS 全程拉低）
    //   true  = 软件 bit-bang，1:1 复刻参考工程 epd.h 的 DEV_SPI_WriteByte：
    //           CS 拉低 -> 逐位 DIN=bit / SCK=1 / SCK=0 -> CS 拉高，每字节一个
    //           CS 脉冲、每位约 1us。用于排除「硬件 SPI 时序/CS 保持/速率」
    //           导致的命令进不去 —— 参考工程正是用这种方式点亮的。
    // 切换会释放/重建 SPI 总线，可在运行时调用（无需重启）。
    bool SetSpiMode(bool bitbang);
    bool bitbang() const { return bitbang_; }

    // 开机默认 SPI 方式（在 panel 构造/Init 之前由板级从 NVS 读入）。
    static void SetDefaultSpiMode(bool bitbang) { g_default_bitbang = bitbang; }
    static bool default_bitbang() { return g_default_bitbang; }

    // 盲刷自检：全程忽略 BUSY、按固定延时连刷 全黑 -> 全白 -> 色条 三帧。
    // 用于 BUSY 线完全无反馈时确认「屏到底有没有收到命令」——只要屏有电且
    // 序列对得上，这三次刷新一定会看到屏幕整体翻黑再翻白。
    bool BlindTest();

    // 进入深睡（0x07 0xA5 + RST 拉低，显示保持，功耗趋近 0）。
    void Sleep();

    // 面板是否正在刷新波形（供外部避免并发 SPI/复位操作）。
    static bool IsRefreshing() { return g_refreshing.load(); }

private:
    static constexpr size_t kDmaChunkBytes = 4096;
    // 官方标称全刷：ACeP 七彩 35s；BUSY 不可靠时的固定延时 38s。
    static constexpr uint32_t kRefreshTimeoutMs = 90 * 1000;
    static constexpr uint32_t kFixedRefreshDelayMs = 38 * 1000;
    // Spectra 6 六色标称 12s；BUSY 不可靠时给 18s 固定延时（含余量）。
    static constexpr uint32_t kFixedRefreshDelayMsS6 = 18 * 1000;

    spi_device_handle_t spi_ = nullptr;
    uint8_t* dma_buffer_ = nullptr;
    SemaphoreHandle_t mutex_ = nullptr;
    bool available_ = false;
    bool bus_owned_ = false;
    bool bitbang_ = false;

    // 是否信任 BUSY 线：一旦某次 WaitBusy 超时，就认为该排线/转接板的
    // BUSY 不可靠（浮空、反相或未连接），后续改用固定延时走时序，
    // 保证面板仍能正常刷新而不是直接判失败。
    bool busy_reliable_ = true;
    bool detected_ = false;
    // 默认按用户当前实板的型号（7.3" (E) Spectra 6 六色）；可由 NVS/MCP 切换。
    Variant variant_ = Variant::kSpectra6;

    // 自动型号探测状态。
    bool auto_probe_ = false;
    bool probe_hit_ = false;
    Variant probe_variant_ = Variant::kSpectra6;
    // 本次刷新期间 BUSY 是否出现过被面板驱动的信号（高电平或跳变）。
    bool busy_responded_ = false;

    // BUSY 线活动采样结果：用于区分「面板在驱动 BUSY」与「浮空恒为高」。
    struct BusyProbe {
        bool saw_low = false;
        bool saw_high = false;
        int transitions = 0;
        int last_level = 0;
    };

    void InitializeGpio();
    bool InitializeBusAndDevice();
    void HardwareReset();
    BusyProbe SampleBusy(uint32_t duration_ms);
    bool WaitBusy(uint32_t timeout_ms);
    // 等 BUSY 空闲；BUSY 不可靠时退化为固定延时（返回 true 继续时序）。
    bool WaitBusyOrDelay(uint32_t timeout_ms, uint32_t fixed_delay_ms,
                         const char* stage);
    void Command(uint8_t command, const uint8_t* data = nullptr,
                 size_t length = 0);
    void Data(const uint8_t* data, size_t length);
    // 软件 SPI：1:1 复刻参考工程 epd.h 的 DEV_SPI_WriteByte（每字节一个 CS 脉冲）。
    void BitbangWriteByte(uint8_t byte);
    void BitbangWrite(const uint8_t* data, size_t length);
    bool WakeAndConfigure();
    bool InitSpectra6();   // 7.3" (E) 六色初始化序列
    bool InitAcep7();      // 7.3" (F) 七彩初始化序列
    bool TurnOnDisplay();
    bool ShowFrameOnce(const uint8_t* packed_frame);
    void ProbePinsTask(uint32_t hold_ms);

    static std::atomic<bool> g_refreshing;
    static bool g_default_bitbang;
};
