# 插件开发

每个插件存放在 `plugins/<id>/`，入口为 `plugin.yaml`。当前清单采用 JSON 语法（JSON 是 YAML 的合法子集），因此运行时不需要额外 YAML 解析依赖。

必填字段：`id`、`name`、`version`、`api_version`、`category`、`requires`、`permissions`、`templates`。

插件不得直接访问核心 SQLite 表；业务数据通过 OpenAPI、设备事件和 Webhook 流动。`risk=health-sensitive` 或 `minor-data` 的插件必须提供免责声明并进入更严格的审核流程。
