#include "battery_adc_reader.h"

#include <esp_adc/adc_oneshot.h>
#include <esp_adc/adc_cali.h>
#include <esp_adc/adc_cali_scheme.h>
#include <esp_log.h>
#include <esp_timer.h>

#include "config.h"

namespace {
constexpr const char* kTag = "BatteryAdc";

// 标准 3.7V 锂聚合物放电曲线（满电 4.2V），相邻点线性插值。
struct CurvePoint { int mv; int pct; };
constexpr CurvePoint kCurve[] = {
    {4200, 100}, {4100, 95}, {4000, 85}, {3920, 75}, {3850, 60},
    {3790, 50},  {3740, 40}, {3680, 30}, {3620, 20}, {3550, 10},
    {3450, 5},   {3300, 0},
};

int MvToPercent(int mv) {
    if (mv >= kCurve[0].mv) return 100;
    for (size_t i = 1; i < sizeof(kCurve) / sizeof(kCurve[0]); ++i) {
        if (mv >= kCurve[i].mv) {
            const int span_mv = kCurve[i - 1].mv - kCurve[i].mv;
            const int span_pct = kCurve[i - 1].pct - kCurve[i].pct;
            return kCurve[i].pct +
                   (int)((float)(mv - kCurve[i].mv) * span_pct / span_mv + 0.5f);
        }
    }
    return 0;
}
}  // namespace

BatteryAdcReader::BatteryAdcReader() {
    adc_oneshot_unit_handle_t unit = nullptr;
    adc_oneshot_unit_init_cfg_t init_cfg = {
        .unit_id = BATTERY_ADC_UNIT,
        .ulp_mode = ADC_ULP_MODE_DISABLE,
    };
    ESP_ERROR_CHECK(adc_oneshot_new_unit(&init_cfg, &unit));
    adc_unit_ = unit;

    adc_oneshot_chan_cfg_t chan_cfg = {
        .atten = ADC_ATTEN_DB_12,
        .bitwidth = ADC_BITWIDTH_DEFAULT,
    };
    ESP_ERROR_CHECK(adc_oneshot_config_channel(
        unit, static_cast<adc_channel_t>(BATTERY_ADC_CHANNEL), &chan_cfg));

#if ADC_CALI_SCHEME_CURVE_FITTING_SUPPORTED
    adc_cali_curve_fitting_config_t cali_cfg = {
        .unit_id = BATTERY_ADC_UNIT,
        .chan = static_cast<adc_channel_t>(BATTERY_ADC_CHANNEL),
        .atten = ADC_ATTEN_DB_12,
        .bitwidth = ADC_BITWIDTH_DEFAULT,
    };
    adc_cali_handle_t cali = nullptr;
    if (adc_cali_create_scheme_curve_fitting(&cali_cfg, &cali) == ESP_OK) {
        adc_cali_ = cali;
    } else {
        ESP_LOGW(kTag, "ADC curve-fitting calibration unavailable");
    }
#elif ADC_CALI_SCHEME_LINE_FITTING_SUPPORTED
    adc_cali_line_fitting_config_t cali_cfg = {
        .unit_id = BATTERY_ADC_UNIT,
        .atten = ADC_ATTEN_DB_12,
        .bitwidth = ADC_BITWIDTH_DEFAULT,
    };
    adc_cali_handle_t cali = nullptr;
    if (adc_cali_create_scheme_line_fitting(&cali_cfg, &cali) == ESP_OK) {
        adc_cali_ = cali;
    }
#endif

    // 首次采样立即执行，之后每 2 秒一次。
    esp_timer_handle_t timer = nullptr;
    esp_timer_create_args_t timer_cfg = {
        .callback = &BatteryAdcReader::OnTimer,
        .arg = this,
        .dispatch_method = ESP_TIMER_TASK,
        .name = "battery_adc",
        .skip_unhandled_events = true,
    };
    ESP_ERROR_CHECK(esp_timer_create(&timer_cfg, &timer));
    timer_ = timer;
    ESP_ERROR_CHECK(esp_timer_start_periodic(timer, 2 * 1000 * 1000));
    Sample();
}

BatteryAdcReader::~BatteryAdcReader() {
    if (timer_) {
        esp_timer_stop(static_cast<esp_timer_handle_t>(timer_));
        esp_timer_delete(static_cast<esp_timer_handle_t>(timer_));
    }
    if (adc_cali_) {
#if ADC_CALI_SCHEME_CURVE_FITTING_SUPPORTED
        adc_cali_delete_scheme_curve_fitting(
            static_cast<adc_cali_handle_t>(adc_cali_));
#elif ADC_CALI_SCHEME_LINE_FITTING_SUPPORTED
        adc_cali_delete_scheme_line_fitting(
            static_cast<adc_cali_handle_t>(adc_cali_));
#endif
    }
    if (adc_unit_) {
        adc_oneshot_del_unit(static_cast<adc_oneshot_unit_handle_t>(adc_unit_));
    }
}

void BatteryAdcReader::OnTimer(void* arg) {
    static_cast<BatteryAdcReader*>(arg)->Sample();
}

void BatteryAdcReader::Sample() {
    std::lock_guard<std::mutex> lock(sample_mutex_);
    auto* unit = static_cast<adc_oneshot_unit_handle_t>(adc_unit_);
    auto* cali = static_cast<adc_cali_handle_t>(adc_cali_);
    const auto channel = static_cast<adc_channel_t>(BATTERY_ADC_CHANNEL);

    // 8 次采样，逐次转电压后平均，抑制噪声。
    constexpr int kSamples = 8;
    int mv_sum = 0;
    int valid = 0;
    for (int i = 0; i < kSamples; ++i) {
        int raw = 0;
        if (adc_oneshot_read(unit, channel, &raw) != ESP_OK) continue;
        int mv = 0;
        if (cali) {
            if (adc_cali_raw_to_voltage(cali, raw, &mv) == ESP_OK) {
                mv_sum += mv;
                ++valid;
            }
        } else {
            // 无校准时按 12bit/3.3V 粗估（精度较差，仅兜底）。
            mv_sum += raw * 3300 / 4095;
            ++valid;
        }
    }
    if (valid == 0) return;

    const int pin_mv = mv_sum / valid;

    // 分压：V_pin = V_bat * R_lower/(R_upper+R_lower)
    // V_bat = V_pin * (R_upper+R_lower)/R_lower
    constexpr float kDivider =
        (BATTERY_UPPER_RESISTOR + BATTERY_LOWER_RESISTOR) /
        BATTERY_LOWER_RESISTOR;

    int new_level;
    int battery_mv = 0;
    if (pin_mv < 150) {
        // ADC 引脚接近 0V：未装电池（下分压电阻把引脚拉到地）。
        new_level = -1;
    } else {
        battery_mv = (int)(pin_mv * kDivider);
        if (battery_mv > 4300) battery_mv = 4300;  // USB 直供等异常高压钳位
        new_level = MvToPercent(battery_mv);
    }

    if (!have_reading_) {
        level_ = new_level;
        have_reading_ = true;
    } else if (new_level < 0) {
        level_ = new_level;  // 电池拔出立即生效
    } else if (level_ >= 0) {
        // 指数滑动平均（alpha=0.35），显示不抖动。
        level_ = (35 * new_level + 65 * level_) / 100;
    } else {
        level_ = new_level;  // 从无电池恢复
    }
    pin_mv_ = pin_mv;
    battery_mv_ = battery_mv;
    ++tick_;

    // 首次 + 每 30 秒打印一次原始值，方便核对分压比/接线。
    if (tick_ == 1 || (tick_ % 15) == 0) {
        if (level_ < 0) {
            ESP_LOGI(kTag,
                     "no battery detected (ADC pin=%dmV, divider=%.2f:1)",
                     pin_mv, kDivider);
        } else {
            ESP_LOGI(kTag,
                     "ADC pin=%dmV -> battery=%dmV (divider=%.2f:1) -> %d%%",
                     pin_mv, battery_mv_, kDivider, level_);
        }
    }
}

int BatteryAdcReader::GetLevel() {
    // 首帧可能早于 VBAT 稳定；无有效结果时同步补采一次，避免状态栏
    // 一直保留启动阶段的无电池状态，直到用户切换页面才更新。
    if (!have_reading_ || level_ < 0) {
        Sample();
    }
    return level_;
}
