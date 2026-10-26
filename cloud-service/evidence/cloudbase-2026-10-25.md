# CloudBase 云端准备证据

## 数据库

- 环境：yingxu-dev，ap-shanghai；PostgreSQL 17.11，running。
- 已执行并读回 migration version 1 和 2。
- 五张应用表均启用 RLS；anon SELECT=false，authenticated INSERT=false；客户端全部表/序列权限已撤销。
- 无令牌请求 PostgREST 返回 401；环境服务端 Key 读 schema_migrations 返回 200，包含 version 1 和 2。
- 发现并修复：CloudBase 的新表默认授权包含 anon SELECT 与 authenticated 写操作；本服务只允许服务端访问这些表。

## 本地部署行为检查

- APP_ENV=production，缺少 DATABASE_URL/JWT 时，进程能正常启动。
- /healthz 200；/readyz 503；带无效 token 的业务请求返回 503，数据库 engine 未创建。
- 临时 SQLite、显式开发 token 下的设备注册仍通过；未使用真实业务数据。
- 依赖按 Linux x86_64/Python 3.11 安装，pydantic-core .so 为 cpython-311-x86_64-linux-gnu。

## 部署边界

- 预发布只用于云端运行检查，身份与应用数据库连接尚未配置。
- CLI 首次组合 --cwd 与 --dir . 解析到仓库根目录，检查到缺少 bootstrap 后停止；随后改用实际工作目录与绝对 --dir，未上传仓库根目录。
- 函数创建成功：yingxu-agent-api，lam-gei240jp，Active，Python3.11/HTTP+WS，256MB，timeout60s；代码包约27.5MB。
- 线上0.1.1：带平台Key的health200，ready503且authentication=not_configured；不带Key401；业务接口AUTH_NOT_CONFIGURED503；WSS收到auth.required，无效令牌后4403。
- 线上0.1.0已发布函数数字版本1用于回退，最新0.1.1为$LATEST。
- v3偏好RPC已执行。随后本地Store重构仅做AST语法解析，未做运行/业务验收且未部署；不能沿用先前测试结论。
