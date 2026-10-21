# 贡献指南

欢迎贡献后台核心、设备协议、SDK、模拟器、内容模板和行业插件。不要提交 PCB、原理图、BOM、Gerber、引脚映射、屏幕波形、生产校准、签名私钥或真实用户数据。

插件 PR 必须包含清单、Schema/模板、测试、权限说明、数据来源与适用范围。健康或教育插件还必须包含风险声明。

提交前运行：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
cd apps\pc-console; npm run build
```
