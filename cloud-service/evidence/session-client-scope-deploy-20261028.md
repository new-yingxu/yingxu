# 账号会话修复：外部账号状态判定（2026-10-28）

## 问题

真实账号注册成功后，服务端 `/v1/auth/session/exchange` 返回 401 `INVALID_ACCOUNT_SESSION`。
用户无法登录，且不能让其重复注册。

## 根因（实测，非推测）

同一环境内两类账号的 `user/me` profile 形状不同：

| 账号来源 | status | type | internal_user_type | providers |
| --- | --- | --- | --- | --- |
| 管理端创建（探测账号） | `ACTIVE` | `external` | 缺失 | 缺失 |
| 用户邮箱注册（真实账号） | **缺失** | `external` | 缺失 | 缺失 |

原实现要求 `status=ACTIVE` 且 `internal_user_type=generalUser`，两条都不成立，
等于永久拒绝所有正常外部账号。管理端 API 对用户账号报告 `UserStatus=ACTIVE`、
`Type=externalUser`，说明账号本身合法。

附带问题：`/auth/v1/user/me` 的 `client_id` 默认是环境 ID；原实现先取 profile 再
introspect 取 `client_id`，顺序反了，profile 不能用令牌所属 client 作用域取。

## 修复

`cloud-service/app/cloudbase_identity.py`：

1. 先 `token/introspect` 取令牌所属 `client_id`，校验 `sub`、`token_type=Bearer`、
   `client_id` 在服务端 `CLOUDBASE_CLIENT_ORIGINS` 注册表内、未显式 `active=false`。
2. 再用该 `client_id` 请求 `user/me`（令牌作用域），profile `sub` 必须与 introspect 一致。
3. 状态判定：**有 `status` 时以它为准**（非 `ACTIVE` 拒绝）；**没有 `status` 时**不假定为激活，
   而是要求 `type=external` 且账号已绑定可自助验证凭据（`has_password`/`password`/`email`/
   `phone_number`/`username`/`user_id`/非匿名 provider）。匿名、内部账号、内部非 generalUser
   一律拒绝。缺失凭据的"空壳"账号仍然 fail-closed。
4. 失败原因写入日志（仅静态文案与字段名，不含令牌、邮箱、密码或 profile 取值），
   如 `account status unavailable and no verified credential`、`token client is not registered`。

## 测试

`cloud-service/.venv/bin/python -m unittest discover -s tests`（Python 3.11）：
**278 项，148 通过，130 跳过（需显式 PG/线上条件），0 失败。**

新增/调整：
- `test_profile_request_is_scoped_to_token_client_and_omitted_fields_fail_closed`：
  `user/me` 必须带令牌自己的 `client_id`；邮箱注册形状（无 status/providers）可接受；
  无 status 且无任何凭据必须拒绝；`BLOCKED`、`internal`、`administrator` 必须拒绝。
- `test_both_fixed_same_environment_apis_and_server_client_map`、`test_session_exchange_http`、
  renew 系列：改为 introspect→profile 顺序；账号不匹配 403、未注册 client 401、撤销设备 403 不变。

## 部署（yingxu-dev）

| 项 | 值 |
| --- | --- |
| 环境 | `yingxu-dev-d8gowpye17ee27a61`（体验版，ap-shanghai） |
| 函数 | `yingxu-agent-api`，`$LATEST` |
| 上下文 | `infra/cloud/function-build-session-client-scope-20261028` |
| buildId | `f12b17eaf708a4e8fe1684e53ddaa4ae21f0484c2eb3dd99fbb3f4e00e5b8416` |
| 版本 / schema | `0.9.0-local-sync-preview` / v16（无迁移） |
| 校验 | 2739 文件哈希、60 个 Linux/Python 3.11 依赖、凭据字节扫描、源码一致性：通过 |
| 方式 | `tcb fn code update --deployMode cos`（仅更新代码） |
| 结果 | `Function code updated successfully`，回读 Status=`Active`、CodeResult=`success` |

部署前后环境变量逐项哈希比对：15 个变量键名与取值完全一致，未触碰
`SESSION_PRIVATE_KEY`、`BAIDU_*`、`CLOUDBASE_*`、`CORS_ORIGINS`。

## 端到端验收（真实账号，2026-10-28 20:21）

用**用户本人账号** `luguanlin20050927`（用户名，uid 2115367706189369344）在 yingxu-dev 实测：

| 步骤 | 结果 |
| --- | --- |
| `POST https://<env>.api.tcloudbasegateway.com/auth/v1/signin?client_id=<env>` | 200 |
| `POST https://<env>-1479513274.ap-shanghai.app.tcloudbase.com/v1/auth/session/exchange` | **200**，`origin=desktop`、`expiresIn=900`、`deviceId=device-b492…` |
| `GET /v1/devices`（影序短会话） | 200，1 台设备 |

修复前同一路径为 401 `INVALID_ACCOUNT_SESSION`（17:49 日志 RequestId `78a3fe27`）。

## 账号与凭据处置

- 为做真实令牌验证，曾创建一次性探测账号 `yingxu-probe-20261028`（密码由本地随机生成、
  未写入仓库与聊天），验证后已删除，回读用户列表仅剩用户本人与内置 administrator。
- 用户账号原 `Name`（用户名）为空，无法用于账号密码登录；已设为 `luguanlin20050927`
  （`tcb user update --name`），密码按用户要求重置为临时密码，存放于本机
  `~/yingxu-temp-password.txt`（600 权限，未进入仓库/日志/聊天）。使用后应改密码并删除该文件。

## 未完成

- 桌面端界面内的登录点击验收（应用已重建为「账号密码」单一登录方式，需人工输入）。
- 百度真实 OAuth 授权、`netdisk` 权限核实、合成素材真实上传与远端 MD5/大小核对。
- 网关层凭据校验：`api.tcloudbasegateway.com` 对无凭据请求返回 401 `MISSING_CREDENTIALS`，
  业务 `/v1` 路由在 HTTP Gateway host 上；旧文档「网关鉴权关闭」需按此更新。
