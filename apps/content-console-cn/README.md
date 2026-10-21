# 森穹小悟双屏场景后台

这是一个与现有 `webapp/` 并列的独立后台，原后台不会被修改。它复用 小悟AI 的账号、设备绑定、成员共享和后端 API，并为双屏设备提供新的场景式配置界面。

## 本地启动

先启动 小悟AI 后端（终端 1）：

```powershell
cd E:\electron\screen\inksight-main\inksight-main\backend
.\.venv\Scripts\python.exe -m uvicorn api.index:app --host 0.0.0.0 --port 8080
```

再启动双屏后台（终端 2）：

```powershell
cd E:\electron\screen\inksight-main\inksight-main\dual
npm run dev
```

浏览器打开 `http://localhost:8094`。默认代理到 `http://127.0.0.1:8080`，可通过 `INKSIGHT_BACKEND_API_BASE` 修改。

如果注册或登录显示 500，先确认第一个终端中的 FastAPI 后端仍在运行。正常的未登录健康检查应返回 HTTP 401，而不是 500。

## 当前能力

- 复用 `ink_token` 登录态和 小悟AI 登录接口
- 读取现有设备列表
- 配对码绑定、MAC 绑定和设备解绑
- 查看与邀请共享成员
- 家庭、学习、理财、办公、自定义五套双屏场景
- A 屏/FPC1 与 B 屏/FPC2 独立配置和预览
- 双屏配置草稿独立存储，不写入旧单屏配置

## 后续接口

场景配置目前保存在浏览器 `xiaowu_dual_config` 草稿中。正式设备同步需要后端增加双屏配置表和按 `screen=A|B` 区分的渲染、刷新状态接口。
