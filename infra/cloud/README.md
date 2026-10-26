# CloudBase 部署准备

此目录保存云端部署说明和模板，不包含账号凭据、环境 ID 或生产环境变量。

## 当前状态

- 服务源码：`../../cloud-service/`
- 实际环境已由 CloudBase CLI 读回：`yingxu-dev-d8gowpye17ee27a61`，地域 `ap-shanghai`，体验版；共享 PostgreSQL 实例已创建。云函数和云托管服务当前均为空。
- 当前目标改为 HTTP 云函数：官方支持 FastAPI、Python 3.11 与 WebSocket。体验版的云托管 CPU/内存需另行开启超限按量，因此本项目暂不创建云托管服务。
- CloudBase CLI 已通过设备码授权；部署前仍需配置 PostgreSQL 连接和正式 JWT 身份来源。不得使用开发 token 或 SQLite 作为生产数据层。
- 当前账期配额为 3000 资源点，CLI 读回时用量/扣量均为 0；超限按量未开启。部署后的调用会消耗额度，须持续监控。

## 本地启动

从 `cloud-service/` 目录创建虚拟环境、安装锁定的 `requirements.lock`，然后设置 `.env.example` 中的开发配置并运行：

```sh
uvicorn app.main:app --host 127.0.0.1 --port 8080
```

认证默认关闭，`/readyz` 会报告未就绪，所有业务 API 都拒绝匿名调用。只有显式设置 `APP_ENV=development`、`AUTH_MODE=dev` 和本地临时 `DEV_AUTH_TOKEN` 后，才可使用隔离开发身份；该模式不能部署到生产。

## HTTP 云函数部署准备

1. 从 CloudBase PostgreSQL 控制台取得服务端连接参数；为 Agent 服务创建最小权限账号并确认 TLS/网络要求。连接串和密码只进入 CloudBase Secrets，不写入仓库或聊天。
2. 依次执行 `schema.postgresql.sql` 和 `schema.postgresql.v2-access.sql`。CloudBase 默认会给新表授予客户端权限；第二份迁移撤销这些授权并启用 RLS，只有后端访问。生产 `/readyz` 检查版本 2。这两份迁移已在开发环境执行并读回。
3. 确认可信身份服务签发的 JWT claims (`sub`、`device_id`、`origin`) 与 JWKS URL。没有这份身份契约时不能启用生产 API。
4. 使用 `python3 infra/cloud/prepare_function_context.py` 生成 `infra/cloud/function-build/`，需要 uv。脚本按 `requirements.linux.lock` 安装 Python 3.11/x86_64 manylinux2014 wheels 到 `third_party/`，避免把 macOS 二进制上传到 Linux。
5. 从 `infra/cloud/function-build/` 目录运行 `tcb fn deploy yingxu-agent-api --dir "$PWD" --ws --runtime Python3.11 --install-dependency false -e yingxu-dev-d8gowpye17ee27a61`。只上传该目录。实际执行使用绝对目录；CLI 的 `--dir .` 不应与 `--cwd` 混用，实测可能解析到仓库根目录。
6. 部署后记录函数版本/路径和访问策略；验证 `/healthz`、`/readyz`、JWT、PostgreSQL 读写、WebSocket 与回滚。

缺少数据库或身份配置时，生产进程能启动，但 `/readyz` 返回 HTTP 503，业务请求失败关闭，不会退回开发 SQLite 或开发 token。当前正在发布仅验证云端启动的受控预发布版本，真实数据库连接及用户身份尚未接入。

## 官方文档

- [CloudBase FastAPI HTTP 云函数](https://docs.cloudbase.net/en/cloud-function/frameworks-examples/fastapi)
- [CloudBase 云函数 CLI 部署](https://docs.cloudbase.net/cli-v1/functions/deploy)
- [CloudBase PostgreSQL 连接](https://docs.cloudbase.net/database/postgresql/connecting-to-postgresql)

