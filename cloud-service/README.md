# 影序云端服务

## 当前云端进度（2026-10-27）

`yingxu-dev` 数据库已逐项应用并回读确认 PostgreSQL schema v11–v16。最新函数源码要求 schema v16；云函数尚未部署。用户确认首期先做邮箱 + 微信登录，短信延期：当前个人认证账号没有可用大陆企业主体，不能办理腾讯云国内短信个人自用资质。CloudBase 邮箱代发、微信开放平台网站应用、client_id 映射、服务端 Secrets、HTTPS CORS 和正式函数运行配置均待完成。桌面端当前仍使用 mock auth，CloudBase SDK 登录及微信回调/账号绑定尚未接线。迁移和本地候选包证据见 `evidence/cloudbase-v16-migration-20261027.md`。历史章节中的旧版本/线上状态是当时记录，不覆盖本节当前状态。

FastAPI 服务的目录所有权属于云端 Agent 开发。共享契约位于仓库根目录 `contracts/`，开发和生产部署使用同一组 v1 schema。

## 启动

创建 Python 3.11 环境并安装锁定依赖（与 CloudBase HTTP 云函数运行时一致）：

```sh
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.lock
cp .env.example .env
```

设置 `APP_ENV=development`、`AUTH_MODE=dev` 和本地临时 `DEV_AUTH_TOKEN` 后，用 `uvicorn app.main:app --host 127.0.0.1 --port 8080` 启动。默认 `AUTH_MODE=disabled`，业务 API 不接受请求，`/readyz` 返回未就绪。

## 生产配置

生产可以用 CloudBase 官方 PostgreSQL REST：不设 `DATABASE_URL`，将 `CLOUDBASE_ENV_ID` 和服务端 Secret `CLOUDBASE_APIKEY` 注入函数环境。还需 `AUTH_MODE=cloudbase`、可信 `JWT_ISSUER`/`JWT_AUDIENCE`、客户端映射 `CLOUDBASE_CLIENT_ORIGINS`、会话签名 Secrets (`SESSION_PRIVATE_KEY`/`SESSION_KEY_ID`) 和明确的 HTTPS `CORS_ORIGINS`。CloudBase access token 由后端经官方 user/me 与 token/introspect 双校验，再换发影序短会话。缺少身份配置时业务失败关闭。

生产不自动建表。`yingxu-dev` 的 PostgreSQL v1–v16 已于 2026-10-27 应用并验证；函数候选仍未部署。此前 `0.2.0-store-preview` 的健康状态仅是历史快照，不代表当前函数版本或正式认证已就绪。

CloudBase HTTP 云函数需要 `scf_bootstrap` 启动脚本并监听 9000 端口。依赖在云端安装；生产函数使用 Python 3.11，支持 HTTP 与 WebSocket。

CloudBase 部署和账号条件见 [`infra/cloud/README.md`](../infra/cloud/README.md)。


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

## 用户授权Ollama真实本地模型验证（0.4.1候选）

检查现有Ollama服务和本机模型，复用已安装qwen2.5:7b，无新模型下载，不重启服务、不删除其他模型。新增仅APP_ENV=development可用的provider=ollama适配；固定127.0.0.1:11434/v1/chat/completions，显式禁用系统HTTP代理、不发送APIKey、不允许云模型标签，不作为生产或云端异常的自动fallback。DashScope路径保留独立BYOK验证。官方兼容依据：https://docs.ollama.com/api/openai-compatibility 。

真实本地证据（不是模拟provider响应）：首次LangGraph模型回答耗时1.34秒，返回prompt92/completion14/total106 token；同轮次重复200 duplicate且实测真实模型调用仍1次。另一隔离任务事件的实际模型回答约4.65秒，正确返回running匹配摘要并说明数据可能不完整，executionAuthorized=false；无原片、GPS、用户资料、CloudBase或付费模型调用。证据ollama-langgraph-real-local-20261026.json与ollama-langgraph-grounded-job-20261026.json。

新增可重复执行的显式opt-in测试test_ollama_integration.py，临时SQLite、运行时随机开发令牌、现有本地模型，校验真实回答/usage/任务依据/重复请求只调用1次；普通测试不自动调用模型。命令：YINGXU_RUN_OLLAMA_TESTS=1 python -m unittest discover -s cloud-service/tests -p test_ollama_integration.py -v。此测试已通过。生产拒绝Ollama配置、无认证头、禁代理与云标签拒绝单元检查通过；PG回归69项59通过10云HTTP跳过（新增opt-in测试之前），未调用CloudBase。

本地0.4.1-ollama-preview包已构建并逐文件回读：2727清单文件/60依赖，buildId0f6bf83c2b184aeb0018b4b301e68b3c645ec2e399e60b2dd0acf801bd34606d，ZIP34234875字节/SHA256eb790deff28a5fb98ccbf21c43d5135275fb82d30d43f69b28732c31b6fbf0d3。项目外600候选配置、旧包保留；未部署，线上仍0.2.0/schema10。LangGraph专用checkpoint、模型自主工具选择和正式身份/远端模型验收继续，不能用本地Ollama通过代替云端供应商接通。已让全栈R9后续接真实本地Ollama逐次授权交互，仍不自动推送。


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

## 0.9.0 本地同步与桌面 Agent 交付候选（仅本地）

云函数上下文按当前源码重新生成并匹配校验，版本 `0.9.0-local-sync-preview`、要求 schema 14，2738 个上下文文件/60 个 Python 3.11 manylinux 依赖；ZIP `infra/cloud/function-packages/0.9.0-local-sync-preview-d1256bcf1307.zip` 共 2739 条目、34,273,203 bytes，SHA-256 `db18553bb2297ce1fe07755f4d5d6cdc5395a28d9e1a3968533d02d0c6d22753`。ZIP 每个清单文件哈希回读通过，凭据检查只使用合成的非秘密测试配置。证据：`cloud-service/evidence/local-function-package-0.9.0-local-sync-preview-20261026.json`。仅为本地候选，未上传/部署；云仍 schema10/app0.2.0，任何后续部署前须先清理指定的合成 owner、迁移并云端验证。

Agent runtime 随 macOS arm64 桌面 DMG 一起交付，更新 UI 的 DMG SHA-256 `4e14db53325f0c9f5838b4f86a3e063eb81d15cbaf30dc7eaa95f13a13ba6bea`；Ollama 与模型权重不在安装包内。宿主 UI 的实际打包 E2E 仍由桌面宿主会话验证中，包未公证。

协调修正：全栈会话在R11准备中转入存储卡只读复核，当前等待卡片挂载；R11尚未完成，不再标记运行中，不覆盖其当前任务或重复派发前端任务。云端会话继续独立实现。
