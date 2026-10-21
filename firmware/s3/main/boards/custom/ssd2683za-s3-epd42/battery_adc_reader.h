#ifndef BATTERY_ADC_READER_H_
#define BATTERY_ADC_READER_H_

#include <stdint.h>
#include <mutex>

// 自包含电池电压/电量采集器：
//  - ADC1 单次采样（oneshot）+ 曲线拟合校准
//  - 分压比取自 config.h 的 BATTERY_UPPER_RESISTOR / BATTERY_LOWER_RESISTOR
//    （100k/100k -> 2:1；200k/100k -> 3:1）
//  - 2 秒周期采样、指数滑动平均，结果稳定不跳变
//  - ADC 引脚接近 0V（未接电池）时 GetLevel() 返回 -1
class BatteryAdcReader {
public:
    BatteryAdcReader();
    ~BatteryAdcReader();

    // 返回 0~100；-1 表示未检测到电池
    int GetLevel();

    BatteryAdcReader(const BatteryAdcReader&) = delete;
    BatteryAdcReader& operator=(const BatteryAdcReader&) = delete;

private:
    void Sample();
    static void OnTimer(void* arg);

    void* adc_unit_ = nullptr;   // adc_oneshot_unit_handle_t
    void* adc_cali_ = nullptr;   // adc_cali_handle_t
    void* timer_ = nullptr;      // esp_timer_handle_t

    bool have_reading_ = false;  // 是否已有过一次有效采样
    int level_ = -1;             // 平滑后的电量百分比
    int battery_mv_ = 0;         // 推算的电池电压
    int pin_mv_ = 0;             // ADC 引脚上的实测电压
    int tick_ = 0;
    std::mutex sample_mutex_;
};

#endif  // BATTERY_ADC_READER_H_
