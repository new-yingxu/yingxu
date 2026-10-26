# 影序本地 Agent 闭环开发交接（2026-10-26）

> 接收对象：Agent 开发会话。用户审阅总方案并明确继续之前，本任务暂停功能开发。现有未提交工作树保留，不清理、不覆盖、不提交。

## 分支与负责范围

- 工作分支：`codex/cloud-agent-store`，隔离 worktree：`/Users/nanyu/Documents/AI工具/02_Codex数据_0907/worktrees/yingxu-cloud-agent/new-影序agent开发`。该分支已推送 GitHub；远端与本地分支头已回读一致。
- 当前阶段职责：本地 LangGraph/runtime、本地模型 provider、Agent 工具 loop、持久步骤/恢复逻辑及其桌面宿主协议。云端 Agent、账号、CloudBase 和部署排到本地闭环之后。
- 只修改该 worktree；不要直接写 `backend/**`、WorkBuddy 的 `desktop-ui/**`，或另一人的工作树。接口变化先在此分支更新本交接文档和协议请求。

## 与总指挥/WorkBuddy 的分工

- WorkBuddy 负责 Vue UI、交互状态和前端 adapter，分支 `workbuddy/dev`。
- Codex 总指挥负责 SQLite/FTS5、本地备份内核、Tauri 宿主与集成验收，分支 `codex/yingxu-desktop-agent`。
- Agent 开发负责本地模型和 LangGraph 调度，向 Codex 提交完整请求/响应、状态、错误、恢复/幂等语义及测试证据；向 WorkBuddy 只提交稳定的 UI/adapter 字段契约。
- 任何联调都通过对应分支内的 Markdown 交接文档和提交记录，不靠聊天口头字段约定。

## 本地阶段顺序

1. 把现有本地 Agent/runtime 代码、测试、数据库迁移和证据盘点成已完成/未完成表；明确哪些是真实本地模型调用，哪些是 fixture 或隔离服务。
2. 先冻结本地协议：模型状态、建 turn、工具请求、待本机确认提议、确认/拒绝、结果回执、重复请求、显式恢复、失败和不确定结果。密钥只保留在本机受控存储/进程内，不进入 UI、日志或安装包。
3. 每个小片先写状态与契约，再实现并即时测试。第一闭环优先只读本地查询；之后才接用户确认式打开/改名/移动/清理等副作用。
4. 对每个真实本地模型用例记录模型标识、输入边界、工具调用链、执行时长和实际返回；禁止把云端请求跳过写成通过。
5. 本地 DB、模型、Agent、宿主和 UI 联通并有回归记录之后，才整理独立的云端阶段计划。

## 本次交接状态与暂停要求

本次核查时本 worktree 有多项未提交的 `cloud-service/**`、`infra/cloud/**`、本地 Agent/runtime 模块、迁移、测试、日志和 evidence。它们尚未形成提交，也没有推到 GitHub；不可描述为 Codex 主分支集成或本地闭环已完成。请暂停继续改码，安全保留全部文件与未提交状态，等待用户审阅。

继续前先回报精确 `git status`、文件清单、最新验证结果和未完成项；总指挥审查后，再依据用户确认的阶段顺序分批推进。只对本任务拥有的文件进行提交，不得夹带其他 worktree 改动。
