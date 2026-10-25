# 连接器开发（07）

连接器（Connector）是平台面向第三方服务的**用户级授权层**，与插件（`plugins/`，设备内容能力）分工明确：

| | 插件 Plugin | 连接器 Connector |
|---|---|---|
| 解决什么 | 设备屏幕上显示什么内容 | 平台如何代表用户访问第三方服务 |
| 授权对象 | 开发者 App（Bearer Token） | 终端用户（OAuth 授权，凭据入保险库） |
| 清单位置 | `plugins/<id>/plugin.yaml` | `connectors/<id>/connector.yaml` |

## 两类连接器

1. **平台级（type=platform）**：平台持有密钥（如 iFinD / Wind / 东方财富等持牌数据 API 的企业授权），所有用户**零绑定**直接可用。绑定页展示「无需绑定，已开通」。
   - 适用：行情数据、公共资讯等平台统一采购的能力。
   - 注意：**不要**做"用户输入个人同花顺账号"的账密代登——同花顺无个人开放 API，代登录违反其服务条款且涉及证券数据合规。
2. **用户级（type=user）**：用户授权一次，凭据加密保存，绑定页展示「去授权 / 已绑定」。
   - 仅接受 OAuth 2.0 授权码模式；凭据保存在服务端保险库，永不下发到设备与前端。

## connector.yaml 规范

入口 `connectors/<id>/connector.yaml`（JSON 语法，与 plugin.yaml 同约定）。必填：`id`、`name`、`version`、`type`（platform/user）、`description`。用户级还须有 `auth`：

```json
{
  "id": "some-service",
  "name": "某某服务",
  "version": "0.1.0",
  "type": "user",
  "category": "general",
  "description": "…",
  "auth": {
    "kind": "oauth2",
    "authorize_url": "https://third-party/oauth/authorize",
    "token_url": "https://third-party/oauth/token",
    "client_id": "xxx",
    "client_secret_env": "INKSIGHT_SOME_SERVICE_SECRET",
    "scopes": ["read"]
  },
  "guide": ["第一步…", "第二步…"],
  "permissions": ["connector.some.read"],
  "risk": "low",
  "disclaimer": "…"
}
```

约定：`client_secret` 一律走 `client_secret_env` 环境变量，不落清单文件；相对路径（`/api/v1/...`）的 authorize/token 端点会自动补全为本平台地址（开发模式内置 mock 授权服务器，见下）。

## 数据模型（store.py）

```sql
user_connectors(
  user_id, connector_id,            -- 联合主键：一个用户一个连接器一条绑定
  credential_encrypted,             -- Fernet 密文（完整 token 响应 JSON）
  external_account,                 -- 第三方账号展示名（不含敏感信息）
  scopes, status, expires_at, updated_at
)
connector_auth_states(state, user_id, connector_id, created_at, expires_at)  -- OAuth state，一次性、10 分钟
```

## 凭据保险库（connectors.py）

- `CredentialVault`：Fernet 对称加密（SHA-256 派生密钥的单层信封加密）。主密钥来自环境变量 `INKSIGHT_CONNECTOR_MASTER_KEY`；开发模式缺省从 `admin_key` 派生，**生产必须显式设置**。
- 加密的是第三方 token 响应的完整 JSON；解密只在服务端运行时发生，任何 API 不回传明文。
- 轮换主密钥 = 全量重加密脚本（待补）；解密失败会明确报"主密钥不一致"。

## API（connector_api.py）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/v1/mobile/connectors` | 绑定页列表：按用户合并绑定状态（included / bound / unbound） |
| POST | `/api/v1/mobile/connectors/{id}/authorize` | 生成 state，返回第三方授权页 URL |
| GET | `/api/v1/connectors/{id}/callback` | OAuth 回调：校验 state → 换 token → 加密入库 → 返回结果页 |
| POST | `/api/v1/mobile/connectors/{id}/unbind` | 解绑并立即删除凭据 |
| GET/POST | `/api/v1/connectors/{id}/mock-authorize`、`mock-token` | 仅 dev_mode：内置 mock 授权服务器，离线跑通全流程 |

审计动作：`connector.authorize` / `connector.bind` / `connector.unbind` 写入 `audit_logs`。

## 绑定流程（用户视角）

1. 小程序首页 → 「🔗 连接器」→ 绑定页列表。
2. 平台级卡片显示「无需绑定，已开通」；用户级未绑定显示「去授权」+ 授权指引步骤。
3. 点「去授权」→ 底部弹层引导：复制授权链接 → 浏览器打开确认授权 → 返回下拉刷新变「已绑定」。
4. 「解除绑定」二次确认后凭据立即删除。

## 接入真实第三方（以行情数据为例）

1. 平台企业侧采购数据 API，密钥放环境变量，新建 `type=platform` 连接器 → 用户零绑定。
2. 如需用户级授权，确认对方提供标准 OAuth2 授权码端点，按上文规范填 `auth`，secret 写入生产环境变量。
3. 运行时消费凭据：业务代码通过 `store.get_connector_binding()` 取密文 → `credential_vault.decrypt()` 在服务端解密使用（参考 `connectors.py` 模块注释）。

## 安全基线

- state 一次性、10 分钟过期，绑定回调必须先验 state。
- 凭据 Fernet 加密落库；主密钥只在服务端环境变量。
- 凭据永不下发终端/前端；解绑即删除。
- `risk=finance` 等敏感类别需免责声明并进入更严格审核（与插件审核对齐）。
