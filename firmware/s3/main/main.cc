#include <esp_log.h>
#include <esp_err.h>
#include <nvs.h>
#include <nvs_flash.h>
#include <driver/gpio.h>
#include <esp_event.h>
#include <esp_system.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>

#include "application.h"

#define TAG "main"

extern "C" void app_main(void)
{
    // 复位原因：1=POWERON 2=EXT(EN/按键) 3=SW(软件重启) 4=PANIC(崩溃)
    // 5=INT_WDT(中断看门狗) 6=TASK_WDT(任务看门狗) 7=WDT 8=DEEPSLEEP
    // 9=BROWNOUT(欠压) 11=USB 12=LPWDT 15=CPU_LOCKUP
    // 设备反复重启时，先看这一行确认根因
    esp_reset_reason_t rr = esp_reset_reason();
    ESP_LOGW(TAG, "=== RESET REASON: %d ===", (int)rr);
    if (rr == ESP_RST_BROWNOUT) {
        ESP_LOGE(TAG, "BROWNOUT reset! 供电不足/电池电压跌落（通常是 WiFi 发射瞬间大电流导致）");
    } else if (rr == ESP_RST_PANIC || rr == ESP_RST_INT_WDT || rr == ESP_RST_TASK_WDT) {
        ESP_LOGE(TAG, "CRASH/WDT reset! 查看上方 Guru Meditation / backtrace");
    }

    // Initialize NVS flash for WiFi configuration
    esp_err_t ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES || ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_LOGW(TAG, "Erasing NVS flash to fix corruption");
        ESP_ERROR_CHECK(nvs_flash_erase());
        ret = nvs_flash_init();
    }
    ESP_ERROR_CHECK(ret);

    // Initialize and run the application
    auto& app = Application::GetInstance();
    app.Initialize();
    app.Run();  // This function runs the main event loop and never returns
}
