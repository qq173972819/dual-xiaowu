#pragma once
#include <freertos/FreeRTOS.h>
#include <freertos/semphr.h>

// FPC1(SSD1619) 与 FPC2(SSD2683ZA) 共享 SPI 总线与 DC/RST 控制线。
// 双屏取图/刷屏已移出 Application 主循环（否则 20~45s 的面板刷新会
// 阻塞语音唤醒等事件处理），因此两块面板的物理刷新可能在不同任务
// 并发发生——所有触碰总线的刷新入口都必须经过这把全局锁。
SemaphoreHandle_t EpaperBusMutex();

inline void EpaperBusLock() { xSemaphoreTake(EpaperBusMutex(), portMAX_DELAY); }
inline void EpaperBusUnlock() { xSemaphoreGive(EpaperBusMutex()); }

// RAII：函数内任意 return 都保证解锁。
struct EpaperBusGuard {
    EpaperBusGuard() { EpaperBusLock(); }
    ~EpaperBusGuard() { EpaperBusUnlock(); }
};
