#include "ssd2683za_display.h"

#include <algorithm>
#include <cassert>
#include <cstring>

#include <esp_heap_caps.h>
#include <esp_log.h>
#include <esp_rom_sys.h>
#include <soc/soc.h>           // for READ_PERI_REG, WRITE_PERI_REG macros
#include <soc/gpio_reg.h>      // for GPIO_IN_REG, GPIO_OUT_REG, GPIO_OUT_SET_REG
#include <soc/io_mux_reg.h>    // for IO_MUX_GPIOx_REG (MCU_SEL field)
#include "assets/lang_config.h"
#include "config.h"
#include "esp_lvgl_port.h"

namespace {
constexpr char kTag[] = "Ssd2683zaDisplay";
constexpr size_t kLvglBufferBytes = DISPLAY_WIDTH * 40 * sizeof(uint16_t);
}

Ssd2683zaDisplay::Ssd2683zaDisplay()
    : LcdDisplay(nullptr, nullptr, DISPLAY_WIDTH, DISPLAY_HEIGHT) {
    InitializeSpi();
    DumpGpioSnapshot("post-spi-init");
    mutex_ = xSemaphoreCreateMutex();
    frame_ = static_cast<uint8_t*>(heap_caps_malloc(kFrameBytes, MALLOC_CAP_SPIRAM));
    tx_frame_ = static_cast<uint8_t*>(heap_caps_malloc(kFrameBytes, MALLOC_CAP_SPIRAM));
    assert(mutex_ && frame_ && tx_frame_);
    memset(frame_, 0xFF, kFrameBytes);  // Four white pixels per byte (11).

    DumpGpioSnapshot("post-malloc-pre-lvgl");
    lv_init();
    DumpGpioSnapshot("post-lv-init");
    lvgl_port_cfg_t port_cfg = ESP_LVGL_PORT_INIT_CONFIG();
    port_cfg.task_priority = 2;
    port_cfg.timer_period_ms = 50;
    lvgl_port_init(&port_cfg);
    DumpGpioSnapshot("post-lvgl-port-init");
    lvgl_port_lock(0);
    display_ = lv_display_create(DISPLAY_WIDTH, DISPLAY_HEIGHT);
    lv_display_set_user_data(display_, this);
    lv_display_set_flush_cb(display_, FlushCallback);
    auto lvgl_buffer = static_cast<uint8_t*>(
        heap_caps_malloc(kLvglBufferBytes, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
    assert(lvgl_buffer);
    lv_display_set_buffers(display_, lvgl_buffer, nullptr, kLvglBufferBytes,
                           LV_DISPLAY_RENDER_MODE_PARTIAL);
    lvgl_port_unlock();
    DumpGpioSnapshot("post-lv-display-create");

    xTaskCreatePinnedToCore(RefreshTaskEntry, "ssd2683_refresh", 4096, this, 3,
                            &refresh_task_, 1);
    DumpGpioSnapshot("post-refresh-task-create");
    // Start a background diagnostic task that samples BUSY every 1ms for
    // 120 seconds. This catches panel-level BUSY transitions and lets us
    // confirm whether the panel ever drives the line.
    xTaskCreatePinnedToCore(BusySnifferTaskEntry, "ssd2683_sniffer", 2048, this,
                            5, &busy_sniffer_task_, 0);
    DumpGpioSnapshot("post-sniffer-task-create");
    // Serialize the initial clear-to-grey with any future refresh task run.
    xSemaphoreTake(mutex_, portMAX_DELAY);
    DumpGpioSnapshot("pre-first-refresh");
    Refresh(frame_);
    DumpGpioSnapshot("post-first-refresh");
    xSemaphoreGive(mutex_);
    // Application::Start() owns UI setup.  Calling it here as well used the
    // shared LCD-style UI path twice and produced a misleading duplicate-UI
    // warning on this e-paper-only board.
    dashboard_.Show(EpaperDashboard::Page::kHome);
    DumpGpioSnapshot("post-dashboard-show");
}

Ssd2683zaDisplay::~Ssd2683zaDisplay() {
    if (busy_sniffer_task_) vTaskDelete(busy_sniffer_task_);
    if (refresh_task_) vTaskDelete(refresh_task_);
    if (spi_) spi_bus_remove_device(spi_);
    if (mutex_) vSemaphoreDelete(mutex_);
    heap_caps_free(frame_);
    heap_caps_free(tx_frame_);
}

void Ssd2683zaDisplay::SetEmotion(const char* emotion) {
    // Emoji and animated emotions are intentionally unsupported on this
    // four-colour e-paper panel.  Keep application state transitions intact
    // while avoiding image lookup, decode, LVGL allocation and refresh work.
    (void)emotion;
}

void Ssd2683zaDisplay::SetStatus(const char* status) {
    // Keep XiaoZhi's normal state machine, but make the two short-lived voice
    // states prominent enough to be useful on a slow, non-animated EPD.
    if (status != nullptr && strcmp(status, Lang::Strings::LISTENING) == 0) {
        voice_overlay_active_ = true;
        dashboard_.HideForVoice();
        LcdDisplay::SetStatus("小悟 listening");
        LcdDisplay::SetChatMessage("system", "小悟 listening");
        return;
    }
    if (status != nullptr && strcmp(status, Lang::Strings::SPEAKING) == 0) {
        voice_overlay_active_ = true;
        dashboard_.HideForVoice();
        LcdDisplay::SetStatus("小悟 speaking");
        LcdDisplay::SetChatMessage("system", "小悟 speaking");
        return;
    }
    LcdDisplay::SetStatus(status != nullptr ? status : "");
    if (voice_overlay_active_) {
        voice_overlay_active_ = false;
        dashboard_.RestoreAfterVoice();
    }
}

uint8_t Ssd2683zaDisplay::Rgb565To2bpp(uint16_t c) {
    const uint8_t r = ((c >> 11) & 0x1f) * 255 / 31;
    const uint8_t g = ((c >> 5) & 0x3f) * 255 / 63;
    const uint8_t b = (c & 0x1f) * 255 / 31;
    // SSD2683ZA 4-color BWRY encoding (per GDEM042F86 reference code from
    // Good Display: 0xAA->Black, 0xFF->White, 0x55->Red, 0x00->Yellow).
    //   00 = Yellow, 01 = Red, 10 = Black, 11 = White
    // Classify RGB into one of the 4 panel colors: red and yellow first
    // (chroma-based), then black/white by luminance.
    const bool has_red   = r > 128;
    const bool has_green = g > 128;
    const bool has_blue  = b > 128;
    if (has_red && has_green && !has_blue) return 0;  // Yellow
    if (has_red && !has_green && !has_blue) return 1;  // Red
    const uint8_t luminance = (77 * r + 150 * g + 29 * b) >> 8;
    if (luminance < 64) return 2;                      // Black
    return 3;                                          // White
}

void Ssd2683zaDisplay::FlushCallback(lv_display_t* display, const lv_area_t* area,
                                     uint8_t* pixels) {
    auto self = static_cast<Ssd2683zaDisplay*>(lv_display_get_user_data(display));
    if (!self || !self->refresh_task_) {
        lv_display_flush_ready(display);
        return;
    }
    const auto src = reinterpret_cast<const uint16_t*>(pixels);
    const int source_width = area->x2 - area->x1 + 1;
    xSemaphoreTake(self->mutex_, portMAX_DELAY);
    for (int y = std::max(0, static_cast<int>(area->y1));
         y <= std::min(DISPLAY_HEIGHT - 1, static_cast<int>(area->y2)); ++y) {
        for (int x = std::max(0, static_cast<int>(area->x1));
             x <= std::min(DISPLAY_WIDTH - 1, static_cast<int>(area->x2)); ++x) {
            const auto color = Rgb565To2bpp(
                src[(y - area->y1) * source_width + x - area->x1]);
            const size_t index = static_cast<size_t>(y) * DISPLAY_WIDTH / 4 + x / 4;
            const unsigned shift = 6 - (x & 3) * 2;
            self->frame_[index] = (self->frame_[index] & ~(0x3u << shift)) |
                                  (color << shift);
        }
    }
    self->refresh_pending_ = true;
    xSemaphoreGive(self->mutex_);
    xTaskNotifyGive(self->refresh_task_);
    lv_display_flush_ready(display);
}

void Ssd2683zaDisplay::RefreshTaskEntry(void* arg) {
    static_cast<Ssd2683zaDisplay*>(arg)->RefreshTask();
}

void Ssd2683zaDisplay::RefreshTask() {
    int consecutive_failures = 0;
    while (true) {
        ulTaskNotifyTake(pdTRUE, portMAX_DELAY);
        vTaskDelay(pdMS_TO_TICKS(250));
        xSemaphoreTake(mutex_, portMAX_DELAY);
        if (!refresh_pending_) {
            xSemaphoreGive(mutex_);
            continue;
        }
        if (consecutive_failures >= 5) {
            static bool warned = false;
            if (!warned) {
                ESP_LOGE(kTag, "Panel not responding after %d retries. "
                         "Display updates suppressed. Hardware check needed.",
                         consecutive_failures);
                warned = true;
            }
            refresh_pending_ = false;
            xSemaphoreGive(mutex_);
            continue;
        }
        memcpy(tx_frame_, frame_, kFrameBytes);
        refresh_pending_ = false;
        ++refresh_count_;
        const uint32_t cs_before = gpio_get_level(EPD_CS_PIN);

        // 强力清屏：每 10 次正常刷新后，先用 0x0E 命令让面板快速清空所有
        // 子像素（红/黄残影的根因是 GC16 波形对红色子像素驱动不足），
        // 再正常全刷内容。0x0E 只需要 1-2s，比手动填充全白 frame 快得多。
        if (refresh_count_ > 0 && refresh_count_ % 10 == 0) {
            ESP_LOGI(kTag, "Refresh: periodic force-clear (#%d) — 0x0E white",
                     refresh_count_);
            InitializePanel();
            SendCommandOnly(0x0E);  // SSD2683ZA CLEAR SCREEN → panel 内部 RAM=全白
            WaitBusy();
            vTaskDelay(pdMS_TO_TICKS(20));
        }

        Refresh(tx_frame_);
        const uint32_t cs_after = gpio_get_level(EPD_CS_PIN);
        if (cs_before == 0 && cs_after == 0) {
            consecutive_failures++;
        } else {
            consecutive_failures = 0;
        }
        xSemaphoreGive(mutex_);
    }
}

// Pin-level self-test for outputs. For each pin we write 0 then 1 back,
// read the actual pad level after a 20µs settle, and classify the result.
// If a pin is configured as output but write(1) reads back 0, that net is
// either externally shorted to GND, connected to a chip's active-low
// reset/oe output, or FPC is plugged in backwards. This is the #1 diagnostic
// we need right now — all previous snapshots showed CS=RST=DC=0 despite our
// code setting them to 1.
static void DiagnoseOutputPin(gpio_num_t pin, const char* name) {
    // Precondition: pin is already configured as GPIO_MODE_OUTPUT by caller.
    // Temporarily switch to input+pullup/down during read? No — for push-pull
    // outputs, gpio_get_level() returns the OUTPUT latch, NOT the pad value,
    // on ESP32. To detect an external clamp, we MUST briefly tri-state the
    // pin (input mode) with pull-up enabled, re-sample, then restore output.
    const int orig_level = gpio_get_level(pin);  // latch, not pad
    gpio_config_t tri = {};
    tri.pin_bit_mask = 1ULL << pin;
    tri.mode = GPIO_MODE_INPUT;
    tri.pull_up_en = GPIO_PULLUP_ENABLE;
    tri.pull_down_en = GPIO_PULLDOWN_DISABLE;
    ESP_ERROR_CHECK(gpio_config(&tri));
    esp_rom_delay_us(20);  // allow pull-up to overcome any residual capacitance
    const int pulled_up = gpio_get_level(pin);
    // Now drive 0 and tri-state with pull-down to detect clamp to VCC.
    gpio_config_t out0 = {};
    out0.pin_bit_mask = 1ULL << pin;
    out0.mode = GPIO_MODE_OUTPUT;
    ESP_ERROR_CHECK(gpio_config(&out0));
    gpio_set_level(pin, 0);
    gpio_config_t tri_down = {};
    tri_down.pin_bit_mask = 1ULL << pin;
    tri_down.mode = GPIO_MODE_INPUT;
    tri_down.pull_up_en = GPIO_PULLUP_DISABLE;
    tri_down.pull_down_en = GPIO_PULLDOWN_ENABLE;
    ESP_ERROR_CHECK(gpio_config(&tri_down));
    esp_rom_delay_us(20);
    const int pulled_down = gpio_get_level(pin);
    // Restore original output mode and saved level.
    gpio_config_t restore = {};
    restore.pin_bit_mask = 1ULL << pin;
    restore.mode = GPIO_MODE_OUTPUT;
    ESP_ERROR_CHECK(gpio_config(&restore));
    gpio_set_level(pin, orig_level);
    const char* verdict;
    if (pulled_up == 0 && pulled_down == 0) {
        verdict = "**HARD SHORT TO GND** — net is clamped to 0 by external "
                  "circuitry (FPC reversed, PCB GND bridge, another IC driving "
                  "low). This explains why write-1 reads 0.";
    } else if (pulled_up == 1 && pulled_down == 1) {
        verdict = "SHORT TO VCC — net is clamped to 3.3V by external load.";
    } else if (pulled_up == 1 && pulled_down == 0) {
        verdict = "OK HIGH-Z — GPIO is not externally clamped. ESP32 push-pull "
                  "output should be able to drive both 0 and 1 freely.";
    } else {
        verdict = "FLOATING/AMBIGUOUS — weak pull-up/down can't dominate; "
                  "possible high-impedance bus conflict.";
    }
    ESP_LOGW("Ssd2683zaDisplay",
             "PIN-TEST %-4s GPIO%2u: orig_latch=%d tri(pullup)=%d tri(pulldown)=%d → %s",
             name, static_cast<unsigned>(pin),
             orig_level, pulled_up, pulled_down, verdict);
}

void Ssd2683zaDisplay::InitializeSpi() {
    ESP_LOGW(kTag,
             "===== DISPLAY INIT ====="
             "MOSI=GPIO%d SCK=GPIO%d CS=GPIO%d DC=GPIO%d RST=GPIO%d BUSY=GPIO%d"
             " (SPI%d_HOST; display pins do not overlap Octal PSRAM)",
             EPD_MOSI_PIN, EPD_SCK_PIN, EPD_CS_PIN,
             EPD_DC_PIN, EPD_RST_PIN, EPD_BUSY_PIN,
             EPD_SPI_NUM);

    // Step 1: Configure CS, DC, RST as push-pull outputs.
    gpio_config_t outputs = {};
    outputs.pin_bit_mask = (1ULL << EPD_CS_PIN) | (1ULL << EPD_DC_PIN) |
                           (1ULL << EPD_RST_PIN);
    outputs.mode = GPIO_MODE_OUTPUT;
    ESP_ERROR_CHECK(gpio_config(&outputs));
    // Use direct register write instead of gpio_set_level because the ESP-IDF
    // gpio_set_level does not reliably set the output latch on these pins.
    // gpio_set_level writes GPIO_OUT_SET/CLR via HAL which may silently fail
    // on ESP32-S3. Writing the W1TS register directly is bulletproof.
    WRITE_PERI_REG(GPIO_OUT_W1TS_REG,
                   (1U << EPD_CS_PIN) | (1U << EPD_DC_PIN) | (1U << EPD_RST_PIN));

    // Step 2: Verify the output latch values immediately after setting them.
    // If these are not 1, something is seriously wrong (driver bug, lock, etc.)
    ESP_LOGI(kTag, "After set_level(1): CS latch=%d DC latch=%d RST latch=%d",
             gpio_get_level(EPD_CS_PIN),
             gpio_get_level(EPD_DC_PIN),
             gpio_get_level(EPD_RST_PIN));

    // Step 2b: Direct register-level test — bypass gpio_set_level and write
    // GPIO_OUT register directly, then read back from GPIO_IN register.
    // All display control pins (CS=9, DC=10, RST=11) are in bank 0 (GPIO 0-31).
    {
        // Read current GPIO_IN register for pins 0-31 directly.
        // ESP32-S3 GPIO_IN register reflects the actual pin pad state.
        uint32_t in_reg = READ_PERI_REG(GPIO_IN_REG);
        int cs_level_hw  = (in_reg >> EPD_CS_PIN)  & 1;
        int dc_level_hw  = (in_reg >> EPD_DC_PIN)  & 1;
        int rst_level_hw = (in_reg >> EPD_RST_PIN) & 1;
        ESP_LOGI(kTag, "Direct GPIO_IN: CS=%d DC=%d RST=%d (before reg write)",
                 cs_level_hw, dc_level_hw, rst_level_hw);

        // Set pins HIGH via direct register write (bypass gpio_set_level).
        // ESP32-S3 GPIO_OUT_W1TS_REG: write 1 to set the output latch bit.
        WRITE_PERI_REG(GPIO_OUT_W1TS_REG,
                       (1U << EPD_CS_PIN) | (1U << EPD_DC_PIN) | (1U << EPD_RST_PIN));

        esp_rom_delay_us(10);

        // Read back GPIO_IN to see if pins actually went HIGH
        in_reg = READ_PERI_REG(GPIO_IN_REG);
        cs_level_hw  = (in_reg >> EPD_CS_PIN)  & 1;
        dc_level_hw  = (in_reg >> EPD_DC_PIN)  & 1;
        rst_level_hw = (in_reg >> EPD_RST_PIN) & 1;
        ESP_LOGI(kTag, "Direct GPIO_IN: CS=%d DC=%d RST=%d (after GPIO_OUT_SET)",
                 cs_level_hw, dc_level_hw, rst_level_hw);

        // Also read GPIO_OUT latch to confirm the register write succeeded
        uint32_t out_reg = READ_PERI_REG(GPIO_OUT_REG);
        int cs_out  = (out_reg >> EPD_CS_PIN)  & 1;
        int dc_out  = (out_reg >> EPD_DC_PIN)  & 1;
        int rst_out = (out_reg >> EPD_RST_PIN) & 1;
        ESP_LOGI(kTag, "GPIO_OUT latch: CS=%d DC=%d RST=%d", cs_out, dc_out, rst_out);
    }

    // Step 2c: IO MUX + GPIO_ENABLE deep diagnostic.
    // ESP32-S3 IO MUX register layout (from soc/io_mux_reg.h):
    //   FUN_PD:   bit 7
    //   FUN_PU:   bit 8
    //   FUN_IE:   bit 9   (input enable)
    //   FUN_DRV:  bits 11:10 (drive strength)
    //   MCU_SEL:  bits 14:12 (function select; 0/1=GPIO, others=peripheral)
    //   FILTER_EN: bit 15
    // GPIO output enable is controlled by GPIO_ENABLE_REG, NOT IO MUX.
    {
        const uint32_t enable_reg = READ_PERI_REG(GPIO_ENABLE_REG);
        const int cs_oe  = (enable_reg >> EPD_CS_PIN)  & 1;
        const int dc_oe  = (enable_reg >> EPD_DC_PIN)  & 1;
        const int rst_oe = (enable_reg >> EPD_RST_PIN) & 1;
        ESP_LOGI(kTag, "GPIO_ENABLE: CS_OE=%d DC_OE=%d RST_OE=%d (1=output enabled)",
                 cs_oe, dc_oe, rst_oe);

        const uint32_t mux9  = READ_PERI_REG(IO_MUX_GPIO9_REG);
        const uint32_t mux10 = READ_PERI_REG(IO_MUX_GPIO10_REG);
        const uint32_t mux11 = READ_PERI_REG(IO_MUX_GPIO11_REG);
        const int sel9  = (mux9  >> 12) & 7;
        const int sel10 = (mux10 >> 12) & 7;
        const int sel11 = (mux11 >> 12) & 7;
        const int ie9   = (mux9  >> 9) & 1;
        const int ie10  = (mux10 >> 9) & 1;
        const int ie11  = (mux11 >> 9) & 1;
        ESP_LOGW(kTag, "IO_MUX GPIO9 : MCU_SEL=%d IE=%d drv=%d (reg=0x%08lx)",
                 sel9, ie9, (int)((mux9 >> 10) & 3), (unsigned long)mux9);
        ESP_LOGW(kTag, "IO_MUX GPIO10: MCU_SEL=%d IE=%d drv=%d (reg=0x%08lx)",
                 sel10, ie10, (int)((mux10 >> 10) & 3), (unsigned long)mux10);
        ESP_LOGW(kTag, "IO_MUX GPIO11: MCU_SEL=%d IE=%d drv=%d (reg=0x%08lx)",
                 sel11, ie11, (int)((mux11 >> 10) & 3), (unsigned long)mux11);
        // ---- GPIO MATRIX OUTPUT SELECT DIAGNOSTIC ---------------------------
        // Even if IO_MUX says MCU_SEL=GPIO, the GPIO MATRIX can override the
        // output source. Each GPIO n has GPIO_FUNCn_OUT_SEL_CFG_REG. The
        // default value is 0x100 (bit 8 set), meaning "use GPIO_OUT_REG as
        // output source". If some boot code / efuse left it as 0x000, the pin
        // output becomes SIGNAL 0 (SIG_GPIO_FUNC0_OUT_IDX) which is a
        // dedicated signal (often 0) rather than the GPIO latch, causing pad
        // to never go high despite OUT_REG=1 and OE=1.
        auto dump_func_out_sel = [&](int gpio, const char* name) {
            const uint32_t addr = 0x60004000 + 0x554 + 4 * gpio;
            const uint32_t v = READ_PERI_REG(addr);
            const int out_sel = v & 0x1FF;
            const int inv = (v >> 9) & 1;
            const int oen_inv = (v >> 11) & 1;
            ESP_LOGW(kTag, "GPIO_MATRIX FUNC_OUT_SEL_CFG GPIO%d(%s): "
                     "out_sel=0x%03X (expected 0x100 for GPIO latch), "
                     "inv=%d oen_inv=%d (reg=0x%08lx)",
                     gpio, name, out_sel, inv, oen_inv, (unsigned long)v);
            // FIX: If default is lost, force to 0x100 = use GPIO_OUT_REG
            if (out_sel != 0x100) {
                ESP_LOGW(kTag, "  -> Forcing out_sel=0x100 (GPIO latch source)");
                WRITE_PERI_REG(addr, (v & ~0x1FF) | 0x100);
            }
        };
        dump_func_out_sel(EPD_CS_PIN,   "CS");
        dump_func_out_sel(EPD_DC_PIN,   "DC");
        dump_func_out_sel(EPD_RST_PIN,  "RST");
        // Also check PA pin (now GPIO7) since it has the same symptom
        dump_func_out_sel(7,             "PA");
        // Re-check pad levels after potential matrix fix
        WRITE_PERI_REG(GPIO_OUT_W1TS_REG,
                       (1U << EPD_CS_PIN) | (1U << EPD_DC_PIN) | (1U << EPD_RST_PIN));
        esp_rom_delay_us(20);
        const uint32_t in3 = READ_PERI_REG(GPIO_IN_REG);
        ESP_LOGW(kTag, "GPIO_IN after MATRIX fix: CS=%d DC=%d RST=%d PA7=%d",
                 (int)((in3 >> EPD_CS_PIN) & 1), (int)((in3 >> EPD_DC_PIN) & 1),
                 (int)((in3 >> EPD_RST_PIN) & 1), (int)((in3 >> 7) & 1));
        if ((sel9 != 0 && sel9 != 1) || (sel10 != 0 && sel10 != 1) || (sel11 != 0 && sel11 != 1)) {
            ESP_LOGE(kTag, "!!! GPIO 9/10/11 MCU_SEL not GPIO — pins owned by "
                     "another peripheral. This is a hardware/efuse config issue.");
        } else if (cs_oe == 0 || dc_oe == 0 || rst_oe == 0) {
            ESP_LOGE(kTag, "!!! GPIO_ENABLE bits are 0 even after gpio_config(OUTPUT) "
                     "— output driver disabled. Attempting manual enable...");
            WRITE_PERI_REG(GPIO_ENABLE_W1TS_REG,
                           (1U << EPD_CS_PIN) | (1U << EPD_DC_PIN) | (1U << EPD_RST_PIN));
            esp_rom_delay_us(10);
            const uint32_t enable2 = READ_PERI_REG(GPIO_ENABLE_REG);
            ESP_LOGW(kTag, "After manual enable: CS_OE=%d DC_OE=%d RST_OE=%d",
                     (int)((enable2 >> EPD_CS_PIN) & 1),
                     (int)((enable2 >> EPD_DC_PIN) & 1),
                     (int)((enable2 >> EPD_RST_PIN) & 1));
            const uint32_t in2 = READ_PERI_REG(GPIO_IN_REG);
            ESP_LOGW(kTag, "After manual enable GPIO_IN: CS=%d DC=%d RST=%d",
                     (int)((in2 >> EPD_CS_PIN) & 1),
                     (int)((in2 >> EPD_DC_PIN) & 1),
                     (int)((in2 >> EPD_RST_PIN) & 1));
        }
    }

    // Step 3: Configure BUSY as input with external pull-up.
    gpio_config_t input = {};
    input.pin_bit_mask = 1ULL << EPD_BUSY_PIN;
    input.mode = GPIO_MODE_INPUT;
    input.pull_up_en = GPIO_PULLUP_ENABLE;
    input.pull_down_en = GPIO_PULLDOWN_DISABLE;
    ESP_ERROR_CHECK(gpio_config(&input));

    // Step 4: PIN-TEST — tri-state each output pin and measure the pad level
    // with internal pull-up/pull-down. This definitively tells us if a pin
    // is being externally clamped to GND or VCC.
    ESP_LOGW(kTag, "===== PIN-TEST START =====");
    DiagnoseOutputPin(EPD_CS_PIN,  "CS");
    DiagnoseOutputPin(EPD_DC_PIN,  "DC");
    DiagnoseOutputPin(EPD_RST_PIN, "RST");
    ESP_LOGW(kTag, "===== PIN-TEST END =====");
    WRITE_PERI_REG(GPIO_OUT_W1TS_REG,
                   (1U << EPD_CS_PIN) | (1U << EPD_DC_PIN) | (1U << EPD_RST_PIN));

    // Step 5: Initialize SPI bus with MOSI=GPIO6, SCK=GPIO8.
    // The SPI peripheral will take exclusive control of these two pins,
    // configuring them for SPI function (not GPIO).
    spi_bus_config_t bus = {};
    bus.mosi_io_num = EPD_MOSI_PIN;
    bus.miso_io_num = -1;
    bus.sclk_io_num = EPD_SCK_PIN;
    bus.quadwp_io_num = -1;
    bus.quadhd_io_num = -1;
    bus.max_transfer_sz = EPD_SPI_BUS_MAX_SZ;
    const esp_err_t bus_ret = spi_bus_initialize(EPD_SPI_NUM, &bus, SPI_DMA_CH_AUTO);
    if (bus_ret != ESP_OK) {
        if (bus_ret == ESP_ERR_INVALID_STATE) {
            ESP_LOGE(kTag,
                     "SPI%d_HOST ALREADY INITIALIZED — conflict! "
                     "Another driver (SD card?) is using the same SPI bus.",
                     EPD_SPI_NUM);
        }
        ESP_LOGE(kTag, "SPI bus init FAILED: %s (0x%x)",
                 esp_err_to_name(bus_ret), bus_ret);
        return;
    }
    ESP_LOGI(kTag, "SPI%d_HOST initialized: MOSI=GPIO%d SCK=GPIO%d max_sz=%d",
             EPD_SPI_NUM, EPD_MOSI_PIN, EPD_SCK_PIN, EPD_SPI_BUS_MAX_SZ);

    spi_device_interface_config_t device = {};
    device.clock_speed_hz = 4 * 1000 * 1000;
    device.mode = 0;
    device.spics_io_num = -1;   // CS is manually controlled via GPIO
    device.queue_size = 1;
    ESP_ERROR_CHECK(spi_bus_add_device(EPD_SPI_NUM, &device, &spi_));

    // Step 6: Final GPIO snapshot after everything is configured.
    // Expected: CS=1, DC=1, RST=1 (outputs), BUSY=1 (input with pull-up)
    // MOSI and SCK will be SPI-controlled (their levels depend on SPI mode).
    DumpGpioSnapshot("post-spi-device-add");
}

void Ssd2683zaDisplay::HardwareReset() {
    vTaskDelay(pdMS_TO_TICKS(20));
    WRITE_PERI_REG(GPIO_OUT_W1TC_REG, 1U << EPD_RST_PIN);  // RST=0
    vTaskDelay(pdMS_TO_TICKS(40));
    WRITE_PERI_REG(GPIO_OUT_W1TS_REG, 1U << EPD_RST_PIN);  // RST=1
    vTaskDelay(pdMS_TO_TICKS(50));
}

bool Ssd2683zaDisplay::WaitBusy(uint32_t timeout_ms) {
    const TickType_t start = xTaskGetTickCount();
    TickType_t last_log = start;
    const int initial = gpio_get_level(EPD_BUSY_PIN);
    ESP_LOGI(kTag, "WaitBusy: enter pin=%d busy_level=%d", initial, EPD_BUSY_LEVEL);
    while (gpio_get_level(EPD_BUSY_PIN) == EPD_BUSY_LEVEL) {
        const TickType_t now = xTaskGetTickCount();
        if ((now - start) * portTICK_PERIOD_MS > timeout_ms) {
            ESP_LOGE(kTag, "BUSY timeout after %lu ms, pin=%d", static_cast<unsigned long>(timeout_ms), gpio_get_level(EPD_BUSY_PIN));
            return false;
        }
        if ((now - last_log) * portTICK_PERIOD_MS >= 5000) {
            ESP_LOGI(kTag, "WaitBusy: still waiting pin=%d elapsed=%lu ms",
                     gpio_get_level(EPD_BUSY_PIN),
                     static_cast<unsigned long>((now - start) * portTICK_PERIOD_MS));
            last_log = now;
        }
        vTaskDelay(pdMS_TO_TICKS(1));
    }
    return true;
}

void Ssd2683zaDisplay::SendCommandRaw(uint8_t command) {
    // NOTE: does NOT touch CS. Caller must have already called CsLow() and
    // is responsible for CsHigh() when the logical transfer is done.
    WRITE_PERI_REG(GPIO_OUT_W1TC_REG, 1U << EPD_DC_PIN);  // DC=0 for command
    spi_transaction_t transaction = {};
    transaction.length = 8;
    transaction.tx_buffer = &command;
    const esp_err_t ret = spi_device_polling_transmit(spi_, &transaction);
    if (ret != ESP_OK) {
        ESP_LOGE(kTag, "SendCommandRaw(0x%02X) spi failed: %s (0x%x)",
                 command, esp_err_to_name(ret), ret);
    }
}

void Ssd2683zaDisplay::SendDataRaw(uint8_t data) {
    SendBufferRaw(&data, 1);
}

void Ssd2683zaDisplay::SendBufferRaw(const uint8_t* data, size_t length) {
    // SPI GDMA on ESP32-S3 cannot reliably read from PSRAM. The frame buffer
    // lives in PSRAM, so we bounce each chunk through an internal-RAM buffer
    // before handing it to the SPI driver. Without this, polling transactions
    // can hang in spi_device_polling_end() and leave polling_trans_in_progress
    // stuck, poisoning all subsequent SPI calls with ESP_ERR_INVALID_STATE.
    // NOTE: does NOT touch CS. Caller controls CS.
    static uint8_t bounce[4096];
    WRITE_PERI_REG(GPIO_OUT_W1TS_REG, 1U << EPD_DC_PIN);  // DC=1 for data
    while (length) {
        const size_t chunk = std::min(length, sizeof(bounce));
        memcpy(bounce, data, chunk);
        spi_transaction_t transaction = {};
        transaction.length = chunk * 8;
        transaction.tx_buffer = bounce;
        const esp_err_t ret = spi_device_polling_transmit(spi_, &transaction);
        if (ret != ESP_OK) {
            ESP_LOGE(kTag, "SendBufferRaw chunk=%u bytes spi failed: %s (0x%x)",
                     static_cast<unsigned>(chunk), esp_err_to_name(ret), ret);
            return;
        }
        data += chunk;
        length -= chunk;
    }
}

void Ssd2683zaDisplay::SendCommandOnly(uint8_t cmd) {
    ESP_LOGI(kTag, "SPI: CMD 0x%02X (no data, CS-framed alone)", cmd);
    CsLow();
    SendCommandRaw(cmd);
    CsHigh();
}

void Ssd2683zaDisplay::SendCommandWithByte(uint8_t cmd, uint8_t data_byte) {
    ESP_LOGI(kTag, "SPI: CMD 0x%02X + DATA 0x%02X (single CS frame)", cmd, data_byte);
    CsLow();
    SendCommandRaw(cmd);
    SendDataRaw(data_byte);
    CsHigh();
}

void Ssd2683zaDisplay::SendCommandWithBuffer(uint8_t cmd, const uint8_t* data, size_t nbytes) {
    ESP_LOGI(kTag, "SPI: CMD 0x%02X + BUFFER %u bytes (single CS frame)",
             cmd, static_cast<unsigned>(nbytes));
    CsLow();
    SendCommandRaw(cmd);
    SendBufferRaw(data, nbytes);
    CsHigh();
}

void Ssd2683zaDisplay::SendDataStream(const uint8_t* data, size_t length) {
    // Used AFTER a previous command (e.g. 0x10 DTM1) that was already
    // acknowledged by the panel via WaitBusy. The command already set up
    // the write-pointer inside the controller; now we just stream bytes
    // with DC=1 in our own CS frame.
    ESP_LOGI(kTag, "SPI: DATA stream %u bytes (DC=1 stream, separate CS frame)",
             static_cast<unsigned>(length));
    CsLow();
    SendBufferRaw(data, length);
    CsHigh();
}

void Ssd2683zaDisplay::DumpGpioSnapshot(const char* label) {
    // Log every panel net level at once so user can cross-check against a
    // multimeter. MOSI/SCK are driven by SPI peripheral so their levels may
    // be idle-high (mode-0) which is expected.
    const int mosi = gpio_get_level(EPD_MOSI_PIN);
    const int sck  = gpio_get_level(EPD_SCK_PIN);
    const int cs   = gpio_get_level(EPD_CS_PIN);
    const int dc   = gpio_get_level(EPD_DC_PIN);
    const int rst  = gpio_get_level(EPD_RST_PIN);
    const int busy = gpio_get_level(EPD_BUSY_PIN);
    ESP_LOGI(kTag,
             "GPIO[%s]: MOSI=%d SCK=%d CS=%d DC=%d RST=%d BUSY=%d "
             "(expected idle: CS=1 RST=1 BUSY=1)",
             label ? label : "-", mosi, sck, cs, dc, rst, busy);
}

void Ssd2683zaDisplay::BusySnifferTaskEntry(void* arg) {
    static_cast<Ssd2683zaDisplay*>(arg)->BusySnifferTask();
}

void Ssd2683zaDisplay::BusySnifferTask() {
    // Sample BUSY with 1ms tick resolution for 120 seconds. This catches
    // every panel-level state transition (RST, charge-pump, waveform) because
    // the SSD2683ZA holds BUSY asserted for 100+ ms during each of those
    // phases. If no edge is ever seen, the BUSY net is physically open or
    // the panel is stuck in deep-sleep / no-power.
    // NOTE: vTaskDelay(1 ms) is used instead of ets_delay_us(500) to avoid
    // pulling in extra ROM headers and to stay scheduler-friendly (the task
    // runs at low priority and should not hog a core for 120 s solid).
    ESP_LOGI(kTag, "BusySniffer: start (120s @ 1ms sample)");
    const uint32_t kTotalMs = 120 * 1000;
    const uint32_t kSummaryEveryMs = 10000;
    uint32_t total_samples = 0;
    uint32_t samples_low = 0;
    uint32_t rising_edges = 0;
    uint32_t falling_edges = 0;
    int prev = gpio_get_level(EPD_BUSY_PIN);
    uint32_t next_summary_ms = kSummaryEveryMs;
    const TickType_t wall_start = xTaskGetTickCount();
    for (uint32_t t = 0; t < kTotalMs; t += 1) {
        vTaskDelay(pdMS_TO_TICKS(1));
        const int cur = gpio_get_level(EPD_BUSY_PIN);
        ++total_samples;
        if (cur == 0) ++samples_low;
        if (prev == 1 && cur == 0) ++falling_edges;
        if (prev == 0 && cur == 1) ++rising_edges;
        prev = cur;
        if ((t % 1024) == 0) {  // ~every 1s, cheap check against wall clock
            const uint32_t elapsed_ms =
                (xTaskGetTickCount() - wall_start) * portTICK_PERIOD_MS;
            if (elapsed_ms >= next_summary_ms) {
                ESP_LOGI(kTag,
                         "BusySniffer: %lums edges up=%u down=%u "
                         "samples=%u low_ratio=%.2f%% cur_level=%d",
                         static_cast<unsigned long>(elapsed_ms),
                         rising_edges, falling_edges,
                         total_samples,
                         total_samples ? (100.0f * samples_low / total_samples) : 0.0f,
                         cur);
                next_summary_ms += kSummaryEveryMs;
            }
        }
    }
    const uint32_t elapsed_ms =
        (xTaskGetTickCount() - wall_start) * portTICK_PERIOD_MS;
    ESP_LOGI(kTag,
             "BusySniffer: FINAL %lums edges up=%u down=%u "
             "samples=%u low_ratio=%.2f%%. If edges=0 the BUSY net is "
             "floating/disconnected or the panel never drives it.",
             static_cast<unsigned long>(elapsed_ms),
             rising_edges, falling_edges,
             total_samples,
             total_samples ? (100.0f * samples_low / total_samples) : 0.0f);
    vTaskDelete(nullptr);
}

void Ssd2683zaDisplay::InitializePanel() {
    // GDEM042F86 (SSD2683ZA, 4-color BWRY) augmented init sequence.
    // Reference (Good Display MSP430 sample) is minimal: Reset → 0xE9 0x01.
    // However our BUSY pin never asserted in logs, so we add diagnostics
    // (GPIO snapshot + double reset) plus extra register writes that should
    // be harmless if the panel's OTP already handles them, but cover the
    // case where the panel was left in deep-sleep (0x07/A5) by a previous
    // run or where the RAM pointers / VCOM interval need a nudge.
    ESP_LOGI(kTag, "InitializePanel: start");
    DumpGpioSnapshot("pre-reset");

    // Double hardware-reset for robustness. If a single reset leaves the
    // panel's OTP controller in a bad state (possible after a partial
    // power-cycle), a second reset re-latches it.
    HardwareReset();
    vTaskDelay(pdMS_TO_TICKS(10));
    DumpGpioSnapshot("post-reset1");
    HardwareReset();
    DumpGpioSnapshot("post-reset2");
    WaitBusy();

    // 0x11 = Deep Sleep Exit (Standby Mode). If the panel entered deep-sleep
    // via 0x07/A5 on a previous run, its SPI interface and BUSY output are
    // tri-stated — this is the most likely cause of a permanently-high
    // (pulled-up) BUSY. The MSP430 reference does not use 0x11 because it
    // powers the panel from cold, but xiaozhi may soft-reset the MCU while
    // leaving the panel's VCC live.
    SendCommandOnly(0x11);
    vTaskDelay(pdMS_TO_TICKS(10));
    WaitBusy();
    DumpGpioSnapshot("post-sleep-out");

    // 0xE9 + 0x01 = Enable OTP waveform. Must be CMD+DATA under one CS frame.
    SendCommandWithByte(0xE9, 0x01);

    // 0x61 = Resolution Setting. 4 bytes: [W_HI, W_LO, H_HI, H_LO].
    // 400 = 0x0190, 300 = 0x012C  →  01 90 01 2C.
    // SSD2683ZA OTP defaults may already match, but setting it explicitly
    // ensures the internal RAM write-window aligns with our 30000-byte frame.
    const uint8_t res[4] = {0x01, 0x90, 0x01, 0x2C};
    SendCommandWithBuffer(0x61, res, 4);

    // 0x50 = VCOM and Data Interval Setting. Typical value for BWRY with
    // border output disabled: 0x97. Harmless even if OTP covers it.
    SendCommandWithByte(0x50, 0x97);

    DumpGpioSnapshot("post-init");
    ESP_LOGI(kTag, "InitializePanel: done");
}

void Ssd2683zaDisplay::Refresh(const uint8_t* data) {
    ESP_LOGI(kTag, "Refresh: begin (frame=%p bytes=%u)", data,
             static_cast<unsigned>(kFrameBytes));

    // Quick health check: if CS is stuck at 0, skip the refresh entirely
    // to avoid flooding the log with useless SPI transactions.
    const uint32_t cs_now = gpio_get_level(EPD_CS_PIN);
    if (cs_now == 0) {
        ESP_LOGW(kTag, "Refresh: CS=0 — panel unresponsive, skipping.");
        return;
    }

    InitializePanel();

    // Step 1: 0x10 (DTM1 start). After CS rises, panel initializes write
    // pointer internally; poll BUSY until it's ready to accept pixel bytes.
    SendCommandOnly(0x10);
    WaitBusy();
    // Step 2: Stream the 30000 BWRY 2-bpp pixel bytes. This is its own CS
    // frame (DC=1 throughout) — the 0x10 command context persists at the
    // panel level, not per-SPI-frame.
    SendDataStream(data, kFrameBytes);

    // Step 3: 0x04 = Power on (charge pump enable). Panel raises BUSY during
    // charge-pump ramp-up. Reference: wait then extra 10ms.
    SendCommandOnly(0x04);
    WaitBusy();
    vTaskDelay(pdMS_TO_TICKS(10));

    // Step 4: 0x12 + 0x00 = Start display refresh (activate waveform engine).
    // This is the actual 12~20 s "flashing" update where the user sees the
    // screen change. Reference: 10ms settle then poll BUSY for completion.
    SendCommandWithByte(0x12, 0x00);
    ESP_LOGI(kTag, "Refresh: waveform engine started — expect 12-20s BUSY active (pin=%d means BUSY)", EPD_BUSY_LEVEL);
    vTaskDelay(pdMS_TO_TICKS(10));
    const bool ok = WaitBusy();
    if (!ok) {
        ESP_LOGE(kTag, "Refresh: waveform BUSY timeout — update did not finish.");
    } else {
        ESP_LOGI(kTag, "Refresh: waveform completed successfully.");
    }

    // Step 5: 0x02 + 0x00 = Power off. Let panel fully discharge before any
    // subsequent operations.
    SendCommandWithByte(0x02, 0x00);
    WaitBusy();
    vTaskDelay(pdMS_TO_TICKS(20));
    ESP_LOGI(kTag, "Refresh: end");
}
