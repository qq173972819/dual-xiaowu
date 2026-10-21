# SSD2683ZA ESP32-S3 ePaper (dual-screen)

Custom ESP32-S3R8 board with two e-paper sockets (FPC1 + FPC2). Each socket
is independently configurable in `idf.py menuconfig` as **4.2" (400x300)** or
**5.83" (648x480)**:
- **FPC1**: SSD1619 black/white fast-refresh information display (XiaoZhi
  conversation + learning dashboard). The main UI / `GetDisplay()` panel.
- **FPC2**: SSD2683ZA black/white/yellow/red image display (album / InkSight).
It uses an ES8311 audio codec, ZTS6216 analog microphone and NS4150B amplifier.

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

| Socket | Role | Panel (default) | CS | BUSY |
| --- | --- | --- | --- | --- |
| FPC1 / CS2 | 对话信息屏 + 学习 dashboard（快刷） | SSD1619 黑白 1bpp（默认 4.2"，可配 5.83"） | GPIO39 | GPIO40 |
| FPC2 / CS1 | 相册 / InkSight 图像 | SSD2683ZA 黑白黄红四色 2bpp（默认 4.2"，可配 5.83"） | GPIO9 | GPIO12 |

### Panel size configuration (menuconfig)

In `idf.py menuconfig` → **Xiaozhi Assistant**, the following choices appear
only for this board (`BOARD_TYPE_SSD2683ZA_S3_EPD42`):

- **FPC1 (B/W fast-refresh) panel size**: `4.2 inch (400x300)` / `5.83 inch (648x480)`
- **FPC2 (color image) panel size**: `4.2 inch (400x300)` / `5.83 inch (648x480)`
- **FPC2 (color image) panel color count**: `4-color (B/W/Y/R)` / `3-color (B/W/R)`

The two sockets are configured independently. All buffer sizes, the InkSight
`/api/render` request dimensions, the JPEG-to-BWR converter and the SSD2683ZA
`0x61` resolution command derive from these choices, so a 4.2"/5.83" mix builds
correctly.

> **5.83" is a configuration placeholder.** The SSD1619 on FPC1 is a 400x300
> controller and cannot drive a 5.83" (648x480) panel — selecting FPC1 5.83"
> requires a different B/W controller driver (follow-up). On FPC2 the SSD2683ZA
> *can* drive a larger panel (it also runs the 7.3" board), but the 5.83"
> waveform / `0x62` parameters differ from 4.2" and are not yet tuned. Build
> with 5.83" only after the matching panel init sequence is added.

- `Ssd1619Display`（FPC1 / CS2）remains the `GetDisplay()` panel: voice
  conversation transcript, home menu, words trainer, quota page. Its
  2-phase partial LUT provides the fast-refresh behavior.
- `Ssd2683zaColorPanel` drives FPC2 using the project's SSD2683ZA / GDEM042F86
  initialization sequence. `InkSightClient::Load2bpp` requests
  `/api/render?bpp=2&colors=4`; the B/W and color-marker planes are repacked
  into SSD2683ZA RAM codes (0=black, 1=white, 2=yellow, 3=red), matching the
  existing controller reference driver. Thus the
  unbound MAC/pair-code image and the owner's configured mode both use all
  four panel colors. Each refresh is a full refresh.
- On first network connection, FPC2 requests `/api/render` with the device MAC.
  The backend's unbound response contains the InkSight pairing code and MAC;
  while unbound the firmware refreshes it every four minutes (the code expires
  after ten minutes). Once claimed, the same endpoint serves the owner's
  configured image instead. FPC1 remains the XiaoZhi conversation/activation
  information display throughout.
- Refresh arbitration: both sockets share RST(GPIO11). While the color
  panel refreshes (`Ssd2683zaColorPanel::IsRefreshing`), SSD1619 hardware resets
  wait; the color driver likewise waits for SSD1619 BUSY before resetting.
- Startup probe: if the FPC2 socket is empty, `Init()` fails and the
  firmware degrades to the legacy single-screen behavior (InkSight frames
  rendered as 1-bit BMP on the SSD1619).
- Auto refresh: `ColorRefreshTask` refreshes the color screen every
  `color_refresh_minutes` (NVS `inksight`, default 30; 0 disables).
- Build switch: `EPD_ENABLE_COLOR_SCREEN=0` compiles the color screen out.
- New MCP tools: `self.epaper.refresh_color_screen`,
  `self.epaper.set_color_refresh_minutes`, `self.epaper.screen_status`.

The SSD2683ZA initialization and frame-write sequence lives in
`ssd2683za_color_panel.cc`. If the panel does not respond, compare it against
the exact panel/module vendor demo (especially BUSY polarity and waveform/LUT).

## Independent information dashboard

`epaper_dashboard.cc/.h` is an original implementation for this board. It does
not copy code, layouts, images, fonts, or web resources from the non-commercial
`miaomiao` project.

Implemented pages:

- Home page with local date/time and usage hints.
- 400x300 monthly calendar with Monday-first layout and today highlighting.
- NVS-backed daily schedule.
- The `self.epaper.show_sd_image` tool displays JPG/JPEG files from
  `/xiaozhi/images` on FPC2 using the onboard JPEG decoder and a B/W+red
  ordered-dither conversion. `self.epaper.show_backend_album` displays the
  device owner's MY_ADAPTIVE Photo Frame from InkSight on FPC2.
- Put JPEG files under the TF card's `/xiaozhi/images` directory, then ask
  XiaoZhi to call `self.epaper.show_sd_image` with the file name. For a photo
  uploaded to the device's InkSight Photo Frame configuration, call
  `self.epaper.show_backend_album`.
- Album caption is persisted on the FPC1 information dashboard.
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
