# 影序交接：WorkBuddy 接手百度网盘与账号（2026-10-28）

## 停止点

用户要求 Codex 收口暂停，后续开发交给 WorkBuddy。不要继续自动部署或后台续跑；保持分支隔离，不合并主线或发布版本。Windows 和移动端验收由其他同学负责。

## 已完成与证据

- 分支 `codex/cloudbase-readiness`，本交接前最新代码 `1b9ff0d`；核心实现 `23b7870`，部署记录 `cadf556`。本交接提交为当前文档所在提交。请先 fetch 和检查工作树，谨慎按需移植，不要整体覆盖 WorkBuddy 的变更。
- 百度 OAuth 服务端、原生授权与凭据存储、已校验副本的分片上传、失败/结果不明处理、桌面独立网盘面板已经实现；合成数据 UI 和单元测试通过。详细结果与限制见 `cloud-service/evidence/baidu-adapter-v0.1-draft-20261028.md`。
- `yingxu-dev` 的 `yingxu-agent-api` 已合并保存百度配置，原有配置回读一致；候选函数已部署，代码逐文件哈希回读一致。未认证请求新百度配置路由返回 401；这不代表真实用户登录或上传通过。
- 验证码标识长度问题已修复：CloudBase 实测返回约 639 字节，原先 500 字节上限导致界面报错；已将发送/验证上限统一为 8192 字节，13 项认证定向测试通过；macOS 隔离修复版已构建。

## 当前登录阻塞（真实问题，尚未修复）

用户使用邮箱验证码和密码注册后，界面提示“影序账号会话已失效”。按源码顺序 CloudBase 注册已成功，但影序服务端 session exchange 返回 401，不能重复让用户注册。

经用户允许，只读调用真实 CloudBase `/auth/v1/user/me` 和 `/auth/v1/token/introspect`：两个 HTTP 200；真实账户返回 `type=external`，但未返回 `status`、`internal_user_type` 和 `providers`；introspect 返回 Bearer 且 client 与环境映射、subject 一致。服务端 `cloud-service/app/cloudbase_identity.py` 当前要求 `status=ACTIVE`、`internal_user_type=generalUser`，因此会拒绝这个合法账户；目前没有验证缺失状态是否代表已激活，也没有安全地放宽该检查。

下一步：基于腾讯官方认证/管理 API 或数据库的可靠账号状态来源设计验证；明确拒绝封禁、内部及匿名身份，不应简单假定缺失的 `status` 为 ACTIVE。补充缺省字段、禁用账号、匿名、内部账号、身份不匹配和上游故障测试，然后仅对 `yingxu-dev` 构建、验证并部署修复，使用用户已有账号完成真实登录和会话交换。服务端凭据放安全配置，不写入 Git、共享记忆或聊天。

## 未完成事项和顺序

1. 修复上面的 CloudBase 身份验证并实际验收桌面登录，使用已有账号，不重复注册。
2. 完成百度官方 OAuth 用户授权与 `netdisk` 权限验证。开放平台应用目前标注“个人场景使用”，正式面向 C 端发行的授权范围仍需向百度确认。
3. 只使用合成素材完成备份→校验→上传→远端 MD5/大小核对，并验证失败、取消、重试和结果不明时的安全行为。此前没有真实百度上传成功证据。
4. 将独立网盘面板接入真实桌面备份 UI 和任务队列，实现本地备份校验成功后按用户勾选上传；当前分支备份向导仍为演示适配器，不能把面板完成等同于自动上传完成。
5. 后续云端账号、同步与 Agent 的完整跨端验收仍需按原计划推进；暂停短信、微信、夸克。版本发布、Windows 与移动验收按用户安排交由对应人员。

## 本机状态与边界

- 本轮隔离应用为 `backend/tauri-host/target/release/bundle/macos/影序百度验收修复版.app`；其隔离数据库和 `/private/tmp/yingxu-baidu-secure-setup/` 下合成夹具需保留到验收完成。已运行的另一份验收应用和其他工作树不应被覆盖。
- `codex/local-agent-ui-closure` 工作树此前存在未提交修改；接手前重新检查，不要丢弃。
- 默认 macOS 27 SDK 与本机链接器不兼容；本轮使用 `SDKROOT=/Library/Developer/CommandLineTools/SDKs/MacOSX26.5.sdk` 完成构建。
- 百度应用与此前云端凭据在截图或控制台操作中曾暴露，用户安排开发结束后统一轮换；不得复制到文档、日志或公共仓库。
