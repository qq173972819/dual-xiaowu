# 十分钟接入

1. 启动 API：`python -m uvicorn xiaowu_platform.main:app --port 8100`。
2. 运行 `tools/s3_simulator.py` 注册模拟 S3，保存返回的设备令牌。
3. PC 运营后台使用管理员密钥创建 Developer App，保存只显示一次的应用 Token。
4. 使用 `Authorization: Bearer <token>` 调用内容接口。

```http
POST /openapi/v1/devices/SIM-S3-001/content
Authorization: Bearer dev-app-key
Content-Type: application/json

{"template":"notice-card","screen":"A","data":{"title":"Hello","content":"First card"}}
```

设备模拟器取到任务并确认后，可在运营后台查看任务状态。无需真实硬件即可完成这条链路。
