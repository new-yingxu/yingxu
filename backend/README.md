# 影序本地后端 B1 草稿

范围：桌面 v0.4 框架的 Rust/SQLite 本地核心。前端 V2 仍使用 MockDesktopApi，尚未接入本后端；Agent、身份、CloudBase 和官方网盘接入由另一会话负责。

## 结构

- `core/`：可独立运行的库和 JSON-lines CLI，SQLite/FTS5、持久任务、文件安全、报告、健康审计、恢复、交付、照片基础分析。
- `tauri-host/`：单独 Cargo package。主窗口 IPC、原生目录授权、单 worker、托盘与退出检查点。不会启动可被网络调用的本地 HTTP 服务。
- `evidence/`：构建/测试日志、脱敏素材检查摘要和原前端快照。
- `scripts/read_only_card_check.py`：使用 release CLI，只读取明确指定来源，临时目录保存测试副本，原片不写回；报告不包含照片、具体文件名、路径、GPS 或 EXIF 内容。

## 本机命令

本机默认 macOS 27 SDK 与当前链接器不兼容，因此仅本次命令选择已安装的 26.5 SDK；没有修改全局配置。

```sh
SDKROOT=/Library/Developer/CommandLineTools/SDKs/MacOSX26.5.sdk cargo test --manifest-path backend/Cargo.toml
SDKROOT=/Library/Developer/CommandLineTools/SDKs/MacOSX26.5.sdk cargo clippy --manifest-path backend/Cargo.toml --all-targets -- -D warnings
SDKROOT=/Library/Developer/CommandLineTools/SDKs/MacOSX26.5.sdk cargo build --manifest-path backend/tauri-host/Cargo.toml
```

CLI 必须指定数据目录；不得指向源码、卡或备份目的地。

```sh
backend/target/debug/yingxu-core --data-dir /absolute/local/app-data rpc
```

输入一行 JSON，输出一行 JSON。例：

```json
{"requestId":"health-1","version":1,"method":"health","params":{}}
```

`worker-once` 执行一个排队任务；`worker-drain` 依次运行队列。CLI 供本地验证，不能转为网络服务或将 stdin 暴露给云端。Tauri 中由宿主管理 worker，网页不能调用 `worker.runNext`。

## 文件安全

1. 预检、确认、执行分离。计划 30 分钟有效；开始前重新确认来源/目标身份及来源大小、时间、文件身份。
2. 本地目标 1–8 个；不支持 NAS、网络共享、重叠目录、符号链接路径。目标空间与写权限预检。
3. 同目录独占临时文件、源流 SHA-256、目标可选回读校验、原子且不覆盖的提交。同名同哈希跳过，异内容加序号。
4. 转移强制校验。全部目标通过后再次确认，来源仅进入卡上的 `.yingxu-transfer-recovery/<job>/`，可撤回。**暂存仍占卡空间，当前没有永久删除或系统废纸篓清空实现。**
5. SQLite 检查点、单 worker 文件锁、分块暂停/取消、失败目标重试。异常进程退出后暂停任务；根据根目录和临时文件身份，只清理已登记的本任务临时副本。
6. 每个本地目的地独立记录结果。报告/历史清空不删除原片或副本。

## 影像边界

支持 JPEG/PNG/TIFF/WebP/BMP 的受限照片解码；压缩输入上限 128 MiB、解码分配上限 256 MiB、宽高各不超过 16384。最长边 512px JPEG 缩略图重新编码，不复制 EXIF。读取相机与拍摄时间等选定字段并标记来源；不保存原始 EXIF/GPS 块。

曝光比例和 512px 拉普拉斯方差仅是待人工复核信号，阈值未做真实标注集校准。未实现 MUSIQ、视觉相似、运动/重影判断、LibRaw、独立视频质量流程。不支持解码的文件仍可按字节备份，标记未分析。无论何种分析结果都不自动删除。

`autoAnalyze`、`startOnLogin`、相似度、上传限速等设置字段可持久保存，但 B1 没有对应后台分析调度/开机启动/视觉相似/真实上传执行器；不能仅凭设置保存成功宣称功能已运行。`health.capabilities` 及状态文档用于确认实现边界。

## 证据与下一阶段

详见 `../docs/协作交接/本地后端状态.md` 和 `../docs/桌面端/本地后端-API-v1.md`。当前只在 Mac ARM64 验证，Windows 构建/真实拔盘/磁盘满/权限变化及 Tauri 图形生命周期验收待完成。没有发布、安装升级、账号登录或云端部署验收。
