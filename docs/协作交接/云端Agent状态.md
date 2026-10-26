# 云端与 Agent 状态

更新时间：2026-10-26

用户已取消临时跨软件身份模块交接。WorkBuddy实际任务待其交接报告确认，不再按旧分工文档假定其负责桌面集成；本会话继续云端与Agent开发；使用独立云端工作区避免覆盖WorkBuddy分支。Store基础合同已通过本地和真实CloudBase检查；最新Agent幂等、过期及回执结果码已有本地/定向云验证。实际源码位于独立云端工作区 codex/cloud-agent-store，尚未合并到workbuddy/dev。

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
- `pip check` 曾在修改 Psycopg 前通过；OpenAPI 更新为12个REST路径（含新增动作状态查询）。
- 烟测使用隔离临时数据库与虚构任务摘要，没有连接外部账号、上传原片或调用模型。

## 当前阻塞与未完成

- CloudBase CLI 3.8.9 已获设备码授权。环境 `yingxu-dev-d8gowpye17ee27a61`、`ap-shanghai`、体验版；超限按量关闭。早期查询用量0不代表部署后实时用量。已创建函数 yingxu-agent-api，Active。
- `tcb env detail` 读回 PostgreSQL 共享实例 `postgres-f44f3luq`。部署目标据此调整为 HTTP 云函数，避免必须启用按量付费的云托管。
- 已执行 PostgreSQL v1 基础表、v2 客户端权限撤销/RLS、v3 service_role-only偏好更新RPC。原新表默认客户端权限过宽问题已收紧。Store真实读写、owner隔离、去重、偏好脱敏和状态条件更新已验证；v4回执结果迁移已执行并定向读回。
- 线上已升级0.2.0-store-preview，health200、ready503，database=ready、authentication=not_configured；无平台令牌401，业务AUTH_NOT_CONFIGURED503。0.1.1回退点已发布为函数数字版本2（版本1为0.1.0）。真实用户身份及完整同步语义尚未验收。配置进度见 `docs/云端与Agent/环境配置进度.md`。
- MySQL schema 保留为历史初稿；当前生产目标使用 `schema.postgresql.sql`。
- 真实 JWT issuer/audience/JWKS、`sub`/`device_id`/`origin` 签发规则未与 CloudBase 认证或手机端身份对接。短信、邮箱、微信登录/回调、刷新、注销与账号合并未实现。
- 云端数据库账号隔离依赖 JWT issuer 正确绑定设备身份；上线前需加入不同账号隔离、撤销、旧事件防复活、删除/导出和恢复验收。
- Agent 现在是有限只读任务查询和提议记录，不是已接入大模型的完整 Agent；没有媒体目录索引或任意工具执行。模型供应商/BYOK 和用量尚未配置。
- Agent session 持久化、缩略图、完整删除标记/防复活、错误码覆盖、设备重新绑定审批仍未实现。
- 百度/夸克官方个人授权及上传能力尚未核实，未实现。

## 部署准备

- `python3 infra/cloud/prepare_function_context.py` 会生成干净的 `infra/cloud/function-build/`，仅包含服务、契约、Python 3.11 锁依赖与 `scf_bootstrap`；脚本遇到已有目录会停止，不覆盖。
- 函数 `yingxu-agent-api` 已有可启动的受控预发布；独立云端工作区已部署本轮通过检查的数据层和Agent回执修复。源码包function-build不含Key，Secret通过项目外私有配置注入。
- 不把 `.env`、数据库连接串、JWT 配置材料或腾讯云令牌放进 Git、容器镜像或聊天。

## 桌面端联调提示

- REST 路由以 `docs/云端与Agent/openapi.json` 为准；已有 v1 schema 未修改。
- 仍需双方确认鉴权 claims、快照/删除游标、Agent action 状态及 WebSocket 消息格式后再由桌面端接入。
- 云端服务没有桌面路径、磁盘权限或源素材；云端提议不得替代本地预检/用户确认。

## 2026-10-26 续跑结果

- 四个临时跨软件交接文件按用户要求删除，不恢复；WorkBuddy范围以其交接给Codex报告为准。
- 全栈开发会话先只读审查，现只写desktop-ui接真实adapter；本地后端与宿主仍由WorkBuddy维护，双方不跨目录覆盖。
- 当前6项本地检查通过，4项真实CloudBase基础合同曾完整通过；最新v4结果码和撤销行为又定向通过。测试隔离owner已精确清理。
- Agent提议和回执支持内容一致的幂等重试、持久结果码；过期不阻止已接受长任务回报完成；查询任务结果按设备/job/sequence选新状态并标记kind=job。
- 仍需处理提交有序游标、整批ack/原子性、路径脱敏代际、权威快照、正式身份会话和模型接入。不能把预发布视为完整Agent上线。

## 整体目标继续（2026-10-26）

用户指出任务停止后，已恢复按完整未完成目标检查；旧usageLimited记录未被误标为完成。新增v5原子同步批次和账号写锁，本地8项通过，v5迁移已执行。CloudBase REST网关TLS握手超时使本次云检查未完成，尚未部署v5应用代码；保留已验证0.2.0预发布。网络同因重试停止，仍可继续未阻塞的本地Agent/身份/同步开发。

## Agent 会话持久化（本地实现，尚未部署）

POST /v1/agent/turns 可选成对提供 sessionId / turnId（各 1–160 字符），不提供时保持原无历史写入行为。轮次按 owner/session/turn 唯一；相同设备与 query 重试返回首次保存的回答，不同内容返回 TURN_ID_CONFLICT（409）。GET /v1/agent/sessions/{session_id}/turns 提供每页 100 条和 after_id 游标，严格按当前账号隔离。

SQLStore 与 PostgRESTStore 提供相同接口；v7-agent-turns 迁移草案依赖 v6，包含 RLS、撤销客户端权限、service_role-only 写 RPC，复用账号锁协调偏好变化与会话写入。syncAgentSessions=false 时仍回答但 persisted=false，不写入新历史；关闭开关不会删除既有历史。会话 query 是用户原文，syncPaths 开关只控制任务路径字段，不对聊天正文做自动语义脱敏，客户端必须明确展示会话同步开关。

生产 readiness 现在要求 v7。v6/v7 均未执行，v5 新应用仍未部署；不能将当前工作树直接覆盖线上 0.2.0。新增合约与 API 验证覆盖重试、内容冲突、分页、账号隔离、禁用写入和撤销设备；19 项总计 12 项本地通过、7 项云端跳过。尚未连接模型，不宣称模型会话或完整 Agent 已完成。后续仍需云端迁移/RPC 验证、正式身份、BYOK、缓存失效和桌面接线。

## 按框架推进：会话删除与防重放恢复

新增 DELETE /v1/agent/sessions/{session_id}，必须发送 {"confirmed":true}。在账号锁下删除当前 owner 的会话轮次并记录不含聊天正文的会话删除标记。重复删除幂等；旧 turn 或新 turn 重用已删除 sessionId 均返回 410 SESSION_DELETED；新建不同 sessionId 可正常保存。此操作仅针对云端 Agent 历史，不触碰本机素材，也不等于账号注销。

SQLStore / PostgRESTStore 同语义；v8-session-deletion 迁移草案依赖 v7，生产 readiness 要求 v8。v6/v7/v8 尚未执行；未部署，未重试受阻 CloudBase 网关。21 项检查：13 项本地通过、8 项云端跳过，覆盖确认前不删除、删除后无历史、重放不恢复、重复删除与跨账号隔离。旧客户端已缓存内容仍需后续删除通知 / generation 机制，不能声称完成跨端隐私删除闭环。

协调：已向全栈开发派发 R2 原生宿主联调及真实云端适配准备，范围保持 desktop-ui/ 与桌面联调记录；WorkBuddy 后端只读。本会话继续 cloud-service / infra/cloud。

## 自主推进规则与跨端会话删除通知

用户授权由本会话统一指挥、自主完成可独立开发验证的事项，除真实账号/凭据/资质或无法代做操作外，不因阶段完成等待确认。已同步全栈并复用现有 heartbeat，保持分工与不重试受阻网关约束。

扩展尚未执行的 v8 草案，为会话删除标记增加账号串行分配的 id，GET /v1/agent/session-deletions 支持按独立 after_id 游标拉取（100 条分页）。客户端须按账号保存删除游标、清除对应会话缓存并停止旧 session 重发；此游标不得混用任务 serverCursor 或设备 sequence。重复删除不生成额外标记。服务端测试已覆盖重复删除仅一条通知、分页与账号隔离。21 项中 13 项本地通过、8 项云端跳过。客户端消费、云迁移与云端验证未完成，不能宣称跨端删除已验收。

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

## 用户授权单次HTTP复验后的网络处置（2026-10-26）

用户允许一次限时复验，healthz 在4.89秒返回200、版本0.2.0-store-preview。随后进行真实CloudBase Store合约，首个动作CAS检查完整通过；第二项会话合约业务段执行后，在tearDown HTTP清理发生StoreUnavailable，失败原因已被现有存储层泛化，不能进一步断言原始异常必然为TLS超时。failfast与网络熔断已生效：共启动2项、1项错误，之后不再真实发请求。第二项不能标通过。完整证据保留cloudbase-http-store-contracts-20261026.log/json。

用户追加“如果再超时就先本地构建，网络没问题再打包推送”，本轮按连接异常保守停止所有云端操作、部署与推送。没有发布新应用，线上仍0.2.0；已有schema10管理SQL证据保持有效，不等于本轮HTTP合约通过。

清理未完成的测试owner精确清单已保存pending-cloud-fixture-cleanup-20261026.json（4个合成owner，包含此前已清理owner，不含真实账号）；生成同名.sql，仅待网络恢复后清理这些精确owner并回读各表0残留。清理SQL尚未执行，不能声明无残留。不得删真实数据或重置序列。

继续本地工作：新增verify_function_context.py，严格核对包内完整文件集合/逐文件SHA256/路径越界/可执行bootstrap/行尾/Linux依赖与当前app/contracts/lock/bootstrap源码一致；扫描外部凭据字节，连manifest也覆盖。2项破坏性样本回归通过。实际0.3.0-agent-preview包1499清单文件、32依赖、sourceMatched与credentialsChecked均true，未部署。旧包保留，测试和临时DB证据不删。

协调：独立验收已确认EXE/NSIS/ZIP内容完整hash一致、DMG容器有效；Windows运行回归仍缺x64 VM。已派独立线修复UTF8兼容ZIP副本与当前源码指纹清单，不能反推旧二进制来源；全栈R7继续本地原生适配，不调用CloudBase重试。
