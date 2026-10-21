# 小智网关·连接级语种/音色选择 —— 固件侧对接说明

(LightAcade #3444 / PR #3445;服务端已上线首尔测试环境并真机验证,2026-09-08)

## 一、功能一句话

设备每次建立 WebSocket 连接时,可在 hello 报文里声明本次会话的语种和音色;不声明则用账户默认值(smp 当前:en / Puck)。**换语种 = 断开重连**,会话中途不支持热切换。

## 二、协议变更(唯一改动点:hello 报文)

在现有 hello 报文里**新增可选字段 `session_params`**,其余一切不变:

```json
{
  "type": "hello",
  "version": 1,
  "transport": "websocket",
  "audio_params": {"format": "opus", "sample_rate": 16000, "channels": 1, "frame_duration": 60},
  "session_params": {"language": "ja", "voice": "Kore"}
}
```

- `language`、`voice` 均可选,只传其一也行
- 不带 `session_params` 的旧固件行为完全不变(已回归验证)

## 三、服务端回显(请以此为准)

服务端 hello 应答会带 `session_params`,回显**实际生效**的值:

```json
{"type": "hello", "...": "...", "session_params": {"language": "ja", "voice": "Kore"}}
```

固件若需展示当前语种/音色,读回显,不要假设请求值一定生效(见第五节回落规则)。

## 四、可用取值(白名单)

**语种**(8 门,注意:zh 不在列——本产品是外语口语练习):

| code | 语种 |
|---|---|
| en | English |
| ja | 日本語 |
| fr | Français |
| de | Deutsch |
| es | Español |
| pt | Português |
| fi | Suomi |
| ar | العربية |

**音色**(6 个,code 区分大小写):

| code | 人设名 |
|---|---|
| Puck | 小阳(默认) |
| Aoede | 阿依 |
| Leda | 小满 |
| Kore | 可蕊 |
| Charon | 夏老师 |
| Zephyr | 泽风 |

## 五、非法值处理(不拒连,静默回落)

| 情况 | 服务端行为 |
|---|---|
| language 不在白名单(如 "zh"、拼错) | 回落账户默认语种,连接照常建立 |
| voice 不在白名单 | 回落默认音色 Puck |
| session_params 形状非法(非对象等) | 整体忽略,等同没传 |
| 账户配置本身异常/配额不足 | 仍然拒连(close),与本功能无关 |

判断实际生效值的唯一可靠方式 = 读 hello 回显。

## 六、联调环境

- **首尔测试**(已上线,可立即联调):`wss://api.aioneschool.com/api/partner/v1/xiaozhi/ws`
- **贵州生产**:`wss://api.cn.aioneschool.com/api/partner/v1/xiaozhi/ws`(联调通过后部署,另行通知)
- 鉴权与现状一致:`Authorization: Bearer <deviceToken>` + `Device-Id` 头,无任何变化

## 七、已验证用例(首尔真机,2026-09-08)

| 用例 | 结果 |
|---|---|
| 不带 session_params(模拟旧固件) | 回显 en/Puck(账户默认),行为零变化 ✅ |
| 指定 ja + Kore | 回显 ja/Kore,会话按日语+可蕊进行 ✅ |
| 指定非法值(zh + BadVoice) | 回落 en/Puck,连接正常,服务端留告警日志 ✅ |

联调中遇到预期外行为,提供连接时间点(精确到分钟)+ Device-Id,我们可即时查服务端日志定位。
