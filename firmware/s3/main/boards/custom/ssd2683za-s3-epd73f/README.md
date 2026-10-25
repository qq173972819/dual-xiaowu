# ssd2683za-s3-epd73f — 7.3 寸七彩电子纸板型（纯增量）

基于 `main_2` 三屏板（S3）新增的**独立板型**，驱动 7.3" 800×480 七色电子纸
（Waveshare 7.3" e-Paper (F) 同款 IC）。**未改动任何既有板型的代码**，仅
在既有文件上做增量：

- `main/CMakeLists.txt`：新增 `BOARD_TYPE` 可覆盖 + 73f 复用白名单
- `main/Kconfig.projbuild`：新增 `BOARD_TYPE_SSD2683ZA_S3_EPD73F` 选项

## 1. 编译（含音频方案切换）

板型 × 音频共 4 种组合，用同一条命令切换：

```bat
.workbuddy\build.bat <epd42|epd73f> <es8311|98357>
```

| 命令 | 板型 | 音频方案 | build 目录 |
|---|---|---|---|
| `build.bat epd73f 98357` | 7.3" 七彩 | MAX98357A + ICS-43434 | `build-73f` |
| `build.bat epd73f es8311` | 7.3" 七彩 | ES8311 + NS4150B | `build-73f` |
| `build.bat epd42 es8311` | 双屏 4.2" | ES8311 + NS4150B | `build` |
| `build.bat epd42 98357` | 双屏 4.2" | MAX98357A + ICS-43434 | `build` |

烧录（可选第三参数指定端口，不填则自动探测）：

```bat
.workbuddy\flash.bat epd73f 98357 COM19
```

切换原理：脚本先调 `.workbuddy/set_config.py` 改写 `sdkconfig` 里的
`CONFIG_BOARD_TYPE_*` 与 `CONFIG_SSD2683ZA_AUDIO_*` 两个 choice，再编译。
两套音频的引脚定义都在 `config.h` 里按宏分支给出，代码共用一套。

两套方案的引脚：

| 方案 | 引脚 |
|---|---|
| ES8311 + NS4150B | I2C SDA=13 SCL=14；I2S MCLK=21 BCLK=18 WS=16 DOUT=15 DIN=17；PA CTRL=GPIO38 |
| MAX98357A + ICS-43434 | SPK BCLK=15 WS=16 DOUT=14 SD=13；MIC SCK=21 WS=17 DIN=18 |

> 注意：一个板型只用一个 build 目录，切换音频会覆盖该目录下的产物
> （cmake 缓存丢失后本环境无法全新配置，见下文"构建环境注意事项"）。

## 1b. 构建环境注意事项

- 全新 build 目录首次 cmake 配置在本机**会失败**（`xtensa-esp32s3-elf-gcc
  is not a full path and was not found in the PATH`），即使 PATH 里有工具链。
  必须复用已有 `CMakeCache.txt` + `CMakeFiles/<ver>/CMake*Compiler.cmake`。
- cmake 配置失败会**删掉 CMakeCache.txt**，一旦丢失需要从上一个可用的
  build 目录把这几个缓存文件复制回来并改写 `CMAKE_CACHEFILE_DIR` 路径。
- 不要给 idf.py 传 `-DCMAKE_C_COMPILER/-DCMAKE_CXX_COMPILER/-DCMAKE_ASM_COMPILER`
  （会强制 cmake 重新探测编译器并触发上述问题），只传
  `-DCMAKE_MAKE_PROGRAM` 指向 ninja。

等价于：

```bat
idf.py -B build-73f ^
       -DBOARD_TYPE=ssd2683za-s3-epd73f ^
       -DBOARD_NAME=ssd2683za-s3-epd73f ^
       -DCMAKE_MAKE_PROGRAM=C:/Users/upisq/.espressif/tools/ninja/1.12.1/ninja.exe build
```

产物：`build-73f/xiaozhi.bin`（app 分区余量约 38%）。

烧录（16MB flash，含 assets）：

```
0x0      build-73f/bootloader/bootloader.bin
0x8000   build-73f/partition_table/partition-table.bin
0xd000   build-73f/ota_data_initial.bin
0x10000  build-73f/generated_assets.bin
0x310000 build-73f/xiaozhi.bin
```

## 2. 硬件接线（重要）

板上的 **FPC3（8pin）与 FPC2 是并联的同一组信号**，不是独立的第三组 SPI。
从 `3屏驱动_main_2_2026-09-23.net` 核对：

| FPC3 pin | 信号 | S3 GPIO |
|---|---|---|
| 1 | BUSY1 | GPIO12 |
| 2 | RST | GPIO11 |
| 3 | DC | GPIO10 |
| 4 | CS1 | GPIO9 |
| 5 | CLK | GPIO8 |
| 6 | DIN | GPIO6 |
| 7 | VDD3V3 | — |
| 8 | GND | — |

> **插 8P→50P 转接板接七彩屏时，FPC2 座必须空置**（两者 CS/BUSY 共用，
> 同时插两片屏会互相干扰）。FPC1（SSD1619，CS=GPIO39/BUSY=GPIO40）是
> 独立信号组，本板型不使用其驱动代码。

SPI 分配：屏幕 **SPI3_HOST**，TF 卡 **SPI2_HOST**（与 epd42 板一致）。

音频：MAX98357A（BCLK=15 / WS=16 / DOUT=14 / SD=13）+ ICS-43434
（SCK=21 / WS=17 / DIN=18）。

## 3. 屏幕时序要点

- 分辨率 800×480，**每字节打包 2 个像素**：高半字节 = 左像素，低半字节 = 右像素，
  故 stride = 400，整帧 **192000 字节**。
- 色索引：0=黑 1=白 2=绿 3=蓝 4=红 5=黄 6=橙。
- 初始化必须先发解锁序列 `0xAA CMDH`，再写一组寄存器（见 `epd_7in3f_panel.cc`
  的 `WakeAndConfigure()`，逐条移植自官方 `epd7in3f` demo）。
- 刷新三步：`0x04` POWER_ON → `0x12` DISPLAY_REFRESH（后跟一个 dummy 0x00）
  → `0x02` POWER_OFF；每步都等 BUSY。
- **BUSY 语义：忙 = 0，空闲 = 1**（`EPD73F_BUSY_IDLE_LEVEL = 1`）。
- 深睡：`0x07 0xA5`，之后把 RST 拉低。
- 全刷约 35 秒，因此刷新统一在后台任务里串行执行，UI 不会卡住。

## 4. 文件结构

| 文件 | 作用 |
|---|---|
| `config.h` | 引脚、阈值、XiaoWu / LightAcade 配置 |
| `config.json` | 板型注册与 `sdkconfig_append` |
| `epd_7in3f_panel.h/.cc` | 七彩屏底层驱动（SPI / 初始化 / 刷帧 / 休眠） |
| `epd73f_display.h/.cc` | LVGL 对接：RGB565 → 7 色最近邻量化 + 后台刷新任务 |
| `INKSIGHT_client.h/.cc` | 800×480 版 XiaoWu 看板客户端（含独立黄色平面） |
| `ssd2683za_s3_73f_board.cc` | 板级类：音频、按键、MCP 工具、定时刷新 |

复用 epd42 目录的两个与分辨率无关的模块（CMake 白名单引入，非整目录 GLOB）：

- `../ssd2683za-s3-epd42/lightacade_client.cc`
- `../ssd2683za-s3-epd42/sd_card_storage.cc`

> 注意：epd42 目录里的 `epaper_dashboard` / `word_machine` / `ssd1619` /
> `uc1679` 等属于双屏板专用，且 dashboard 会 include **400×300 版**
> `INKSIGHT_client.h`，与 73f 的 800×480 版 ABI 不兼容，**不可** GLOB 进来。

## 5. 画面来源与 MCP 工具

- XiaoWu 看板：2bpp 彩色流拆成 **黑 / 黄 / 红三个平面**，七彩屏上黄色走
  独立平面显示真黄（不再像黑白屏那样退化成红）。
- 默认关闭逐句聊天的 UI 刷新（`EPD73F_ENABLE_CHAT_UI = 0`）：35s 全刷不适
  合对话场景。需要聊天界面时把 `epd73f_display.h` 里的该宏改为 1。
- MCP 工具：`self.epaper.set_INKSIGHT_server`、`set_INKSIGHT_persona`、
  `refresh_color_screen`、`set_color_refresh_minutes`、`clear_screen`
  （7 色可选）、`show_color_bars`、`screen_status`、`self.lightacade.*`、
  `self.storage.*`、`self.audio.*`。

## 6. 按键

- `KEY+` / `KEY-`：音量 ±10
- `KEY_OK` 短按：语音助手开/关
- `KEY_OK` 双击：录 10 秒 WAV 到 TF 卡
- `KEY_OK` 长按：取消正在进行的 XiaoWu 下载
- `KEY-` 长按 2s：进入配网模式（先解绑，再起 AP 热点）
- **`KEY+`（GPIO0，兼 BOOT）长按 3s：切换电源模式 活跃 ⇄ 间歇**
  （NVS `XiaoWu/power_mode`，键不存在＝出厂默认**活跃**）

> 排查提示：切换是否真的生效，**不要只看网页上的「间歇状态」角标**。
> 该角标来自后端推导，S3 固件不轮询 `/api/device/<mac>/state`，会被误判成间歇。
> 可靠判据：串口日志 `KEY+ long press (3000ms): toggle power mode`
> → `电源模式切换为 活跃（已写入 NVS，重启保持）`；且切到活跃后屏幕**立刻刷一屏**，
> 自动刷新间隔从 60 分钟降到 5 分钟。
