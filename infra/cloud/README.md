# CloudBase 部署准备

此目录保存云端部署说明和模板，不包含账号凭据、环境 ID 或生产环境变量。

## 当前状态

- 服务源码：`../../cloud-service/`
- `yingxu-dev` 体验环境的 PostgreSQL schema v11–v16 已于 2026-10-27 逐项应用并读回验证；当前服务启动要求 schema v16。
- 当前目标改为 HTTP 云函数：官方支持 FastAPI、Python 3.11 与 WebSocket。体验版的云托管 CPU/内存需另行开启超限按量，因此本项目暂不创建云托管服务。
- 当前登录决策：只使用邮箱验证码与用户名/密码；用户名/密码账号先通过邮箱验证码注册。短信与微信登录均关闭且不属于首期范围。桌面 UI 的 CloudBase SDK 登录/短会话接线目前在 `workbuddy/dev`，尚未合并进此 Codex 云端分支。服务端 Secrets、可信 session issuer/audience、client_id 映射和 HTTPS/Tauri CORS 仍未设置，云函数未部署。
- 当前机器未安装 CloudBase CLI；控制台 CLI 授权申请涵盖多个 CloudBase 资源，尚未授权。数据库迁移仅经 SQL 编辑器完成。不得用开发 token 或 SQLite 作为生产身份/数据层。
- 新候选 ZIP 和详细证据见 [`cloudbase-v16-migration-20261027.md`](../../cloud-service/evidence/cloudbase-v16-migration-20261027.md)。未上传部署，未清理合成测试账号，未调用真实短信、微信、邮箱或云模型。

## 本地启动

从 `cloud-service/` 目录创建虚拟环境、安装锁定的 `requirements.lock`，然后设置 `.env.example` 中的开发配置并运行：

```sh
uvicorn app.main:app --host 127.0.0.1 --port 8080
```

认证默认关闭，`/readyz` 会报告未就绪，所有业务 API 都拒绝匿名调用。只有显式设置 `APP_ENV=development`、`AUTH_MODE=dev` 和本地临时 `DEV_AUTH_TOKEN` 后，才可使用隔离开发身份；该模式不能部署到生产。

## HTTP 云函数部署准备

1. 当前采用官方 PostgREST：将环境 ID 与服务端 API Key 作为函数环境 Secret 注入，`DATABASE_URL` 留空。无需用户转发数据库密码。
2. `yingxu-dev` 的 PostgreSQL v1–v16 已逐项应用并读回验证；服务 readiness 要求 schema v16。不要对线上重复执行旧版 v1–v4 迁移。
3. 认证首期为邮箱验证码和用户名/密码；微信扫码暂停，短信待服务商、审核签名和模板配置。新用户先通过邮箱验证码注册，再设置用户名/密码。桌面 SDK 接入时按 CloudBase Authentication v2 官方流程实现，并将 CloudBase access token 交给服务端 `/v1/auth/session/exchange` 换发影序短会话。
4. 配置正式 CloudBase 身份通道后，再生成服务端 session 签名密钥并以 Secrets 保存：`CLOUDBASE_CLIENT_ORIGINS`、`SESSION_PRIVATE_KEY`、`SESSION_KEY_ID`、`JWT_ISSUER`、`JWT_AUDIENCE`、`CLOUDBASE_ENV_ID`、`CLOUDBASE_APIKEY` 和 HTTPS `CORS_ORIGINS`。凭据不得进入仓库、客户端或聊天。
5. 使用 `python3 infra/cloud/prepare_function_context.py` 生成 Linux/Python 3.11 函数上下文，并校验源文件、锁定依赖和候选包哈希。
6. 只有身份渠道和 Secrets 完成后，才在体验环境部署已验证的 schema16 候选。部署后记录函数版本/路径和访问策略；验证 `/healthz`、`/readyz`、账号交换、PostgreSQL 读写、WebSocket 与回滚。

用户逐项配置路径与非敏感待办见 [`console-configuration-checklist.md`](console-configuration-checklist.md)。

## 当前本地部署候选（不代表已部署）

旧候选 0.9.0/schema14 的描述为历史记录，不再作为当前部署候选。当前 schema16 候选的包哈希与验证边界见上面的证据文件。待认证和服务端密钥完成后，再从已验证上下文部署，切勿将密钥写入代码、仓库或聊天。

早期部署证据曾记录 0.2.0-store-preview 的 health/database 正常但 authentication 未配置；本轮未重新查询现存函数版本，故不把该旧记录视为当前运行状态。下一次部署前应在控制台读回当前版本和访问策略。使用项目外的私有配置文件注入 Secret；不要把含 Secret 的 cloudbaserc 放入上传目录或输出日志。

## 官方文档

- [CloudBase FastAPI HTTP 云函数](https://docs.cloudbase.net/en/cloud-function/frameworks-examples/fastapi)
- [CloudBase 云函数 CLI 部署](https://docs.cloudbase.net/cli-v1/functions/deploy)
- [CloudBase PostgreSQL 连接](https://docs.cloudbase.net/database/postgresql/connecting-to-postgresql)
- [CloudBase 邮箱验证码登录](https://docs.cloudbase.net/authentication-v2/method/email-login)
- [CloudBase 短信验证码登录](https://docs.cloudbase.net/authentication-v2/method/sms-login)
- [CloudBase 用户名密码登录](https://docs.cloudbase.net/authentication-v2/method/username-login)
- [CloudBase 用户注册](https://docs.cloudbase.net/authentication-v2/method/signup)


## 本地续跑：路径脱敏后的幂等重试

SQLStore 新增不可变 SHA-256 请求指纹，关闭路径同步后仅清理读取投影。相同原始重试返回 duplicate；改变路径仍冲突；重新开启同步不会恢复已清理路径。

v6 PostgreSQL 迁移为未执行草案，依赖 v5；SQL 使用 JSONB 文本规范化，与 Python 规范化方式不同，不跨后端复制指纹。历史已脱敏原文无法恢复，完整路径重放继续保守冲突。生产 readiness 要求 v6。已有数据库必须迁移。

本轮 unittest 15 项：9 项本地通过，6 项云端主动跳过。未重试 REST 网关，未执行 v6，未部署新应用。线上仍为 0.2.0 预发布。旧客户端缓存失效 / generation 通知仍待实现，整体目标未完成。

## Agent 会话持久化（本地实现，尚未部署）

POST /v1/agent/turns 可选成对提供 sessionId / turnId（各 1–160 字符），不提供时保持原无历史写入行为。轮次按 owner/session/turn 唯一；相同设备与 query 重试返回首次保存的回答，不同内容返回 TURN_ID_CONFLICT（409）。GET /v1/agent/sessions/{session_id}/turns 提供每页 100 条和 after_id 游标，严格按当前账号隔离。

SQLStore 与 PostgRESTStore 提供相同接口；v7-agent-turns 迁移草案依赖 v6，包含 RLS、撤销客户端权限、service_role-only 写 RPC，复用账号锁协调偏好变化与会话写入。syncAgentSessions=false 时仍回答但 persisted=false，不写入新历史；关闭开关不会删除既有历史。会话 query 是用户原文，syncPaths 开关只控制任务路径字段，不对聊天正文做自动语义脱敏，客户端必须明确展示会话同步开关。

生产 readiness 现在要求 v7。v6/v7 均未执行，v5 新应用仍未部署；不能将当前工作树直接覆盖线上 0.2.0。新增合约与 API 验证覆盖重试、内容冲突、分页、账号隔离、禁用写入和撤销设备；19 项总计 12 项本地通过、7 项云端跳过。尚未连接模型，不宣称模型会话或完整 Agent 已完成。后续仍需云端迁移/RPC 验证、正式身份、BYOK、缓存失效和桌面接线。

## 按框架推进：会话删除与防重放恢复

新增 DELETE /v1/agent/sessions/{session_id}，必须发送 {"confirmed":true}。在账号锁下删除当前 owner 的会话轮次并记录不含聊天正文的会话删除标记。重复删除幂等；旧 turn 或新 turn 重用已删除 sessionId 均返回 410 SESSION_DELETED；新建不同 sessionId 可正常保存。此操作仅针对云端 Agent 历史，不触碰本机素材，也不等于账号注销。

SQLStore / PostgRESTStore 同语义；v8-session-deletion 迁移草案依赖 v7，生产 readiness 要求 v8。v6/v7/v8 尚未执行；未部署，未重试受阻 CloudBase 网关。21 项检查：13 项本地通过、8 项云端跳过，覆盖确认前不删除、删除后无历史、重放不恢复、重复删除与跨账号隔离。旧客户端已缓存内容仍需后续删除通知 / generation 机制，不能声称完成跨端隐私删除闭环。

协调：已向全栈开发派发 R2 原生宿主联调及真实云端适配准备，范围保持 desktop-ui/ 与桌面联调记录；WorkBuddy 后端只读。本会话继续 cloud-service / infra/cloud。

## 路径同步关闭后的缓存失效通知（本地）

新增账号隔离 GET /v1/sync/invalidations，按独立 after_id 返回 paths_redacted 通知（每页100条）。关闭路径同步在同一账号锁事务中清理旧投影并发布通知；重复保持关闭且无旧路径时不重复发布，重新开启再关闭会发新通知。新账号首次明确关闭也发布通知。通知不包含路径。v9 迁移草案依赖 v8，production readiness 要求9；未执行迁移、未部署、未重试受阻REST网关。

客户端应按账号独立维护任务事件、路径失效、会话删除三个游标，收到 paths_redacted 后清除已有缓存source/paths。快照拉取与偏好变化可并发，客户端写缓存后应再拉失效通知，并持续轮询，不能仅登录时检查一次；本地清理操作和通知游标推进必须事务提交。当前未声称一致性快照/原生客户端消费或跨端隐私验收完成。23项检查中14项本地通过9项云端跳过。

已读取全栈R2结果；旧宿主包阻塞可通过独立target构建新本地调试宿主继续，已授权全栈R3不改backend源码完成重建及原生联调，无需等待用户手工处理。

## PostgreSQL 实测及真实云端 schema v9（修正前述草案状态）

本轮安装 Homebrew PostgreSQL 17.11_1（依赖由 Homebrew 更新），未启用开机服务。验证器 `infra/cloud/validate_local_postgres.py` 创建仅 Unix socket 的独立 /tmp/yingxu-pg-* 数据库，应用 v1–v9 真实迁移，检查客户端表/RPC权限，运行 SQLStore 与真实 PL/pgSQL RPC 合约；RPC 写入测试切换为 service_role。通过 pg_locks 亲见账号锁阻塞，验证跨事务发布顺序；新增直连/RPC 双向重试与脱敏后指纹兼容检查。总计45项：36项本地通过、9项CloudBase HTTP合约主动跳过。所有测试数据库均已停止，保留日志与数据供复查；未使用Homebrew默认数据库。

修复生产兼容问题：SQLStore 在 PostgreSQL 下改为与RPC和迁移相同的 JSONB 文本 SHA256 规范化；SQLite仍采用Python规范化，不跨SQLite和PG复制指纹。前述“不同Store指纹不互通”描述对同一PG数据库已被本修复取代。

管理CLI首次读取提示无有效登录，现有Safari授权页随后显示成功，原请求继续读回版本1–5；未另建替代任务或重复迁移。通过已恢复的CLI管理API，在同一个事务应用v6–v9，RequestId=159ad619-f2f0-4d00-ab08-b74f9732b2cd。随后真实云数据库合成RPC检查通过并ROLLBACK：路径脱敏重试、会话去重、同步关闭、删除及防恢复，回读latest_version=9、remaining_synthetic_devices=0、anon历史访问=false、authenticated写RPC权限=false。RequestId=c707323d-1aac-4fe3-b1bb-b6547609a104。序列号可因回滚测试前进，不重置。

证据：cloud-service/evidence/postgresql17-migrations-contracts-20261026.log、cloudbase-schema-v6-v9-20261026.log、cloudbase-schema-v9-rpc-checks-20261026.log。管理SQL成功不能当作REST网关成功；本轮未调用已受限网关、未部署新应用。线上应用仍是此前0.2.0预发布，本地应用要求schema9。CloudBase HTTP合约、正式身份、BYOK和最终客户端联调继续，整体目标未完成。

全栈R3已报告原生目录选择到报告闭环通过；协调已派R4客户端通知消费、账号隔离与persisted=false合法行为修复，后端源码边界不变。

## 显式 BYOK 路由及持久化请求预留（应用未部署）

POST /v1/agent/turns 新增 useModel（默认false）；只有明确true才允许外部文本模型调用，要求sessionId/turnId及受控server Secret MODEL_OWNER_CREDENTIALS中匹配当前owner的凭据。GET /v1/agent/model仅返回配置状态/模型/地域及逐次授权要求，不返回key。新模型路径只解释白名单任务摘要，不接文件/照片，也不产生执行权限。真实BYOK凭据未配置、未发真实供应商请求。

新增 agent_model_requests 与 reserve/finish RPC：在网络调用前提交唯一轮次预留；并发仅一方reserved，其他pending；成功重放返回原回答；网络超时等结果不确定时标uncertain，禁止同轮次自动重发。进程在预留后崩溃可能持续pending，不自动过期重领，防重复计费；需要后续人工/供应商证据核对，不能承诺供应商恰好一次收费。历史同步关闭时只留hash/状态元数据，不保存query/response正文，首次仍正常回答；后续同轮次completed_unretained拒绝自动重发。会话删除后迟到结果不能恢复历史。

核验：56项检查中46项本地通过、10项CloudBase HTTP跳过，含真实PG17.11、service_role RPC、并发预留、删除期间迟到结果、显式授权API、模拟provider调用计数及不确定错误。修复SQLAlchemy已缓存设备对象可能不刷新的问题，涉及权限判断的设备查询增加populate_existing，写路径采用共享锁读取。

真实CloudBase管理SQL已应用v10（RequestId972c9402-09bd-40d4-ad1e-3f8649e9af10）；合成reserve/pending/finish/重放/关闭历史/uncertain RPC验证并ROLLBACK成功（RequestId70ede233-54cb-43bf-91be-904f2b78d8e7），回读latest_version10、remaining_requests0、client_access false。应用仍0.2.0预发布；HTTP网关未重试，新应用未部署。管理RPC成功不是供应商接通或生产身份验收。

全栈R5报告149次真实本地HTTP请求、15项检查及16项缓存回归通过，已派R6模型状态与逐次授权/不确定错误的客户端适配。整体目标继续，正式身份/实际模型凭据/HTTP云验收及生产部署仍待完成。

## LangGraph 明确纳入完整目标（本地0.4候选）

用户明确更正并要求采用LangGraph，完整目标增加LangGraph编排、工具适配和实际云模型API接入，不能用关键词查询或仅有文本传输模块替代。已固定langgraph1.2.17，并沿官方StateGraph/START/END/条件边构建实际运行图；POST agent/turns已走图，而非仅声明依赖。图主链：owner_scoped_tool → reserved_model_call 或 persist_readonly_turn → END。

只读工具注册表已实现search_backup_jobs/get_backup_job，新增工具目录与POST工具调用接口，强制绑定服务端principal.owner，不接受owner注入；未知/云端写工具403，非法参数422。结果明确最近200事件窗口，get_backup_job未找到只称NOT_IN_SEARCH_WINDOW，不能断言任务不存在。文件改名/移动/上传仍仅既有提议，不由云图直接执行。

Graph不自动重试，模型仍逐次useModel授权、owner Secret和SQL持久化请求预留。凭据不进入图状态；即使外部LANGSMITH_TRACING=true也在执行上下文禁用追踪，已有回归验证。当前没有Graph专用持久checkpoint或模型自主工具选择，不能把SQL会话保存说成LangGraph恢复机制已实现；这些仍保留后续任务。官方依据：https://docs.langchain.com/oss/python/langgraph/graph-api 。

本地PG17/Python3.11全套68项：58本地通过，10云HTTP主动跳过；新增图分流/模型节点单次调用/禁追踪/工具所有权与API路由检查通过。更新Linux与通用锁，0.4.0-langgraph-preview候选已构建：2727清单文件、60依赖，源/配置/凭据扫描通过。buildId258d02d1cb11fec7ef43c4f3d1fb1042ef09352fd7ecef22ac58310231026aa3。

生成本地ZIP并逐文件回读：0.4.0-langgraph-preview-258d02d1cb11.zip，34,234,547字节，2728文件（含manifest），SHA25681c1f3a737383c613aac6435528218d738dd899a59620207f0f4e5c2c1bf93b4；bootstrap执行位保留。项目外600权限候选配置准备，原配置及旧包保留。未调用CloudBase、未部署；线上仍0.2.0，真实schema10。

R8后端视频重复分析修复已由全栈报告：5视频单测、clippy通过，三次原生分析均3帧，来源字节/mtime不变；仅确认Mac本地调试验收，不代表旧发行包已更新。已派R9客户端真实本地LangGraph工具API联调，后端恢复只读。


## 0.5.0 持久检查点与 HTTP 显式恢复（仅本地）

POST `/v1/agent/turns` 新增 `resume` 布尔字段，默认 false。显式恢复需保持 sessionId、turnId、query、useModel 一致；thread ID 由服务端绑定身份后派生，客户端不可指定。仅稳定 session/turn 且开启会话同步才保存检查点。完成态仍检查业务 ledger；pending/uncertain 不得自动重新调用供应商。缺检查点 409 CHECKPOINT_NOT_FOUND，关闭同步 409 CHECKPOINT_SYNC_DISABLED，缺稳定标识 422 RESUME_REQUIRES_SESSION_AND_TURN；删除会话 410、撤销设备 403。每个图节点独立 Store，释放请求读事务，单连接池测试无死锁且归还连接。

Python3.11/PostgreSQL17.11 隔离实例：85 tests，74 passed，11 skipped；v11 重复迁移和 anon/authenticated 表/RPC拒绝检查通过。检查点关闭同步同事务清理，重新开启不复活。证据：checkpoint-http-postgresql-regression-20261026.log。HTTP 恢复 API 六项采用本地 SQLite，真实 PG saver/RPC另测，不声称真实云HTTP恢复通过。

本地ZIP 0.5.0-checkpoint-preview-dd0498f03d89.zip：34,239,390字节，2729个归档条目，SHA256 90786c2fbfdbfcbc6a4e8350ff287ac9588b0f250a60ee2a55a2b43075802fe2。源码匹配、凭据扫描、逐文件ZIP回读通过。required schema=11，云端仍schema10/应用0.2.0；未应用v11、未部署、未推送项目源码。正式身份、云模型和模型自主工具选择继续待完成。


## 0.6.0 模型选择只读工具（仅本地）

useModel=true 现在进入实际 LangGraph model→tools→model 条件循环，无新请求字段。允许 search_backup_jobs/get_backup_job，工具回调绑定已认证 owner 并逐次校验设备；未知/写工具、owner 参数注入、重复 JSON 键均拒绝，结果不确定后沿原 ledger 禁止自动重发。最多3次模型请求，每次响应最多2个工具（前两轮合计最多4次工具），累计最多10条摘要；仅回传 jobId/name/state/occurredAt，路径等字段不得进入供应商消息。到预算返回明确 bounded 结果，网络/解析/工具异常按 uncertain 处理。内层模型循环不保存独立中途 checkpoint；外层持久预留会在崩溃后阻止整个模型步骤自动重发，未承诺供应商 exactly-once。

证据：model-tools-postgresql-regression-20261026.log：93 tests，82 passed、11 skipped；新增6项工具循环和2项HTTP调用闭环测试。真实 Ollama qwen2.5:7b 测试1项4.275秒通过，明确断言至少2次真实provider HTTP以及至少一次模型所选工具，重复和显式resume没有增加provider请求数；见ollama-actual-model-tools-20261026.log。没有下载模型/重启Ollama/调用CloudBase或付费供应商。

本地ZIP 0.6.0-readonly-tools-preview-5dd78ded1f3e.zip，34,241,919字节，2729条目；SHA256 fa4af0c2d9f6fbcff40c56d9858a9b5ef4e68b9490cdddc13511611689c2c5de。源码、凭据扫描与ZIP逐文件回读通过，schema11仅本地、未部署。正式身份/真实云模型/线上全链仍未闭合。已通知桌面R10授权文案说明最多3次模型请求。

格式参考：[Ollama工具调用](https://docs.ollama.com/capabilities/tool-calling)、[LangGraph工作流与Agent](https://docs.langchain.com/oss/python/langgraph/workflows-agents)。本实现通过现有OpenAI兼容端点传递tool_call_id，不使用Ollama原生/api/chat消息格式。


## 0.7.0 账号令牌与服务端设备短会话（仅本地）

新增可选 AUTH_MODE=cloudbase，默认配置仍禁用/原候选配置不启用该模式。POST /v1/auth/session/exchange 请求体仅 displayName，Authorization 为 CloudBase 用户 access_token；固定同环境 user/me 与 token/introspect 双验证 sub、一致 Bearer 类型、服务端注册 client_id→desktop/mobile 映射，再由服务端生成 device ID。请求体 owner/device/origin 不被接受，header 不提供身份依据；不声称硬件认证。返回 RS256 900秒影序 accessToken、tokenType、expiresIn、ownerId、deviceId、origin、deviceAttested:false；私钥及上游token不保存数据库、不返回。auth响应 Cache-Control:no-store。/.well-known/jwks.json 只返回公钥。POST /v1/auth/logout 用影序短JWT撤销当前设备所有token，再次登出幂等；旧token不能重新注册该设备。重新账号交换会生成新device ID。

配置需要已确认 CLOUDBASE_CLIENT_ORIGINS、SESSION_PRIVATE_KEY（RSA≥2048，仅服务端Secrets）、SESSION_KEY_ID、JWT_ISSUER/JWT_AUDIENCE；生产issuer需HTTPS。没有配置则503，不能回退开发认证。现阶段不实现刷新或多key平滑轮换，短期会话到期需重新账号交换；生产登录UI、渠道送达、真实client注册和手机UID兼容仍需联调。旧jwt模式保持。

验证：Python3.11/PostgreSQL17.11 104 tests，93 passed、11 skipped。新增8项账户通道与真实RSA测试、3项真实本地HTTP/SQLite交换与退出测试；CloudBase HTTP为合成响应，未请求网关，不代表正式登录成功。真实签名、issuer/aud/exp、未知client/账号不匹配拒绝、客户端伪造device/header无效、owner隔离、退出403已验证。证据 account-session-postgresql-regression-20261026.log。

本地ZIP 0.7.0-account-session-preview-093b3d54029c.zip，34,245,375字节，2730条目，SHA256 0682dcef5f9986c32f15f790115a37f6f46389bae3fcbf4b913b1a477d11c114。源码匹配、凭据扫描及ZIP逐文件回读通过；schema11仅本地，云仍schema10/应用0.2.0，未部署。R10桌面8项合成回归与构建已收，已派R11短会话客户端和真实本地fixture联调，运行中不重复派发。

官方接口参考：https://docs.cloudbase.net/http-api/auth/auth-token-introspect 。实际供应商返回client_id的语义及真实注册配置仍待云端验收，不能以合成测试代替。


## 0.7.1 短会话公钥轮换（本地）

SESSION_VERIFICATION_JWKS 可配置最多3个旧公钥；新会话只用当前私钥签发。严格接受六字段 RSA/RS256/use=sig JWK，RSA至少2048位，拒绝私钥字段、URL、未知字段、重复kid（包括active kid冲突）及超量keys。cloudbase模式每次按kid明确选key验签，不缓存旧key；JWKS输出active+旧public，移除旧key后该进程立即拒绝旧token。旧jwt/JWKS模式仍有原有300秒set缓存，不把本轮结论扩展到它。

本地实际RSA与HTTP验证：旧签名会话在新active+旧public时仍可用，移除旧public返回401，新会话继续可用。PG隔离全量109 tests，98 passed、11 skipped；证据session-key-rotation-postgresql-regression-20261026.log。

运维顺序：保留旧公钥→配置新私钥/kid与旧public→完成所有实例配置部署→等待旧900秒会话全部到期→移除旧public。配置在应用启动时读取；不声称未重启或多实例未更新时已全局立即撤销。遇密钥失陷需紧急移除对应public并完成实例更新，重新登录。没有创建生产私钥、修改CloudBase或发布。刷新仍未实现；本段替代0.7“无多key轮换”的旧状态。

本地ZIP 0.7.1-session-rotation-preview-512dedd4525d.zip，34,246,015字节，2730条目，SHA256 530e252a9fc9d933d2afaf51ead13bd4cc0c54894624b7f29835cdeeb19379f3。源/凭据/ZIP逐文件回读通过。线上仍0.2.0/schema10，v11仅本地。

协调修正：全栈会话在R11准备中转入存储卡只读复核，当前等待卡片挂载；R11尚未完成，不再标记运行中，不覆盖其当前任务或重复派发前端任务。云端会话继续独立实现。
