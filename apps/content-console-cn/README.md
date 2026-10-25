# 森穹小悟双屏场景后台

这是一个与现有 `webapp/` 并列的独立后台，原后台不会被修改。它复用 小悟AI 的账号、设备绑定、成员共享和后端 API，并为双屏设备提供新的场景式配置界面。

## 本地启动

> ⚠️ 本目录是「森穹小悟 S3 开放平台」内置的双屏内容后台，**不要**再用旧仓库的 `../backend`(8080) 或 `../dual`(8094)。旧栈与本项目无关，连了也是连错后端。

先启动本工程后端（终端 1，仓库根目录）：

```powershell
cd E:\electron\screen\XiaoWu-main\XiaoWu-main\xiaowu-s3-open-platform
.\run_backend.bat
# 等价命令（注意必须 0.0.0.0，127.0.0.1:8104 在本机被占用会绑不上）：
# .\.venv\Scripts\python.exe -m uvicorn INKSIGHT_platform.main:app --host 0.0.0.0 --port 8104 --reload
```

再启动本控制台（终端 2）：

```powershell
cd E:\electron\screen\XiaoWu-main\XiaoWu-main\xiaowu-s3-open-platform\apps\content-console-cn
npm run dev
```

浏览器打开 `http://localhost:8094`。代理目标由 `apps/content-console-cn/.env.local` 的
`INKSIGHT_BACKEND_API_BASE`（默认 `http://127.0.0.1:8104`）决定，已与后端端口对齐。

如果注册或登录显示 500，先确认后端(8104)仍在运行。正常的未登录健康检查应返回 HTTP 401，而不是 500。

> 注意：此控制台通过「应用到设备」把双屏配置 POST 到 `/api/device/<mac>/dual-config`，
> 后端写入设备内容配置并置位 `pending_refresh`，固件下次取图（≤1 分钟）即生效。

## 当前能力

- 复用 `ink_token` 登录态和 小悟AI 登录接口
- 读取现有设备列表
- 配对码绑定、MAC 绑定和设备解绑
- 查看与邀请共享成员
- 家庭、学习、理财、办公、自定义五套双屏场景
- A 屏/FPC1 与 B 屏/FPC2 独立配置和预览
- 双屏配置草稿独立存储，不写入旧单屏配置

## 后续接口

场景配置目前保存在浏览器 `INKSIGHT_dual_config` 草稿中。正式设备同步需要后端增加双屏配置表和按 `screen=A|B` 区分的渲染、刷新状态接口。
