# OpenAPI v1

第三方应用使用 `Authorization: Bearer <app-token>`。应用 Token 由 PC 运营后台创建，作用域包括：

- `device.content.write`
- `device.read`
- `device.event.read`
- `webhook.manage`

应用 Token 与设备 Token、用户会话严格分离。内容推送必须引用已启用模板并通过模板必填字段校验。

Webhook 当前写入可靠 Outbox；订阅地址必须使用 HTTPS。投递 Worker、重试和 HMAC 请求头属于下一迭代，Outbox 可由运营后台审计。
