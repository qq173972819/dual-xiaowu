# 设备协议 v1

设备使用唯一 `X-Device-Token`，只能访问自己的资源。

| 接口 | 用途 |
|---|---|
| `POST /api/v1/devices/register` | 工厂/开发注册 |
| `POST /api/v1/devices/{id}/heartbeat` | 心跳与运行状态 |
| `GET /api/v1/devices/{id}/capabilities` | 能力描述 |
| `GET /api/v1/devices/{id}/tasks/next` | 拉取下一任务 |
| `POST /api/v1/devices/{id}/tasks/{task}/ack` | 显示结果确认 |
| `POST /api/v1/devices/{id}/events` | 按键、确认、语音意图事件 |

生产环境关闭匿名注册，由工厂注入凭据或一次性激活完成注册。
