# LightAcade 口语陪练 Partner API 接入文档（试点版）

> 版本 2026-09-01 · 试点环境

## 1. 概述与试点声明

Partner API 提供 AI 实时口语陪练能力：您的用户与 AI 进行低延迟语音对话（英/日/法/德/西/葡/芬/阿等语种），AI 主动开场、实时字幕、按 K12 学段调整话题。

**试点声明**：当前为测试环境接入，不承诺可用性 SLA；协议可能变更，变更将提前通知。每个 API key 有每日音频时长配额与并发会话上限。

- 接入地址：`https://api.aioneschool.com`
- 鉴权：HTTP 头 `X-Api-Key: <您的密钥>`。密钥泄露请立即联系我们吊销换发。

## 2. 接入流程（两步）

### 第 1 步：换取一次性会话票据

```
POST /api/partner/v1/speech/live-tickets
X-Api-Key: lap_xxxxxxxx
Content-Type: application/json

{"language": "en", "gradeBand": "junior", "voice": "Puck"}
```

| 字段 | 必填 | 说明 |
|---|---|---|
| `language` | 是 | 语种 code，完整可选表见「附录 A」（以您 key 的白名单为准） |
| `gradeBand` | 是 | 学段（决定话题与口吻）：`primary_lower` / `primary_upper` / `junior` / `senior` |
| `voice` | 否 | 音色 code，完整可选表见「附录 A」；未知值回落默认音色（Puck） |

成功响应（200）：

```json
{"ticket": "97739f05…", "expiresInSeconds": 60,
 "wsPath": "/api/v1/speech/live-practice/stream"}
```

票据**一次性**且约 60 秒过期：拿到后立即建连，一张票只能用一次。

### 第 2 步：建立 WebSocket 会话

```
wss://api.aioneschool.com{wsPath}?ticket={ticket}
```

## 3. WebSocket 协议

**上行（客户端 → 服务端）**

| 帧 | 格式 | 说明 |
|---|---|---|
| 音频 | binary | PCM16、16000Hz、单声道，建议 20ms/帧连续推送（静音也推） |
| 结束 | text `{"type":"stop"}` | 主动结束会话，随后收 `end` 帧 |

**下行（服务端 → 客户端）**

| 帧 | 说明 |
|---|---|
| binary | AI 语音，PCM16、24000Hz、单声道，直接送播放器 |
| `{"type":"turn_start","speaker":"tutor|student","turnIndex":n}` | 一轮发言开始 |
| `{"type":"transcript","speaker":…,"text":…,"turnIndex":n}` | 流式字幕（同轮累积全文，覆盖式渲染） |
| `{"type":"interrupted"}` | AI 被用户抢话打断，清空未播完的音频缓冲 |
| `{"type":"end","endReason":…,"durationS":…,"studentUtterances":…}` | 会话终帧 |
| `{"type":"error","error":{"code":…}}` | 错误 |

会话行为：连接建立后 **AI 会主动开场**，用户无需先说话；会话有服务端硬时长上限与静默超时，到点自动收 `end`（`endReason` = `max_duration` / `idle_timeout` / `client_closed`）。

## 4. 错误码

| HTTP/close | code | 含义与处理 |
|---|---|---|
| 401 | `INVALID_API_KEY` | key 错误或已停用 |
| 400 | `UNSUPPORTED_LANGUAGE` / `UNSUPPORTED_GRADE_BAND` | 参数不在您的白名单内 |
| 429 | `PARTNER_QUOTA_EXCEEDED` | 当日时长配额用尽（次日 00:00 UTC+8 重置），不要重试 |
| 429 | `PARTNER_CONCURRENCY_LIMIT` | 并发会话满，稍后重试 |
| 503 | `ORAL_PRACTICE_DISABLED` 等 | 服务暂不可用，稍后重试 |
| WS close 4401 | — | 票据无效/已用/过期，重新换票 |
| WS close 4429 | `PARTNER_CONCURRENCY_LIMIT` / `LIVE_PRACTICE_BUSY` | 并发满（您的上限或系统总容量） |

## 5. 参考客户端

随文档附 `partner_demo.py`（Python 3.11+，`pip install httpx websockets`，麦克风模式另需 `sounddevice`）：

```bash
# 连通性验证（静音上行，看 AI 开场白字幕）
python partner_demo.py --key lap_xxx

# 真实对话（麦克风 + 扬声器）
python partner_demo.py --key lap_xxx --mic --seconds 60

# 用 wav 文件模拟学生说话（16kHz/mono/PCM16）
python partner_demo.py --key lap_xxx --wav hello.wav
```

集成建议：票据换取放在**您的服务端**（key 不下发到终端用户设备），终端拿票后直连我们的 WebSocket。

## 6. 配额语义

- 每 key 每日音频时长配额（您当前为 **3 小时/日**），会话结束后按实际时长扣减；用尽后当日不再签发票据（次日 00:00 UTC+8 重置）。
- 每 key 并发会话上限；票据签发与建连两处都会校验。
- 需调整配额/白名单请联系我们。

## 附录 A：可选语种与音色

**语种（`language` 字段）**

| code | 语种 |
|---|---|
| `en` | English（英语） |
| `ja` | 日本語（日语） |
| `fr` | Français（法语） |
| `de` | Deutsch（德语） |
| `es` | Español（西班牙语） |
| `pt` | Português（葡萄牙语） |
| `fi` | Suomi（芬兰语） |
| `ar` | العربية（阿拉伯语） |

**音色（`voice` 字段，均支持全部语种）**

| code | 人设名 |
|---|---|
| `Puck` | 小阳（默认） |
| `Aoede` | 阿依 |
| `Leda` | 小满 |
| `Kore` | 可蕊 |
| `Charon` | 夏老师 |
| `Zephyr` | 泽风 |

**学段（`gradeBand` 字段，决定话题与口吻）**

| code | 学段 |
|---|---|
| `primary_lower` | 小学低年级 |
| `primary_upper` | 小学高年级 |
| `junior` | 初中 |
| `senior` | 高中 |

> 以上为当前启用集，后续增减会提前通知。语种以您 key 的白名单为准（当前未设白名单 = 全部可用）。
