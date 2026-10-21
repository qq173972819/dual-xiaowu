# 既有 S3 功能完整迁移

本工程遵循“先完整迁移，再逐步解耦”的顺序。迁移源与目标如下：

| 原目录 | 新目录 | 用途 |
|---|---|---|
| `dual/` | `apps/content-console-cn/` | 国内 PC 双屏内容与设备配置后台 |
| `miniprogram-xiaowu/` | `apps/miniapp-cn/` | 国内微信小程序完整版本 |
| `firmware-dual/` | `firmware/s3/` | ESP32-S3 双屏固件完整业务源码 |
| `backend/` | `services/legacy-api/` | 保证既有功能可运行的兼容 API |

未迁移内容仅限可再生成或不应公开的运行数据：`node_modules`、`.next`、ESP-IDF `build`、`managed_components`、虚拟环境、SQLite 数据库、上传数据、日志、`.env` 和 `sdkconfig`。依赖锁文件、默认配置、源代码、测试、资源与业务文档均保留。

## 端口

- `8100`：新开放平台 API
- `8101`：PC 运营后台
- `8102`：海外用户 PC 内容配置端
- `8103`：国内 PC 双屏内容配置端（完整迁移自 `dual/`）
- `8104`：既有功能兼容 API（完整迁移自 `backend/`）

后续开发先在迁移版本上建立回归清单，再逐个把兼容 API 合并到新开放平台。未完成回归前不删除旧功能。
