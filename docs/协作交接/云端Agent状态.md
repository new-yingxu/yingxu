# 云端与 Agent 状态

更新时间：2026-10-25

最新跨软件协作边界与恢复入口：`云端Agent-跨软件并行交接-2026-10-25.md`。用户要求本轮整理交接后由另一款 Agent 先开发独立身份模块；本会话已关闭 heartbeat。最新本地 Store 重构只通过 AST 语法解析，未部署，恢复后必须先验证。

## 已实现

- 新增 `cloud-service/` FastAPI 初版、Python 3.11 依赖锁 `requirements.lock`、CloudBase HTTP 云函数启动脚本与无凭据部署上下文生成器；旧容器方案仅作备选。
- SQLAlchemy 运行时驱动已改为 Psycopg 3；新增 CloudBase PostgreSQL schema v1 与迁移准备说明。
- 新增 SQLAlchemy 设备、同步事件、Agent 提议、同步偏好数据模型；本地开发可用 SQLite，生产配置拒绝 SQLite。
- 服务复用 `contracts/` 的两个 v1 schema；同步以 owner+eventId 及 owner+deviceId+sequence 双重幂等，冲突拒绝覆盖。
- 账号 owner、deviceId、origin 来自验签 JWT claims；真实身份服务尚未绑定。开发 token 仅在显式 `APP_ENV=development`、`AUTH_MODE=dev` 配置下可用，默认关闭。
- 设备注册/列表/撤销；撤销设备后 REST 和 WebSocket 下一轮检查会拒绝访问。
- 任务摘要同步、owner 限定快照与游标、路径同步开关和关闭时既有路径字段剔除。
- Agent 查询只搜索已同步的任务摘要；写操作仅保存带过期时间的提议/本地回执，不执行桌面写操作。移动端只能调用只读工具。
- WebSocket 首帧验权、初始事件快照、owner 限定增量轮询和 afterCursor 重连已实现；生产 JWT 可在共享身份 claims 对接后启用。
- `GET /healthz`、`GET /readyz`；默认认证关闭时 readiness 显示未就绪。
- OpenAPI 快照：`docs/云端与Agent/openapi.json`；部署步骤与风险：`infra/cloud/README.md`。

## 本地验证

- 早期本地烟测在 SQLite 和显式开发 token 下通过。依赖锁现由 uv 按 CloudBase Python 3.11 目标重新解析；本轮没有重跑此前烟测。
- 临时 SQLite/显式开发 token 烟测通过：健康与就绪、缺少认证失败、设备注册、v1 事件写入/重放/冲突、设备 ID 伪造拒绝、快照、按 owner 查询、Agent 提议及不可越过本地授权状态机；另验证开发态 WebSocket 初始快照与手机只读/禁止桌面写操作。
- `pip check` 曾在修改 Psycopg 前通过；OpenAPI 含 11 个 REST 路由。
- 烟测使用隔离临时数据库与虚构任务摘要，没有连接外部账号、上传原片或调用模型。

## 当前阻塞与未完成

- CloudBase CLI 3.8.9 已获设备码授权。环境 `yingxu-dev-d8gowpye17ee27a61`、`ap-shanghai`、体验版；超限按量关闭。早期查询用量0不代表部署后实时用量。已创建函数 yingxu-agent-api，Active。
- `tcb env detail` 读回 PostgreSQL 共享实例 `postgres-f44f3luq`。部署目标据此调整为 HTTP 云函数，避免必须启用按量付费的云托管。
- 已执行 PostgreSQL v1 基础表、v2 客户端权限撤销/RLS、v3 service_role-only偏好更新RPC。原新表默认客户端权限过宽问题已收紧。服务端 Key 的 REST 读取已验证；最新业务 Store 的读写尚未验证。
- 线上0.1.1-cloud-preview已部署，health200、ready503；无平台令牌401、业务AUTH_NOT_CONFIGURED503；WS首帧auth.required、无效令牌关闭4403。真实用户身份/业务持久化/完整WS同步未验收。线上函数版本1是0.1.0回退点，最新0.1.1尚未另发数字版本。配置进度见 `docs/云端与Agent/环境配置进度.md`。
- MySQL schema 保留为历史初稿；当前生产目标使用 `schema.postgresql.sql`。
- 真实 JWT issuer/audience/JWKS、`sub`/`device_id`/`origin` 签发规则未与 CloudBase 认证或手机端身份对接。短信、邮箱、微信登录/回调、刷新、注销与账号合并未实现。
- 云端数据库账号隔离依赖 JWT issuer 正确绑定设备身份；上线前需加入不同账号隔离、撤销、旧事件防复活、删除/导出和恢复验收。
- Agent 现在是有限只读任务查询和提议记录，不是已接入大模型的完整 Agent；没有媒体目录索引或任意工具执行。模型供应商/BYOK 和用量尚未配置。
- Agent session 持久化、缩略图、完整删除标记/防复活、错误码覆盖、设备重新绑定审批仍未实现。
- 百度/夸克官方个人授权及上传能力尚未核实，未实现。

## 部署准备

- `python3 infra/cloud/prepare_function_context.py` 会生成干净的 `infra/cloud/function-build/`，仅包含服务、契约、Python 3.11 锁依赖与 `scf_bootstrap`；脚本遇到已有目录会停止，不覆盖。
- 函数 `yingxu-agent-api` 已有可启动的受控预发布；当前工作树在适配SQLStore/PostgRESTStore，不等于线上版本。生成包 `function-build/` 暂保留线上0.1.1快照，不直接用未验证源码覆盖。
- 不把 `.env`、数据库连接串、JWT 配置材料或腾讯云令牌放进 Git、容器镜像或聊天。

## 桌面端联调提示

- REST 路由以 `docs/云端与Agent/openapi.json` 为准；已有 v1 schema 未修改。
- 仍需双方确认鉴权 claims、快照/删除游标、Agent action 状态及 WebSocket 消息格式后再由桌面端接入。
- 云端服务没有桌面路径、磁盘权限或源素材；云端提议不得替代本地预检/用户确认。
