# SSD2683ZA ESP32-S3 ePaper 4.2

Custom ESP32-S3R8 board with a 400x300 SSD2683ZA 2-bpp e-paper panel,
ES8311 audio codec, ZTS6216 analog microphone and NS4150B amplifier.

The pin assignments in `config.h` were taken from
`Netlist_PCB_Main_2026-08-13.net` and the dual-socket schematic
`docs/eda/Netlist_schematic_main_1_2026-09-15.net`. The display
initialization and refresh sequence were ported from the hardware-tested
`firmware-2683ZA-s3-epd42-2bpp-fix-source` project.

The board uses XiaoZhi's standard AFE wake-word, microphone capture, cloud
conversation and speaker playback pipeline. During a conversation the fast
EPD shows the conversation transcript and learning pages.

## Dual-screen configuration (双排座双屏)

The board exposes two symmetric 24-pin e-paper sockets sharing
DIN(GPIO6)/CLK(GPIO8)/DC(GPIO10)/RST(GPIO11), with independent CS/BUSY:

| Socket | Role | Panel | CS | BUSY |
| --- | --- | --- | --- | --- |
| FPC1 | 对话信息屏 + 学习 dashboard（快刷） | SSD1619 4.2" 黑白 1bpp | GPIO39 | GPIO40 |
| FPC2 | XiaoWu 模式画面（服务端渲染） | UC1679 4.2" 黑白红三色 2bpp | GPIO9 | GPIO12 |

- `Ssd1619Display`（FPC1）remains the `GetDisplay()` panel: voice
  conversation transcript, home menu, words trainer, quota page. Its
  2-phase partial LUT provides the fast-refresh behavior.
- `Uc1679Display`（FPC2，`uc1679_display.cc`）renders XiaoWu mode
  frames. `InkSightClient::Load2bpp` requests
  `/api/render?bpp=2&colors=4` and splits the 30000-byte raw 2bpp stream
  (0=black 1=white 2=yellow 3=red) into B/W and RED planes; yellow pixels
  map to red by default (`EPD_YELLOW_AS_RED`). Each refresh runs the full
  16 s waveform and deep-sleeps afterwards; there is no partial waveform.
- Refresh arbitration: both sockets share RST(GPIO11). While the color
  panel refreshes (`Uc1679Display::IsRefreshing`), SSD1619 hardware resets
  wait; the color driver likewise waits for SSD1619 BUSY before resetting.
- Startup probe: if the FPC2 socket is empty, `Init()` fails and the
  firmware degrades to the legacy single-screen behavior (XiaoWu frames
  rendered as 1-bit BMP on the SSD1619).
- Auto refresh: `ColorRefreshTask` refreshes the color screen every
  `color_refresh_minutes` (NVS `XiaoWu`, default 30; 0 disables).
- Build switch: `EPD_ENABLE_COLOR_SCREEN=0` compiles the color screen out.
- New MCP tools: `self.epaper.refresh_color_screen`,
  `self.epaper.set_color_refresh_minutes`, `self.epaper.screen_status`.

UC1679 tunables (`kPanelSetting`/`kPowerSetting`/`kBooster`) live at the
top of `uc1679_display.cc`; if the panel does not light, compare them with
the module vendor's UC1679 demo values.

## Independent information dashboard

`epaper_dashboard.cc/.h` is an original implementation for this board. It does
not copy code, layouts, images, fonts, or web resources from the non-commercial
`miaomiao` project.

Implemented pages:

- Home page with local date/time and usage hints.
- 400x300 monthly calendar with Monday-first layout and today highlighting.
- NVS-backed daily schedule.
- Album entry and persistent photo caption (image persistence is planned).
- NVS-backed generic API quota summary.

The BOOT button keeps its original single-click voice action. Double-clicking
cycles through dashboard pages. Dashboard pages are hidden while XiaoZhi is
listening or speaking and restored after the voice session.

The following MCP tools let XiaoZhi control the display by voice:

- `self.epaper.show_page`
- `self.epaper.set_schedule`
- `self.epaper.set_album_caption`
- `self.epaper.set_quota`

The quota page intentionally stores only numeric summary values. It does not
store an OpenAI login, browser cookie, or account password. A future network
adapter may fetch a user-selected service's documented JSON endpoint.

The LVGL flush callback only updates a PSRAM framebuffer. A dedicated
FreeRTOS task coalesces UI changes and performs the slow panel refresh, so
EPD BUSY waits do not block XiaoZhi audio capture, playback or networking.

## TF card

The onboard XKTF-015-G socket uses a dedicated SDSPI bus: CS GPIO1, MISO GPIO2,
SCK GPIO4 and MOSI GPIO5. Use FAT16/FAT32 and place media under:

- `/xiaozhi/images`: PNG/JPEG images
- `/xiaozhi/audio`: mono 16-bit PCM WAV at 24 kHz
- `/xiaozhi/recordings`: recordings created by the device

The firmware still starts normally if no card is inserted. It never formats a card
automatically. Media file names are restricted to a single path component.

Verified with ESP-IDF 5.5.2:

```sh
idf.py -DBOARD_TYPE=custom/ssd2683za-s3-epd42 -DBOARD_NAME=ssd2683za-s3-epd42 build
```

Hardware validation should cover microphone capture, wake word, playback,
NS4150B mute/unmute, Wi-Fi provisioning, and repeated full display refreshes.
