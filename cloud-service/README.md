# 影序云端服务

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

生产使用 CloudBase PostgreSQL，`DATABASE_URL` 应按控制台连接信息配置为 SQLAlchemy `postgresql+psycopg://` URL，并遵照控制台 TLS 要求。还需接入经过确认的 JWT issuer/JWKS，精确配置 HTTPS CORS allowlist。JWT 必须由可信身份服务签发，且有 `sub`、`device_id`、`origin` claims。缺少任一配置时，readiness 不通过。Production 不会自动建表，需先完成并执行 `infra/cloud/schema.postgresql.sql`。

CloudBase HTTP 云函数需要 `scf_bootstrap` 启动脚本并监听 9000 端口。依赖在云端安装；生产函数使用 Python 3.11，支持 HTTP 与 WebSocket。

CloudBase 部署和账号条件见 [`infra/cloud/README.md`](../infra/cloud/README.md)。

