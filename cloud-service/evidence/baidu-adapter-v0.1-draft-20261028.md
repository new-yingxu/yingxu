# 百度网盘适配器 v0.1 草稿：本地证据与交接

状态：本地实现和模拟验收通过；真实 OAuth、线上部署、百度上传未验收。

## 实现边界

- 服务端：`app/baidu_oauth.py`，已认证且已注册的桌面设备获取配置或换取/刷新令牌；固定百度 HTTPS 端点和已登记回调，无自动网络重试，不回显秘密，不在云端存储网盘用户令牌或影像。
- 原生端：`backend/tauri-host/src/baidu.rs`，一次性回调和 state 校验、按影序账号隔离的系统凭据库、刷新结果不明标记；provider token 不返回 WebView。
- 核心：`backend/core/src/baidu.rs`，SQLite 已校验副本门槛、流式 SHA-256/MD5、4 MiB 分片、失败游标、跨进程锁、取消、结果不明只读核对。真实网络固定为 Baidu 域名，错误不返回带 token 的 URL。
- UI：`desktop-ui/src/components/BaiduPanel.vue`，备份目录原生授权、已校验副本预览、明确目标后确认上传、进度/速度/预计剩余时间、取消和重试。浏览器 mock 只在临时验收夹具中使用，夹具已删除，不随生产构建发布。
- 旧 `netdisk.*` 保留为本地 mock 协议；真实百度流程通过原生 `baidu_request`，没有把旧 mock 闸门直接改成“已获批”。
- 当前 cloudbase-readiness 的原备份向导仍是演示；本轮独立网盘入口可消费真实 SQLite 备份副本，备份完成后自动上传需要继续与已完成的桌面真实备份适配器集成。

## 本地验证

| 检查 | 结果与范围 |
| --- | --- |
| Rust core 全量 | 58 通过，包括复制/校验/恢复/媒体及百度新旧协议 |
| Tauri 宿主全量 | 20 通过；没有触发真实钥匙串或供应商网络 |
| 云服务 unittest | 277 总项，147 通过，130 因显式 PG/线上验收条件未启用跳过 |
| 百度最终定向 | core 9 通过（含 2 个旧 Provider 测试），宿主 5 通过 |
| Vue/TS 构建 | 通过；原生开发 binary 构建通过，未安装/启动包验收 |
| UI 合成夹具 | 760×840、1280×840：副本选择、上传确认、长中文路径、失败/完成状态；DOM 横向溢出均为 0 |
| 云函数上下文 | 2739 文件逐项与源码/锁文件匹配、60 个 Linux/Python 3.11 依赖；未做实际凭据字节扫描、未部署 |

关键失败场景：副本未校验、运行前副本变更、单分片网络失败后只续失败片、错误分片 MD5、上传取消、账号隔离、任务重复与锁、create 响应丢失后只读核对、刷新前持久化不明标记、刷新令牌轮换、callback 错误路径/state/重复参数、token 接口匿名/非桌面/设备拒绝、scope 缺失、错误回显脱敏。HTTP 编码测试使用真正的本机 HTTP socket，生产代码没有可配置的任意上传主机。

截图均为合成数据（不是百度线上成功证据）：
- `desktop-ui/evidence/baidu-v0.1-draft-20261028/confirm-760.jpg`
- `desktop-ui/evidence/baidu-v0.1-draft-20261028/confirm-1280.jpg`

## 已准备的候选

- 云上下文：`infra/cloud/function-build-baidu-v01-draft-20261028/`
- buildId：`401b4b4ba15f5a3ab858daceec0137801b20e80e4eb929a44b064c5089ebaceb`
- 服务版本沿用当前草稿 `0.9.0-local-sync-preview`、schema v16；没有进行数据库迁移。
- 原生 binary：`backend/tauri-host/target/debug/yingxu-desktop-host`；嵌入既有 yingxu-dev 的公开认证/API origin 和 client ID，不嵌入应用 Secret。
- 此开发构建有原有 `cloud_auth::post_to` dead_code 警告；没有编译错误。

## 顺序交接与停止点

1. 本机缓存的 CloudBase CLI 3.8.9 可以运行，但 `env list` 返回 `No valid identity found`。当前会话未加载 CloudBase 管理 MCP。需完成官方登录授权，再只读核对 yingxu-dev/yingxu-agent-api；不要求重新创建环境或百度应用。
2. 在服务端安全配置 `BAIDU_CLIENT_ID`、`BAIDU_CLIENT_SECRET`、`BAIDU_APP_FOLDER`，保留当前所有其他配置；扫描候选不含实际秘密后再部署并回读状态。现有授权允许继续，但凭据由官方安全入口处理。
3. 桌面用户登录后，启动百度 OAuth，用户在百度官方页面登录并确认授权；真实 `netdisk` scope 及上传资格由返回值验证。
4. 仅用隔离合成素材走真实本地备份→副本校验→确认上传→远端 MD5/大小核对→报告；检查重试不重复合并、断开连接不删除远端文件。
5. 接到当前真实备份 UI/任务队列，确认局部失败不影响本地成功；真正完成第五项后停止，等用户确认后再做其他项。

## 参考协议

- https://pan.baidu.com/union/doc/al0rwqzzl
- https://pan.baidu.com/union/doc/3ksg0s9ye
- https://github.com/baidu-netdisk/baidu-drive-sdk-go/tree/main/baidudriver/api ：本轮读取 auth_code2token、auth_refresh_token、upload_precreate、upload_slice、upload_create 源码核对协议。官方文档页面读取工具报错，以实际 API 验收继续校准。
