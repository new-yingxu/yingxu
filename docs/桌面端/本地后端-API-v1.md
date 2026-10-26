# 本地后端 API v1（B1 草稿）

该协议是 Rust 本地服务接口，与 `contracts/` 的云端同步/Agent v1 分属不同层。未经双方确认，不修改现有云端 v1 schema。

## 传输

Tauri 主窗口通过 `backend_request` 调用；目录须先通过原生 `select_directory(purpose: source|destination)` 授权。调用发生在阻塞线程池，文件任务交给独占 worker。调试 CLI 使用 JSON-lines，不开 HTTP 端口。

```json
{"requestId":"unique-correlation-id","version":1,"method":"backup.preview","params":{"source":"/absolute/card","destinations":["/absolute/backup"],"name":"导入","verify":true,"selected":[],"subdirectory":null}}
```

成功 `{requestId,version:1,ok:true,data}`；失败 `{requestId,version:1,ok:false,error:{code,message}}`。未知参数拒绝；未知方法/协议明确报错。输入路径必须绝对且属于本地目录。`confirmed` 必须由用户看到当前具体对象/影响后提供，不能把 Agent 提议或云端状态当成本地确认。

## 方法表

| 方法 | 参数 / 返回 |
|---|---|
| `health` | 版本、协议、能力；Tauri 额外返回 workerReady |
| `backup.scan` | `{source}` → 普通文件相对路径、指纹、类型；只读扫描 |
| `backup.preview` / `transfer.preview` | PlanRequest → planId、文件、总容量、目标身份/空闲空间；不开始复制 |
| `jobs.start` | `{planId,confirmed,idempotencyKey}` → 排队 Job；幂等键同请求重放，异请求冲突 |
| `jobs.detail` | `{jobId}` → Job/逐文件/逐目标/runtime |
| `jobs.control` | `{jobId,action:pause|resume|cancel|retry,targetId?:null}`；只有失败目标可单目标 retry |
| `history.list` | `{query,state:all或任务状态}` |
| `history.rename` | `{jobId,name,confirmed}`；同步更新既有报告名称 |
| `history.clear` | `{confirmation:"清空归档记录"}`；存在活动任务时拒绝 |
| `library.index` | `{source}` → 只读索引计数，变化来源使旧分析/缩略图失效 |
| `library.search` | `{query,kind:all|photo|video|other,quality:all|good|review|unanalyzed}`；最多 500 项，名称/路径/选定元数据及 FTS5 |
| `library.analyzePhoto` | `{mediaId}` → 元数据、解码状态、质量候选、缩略图可用性 |
| `library.thumbnail` | `{mediaId}` → `{mimeType,bytes,maxEdge:512,exifIncluded:false}`；来源变化拒绝读取 |
| `library.review` | `{mediaId,state:good|review|unanalyzed,note,confirmed}` → 人工复核标签；不操作原片 |
| `library.duplicates` | 索引中相同 SHA-256 组；使用前须重新验证，不自动清理 |
| `replicas.list` | 副本 ID、路径、哈希和最近校验结果 |
| `audit.run` | 所有已登记副本回读 SHA-256；仅本轮另一健康副本可支持 recoverable |
| `restore.start` | `{replicaId,destination,confirmed,idempotencyKey}`；来源副本重新校验后排队 |
| `transfer.prepareRemoval` | `{jobId}` → 有效期 5 分钟的一次性确认 token |
| `transfer.confirmRemoval` | `{token,confirmation:"将来源移入可恢复目录"}`；逐文件重新验证后可撤回暂存 |
| `transfer.undoRemoval` | `{jobId,confirmed}`；原路径被占用时不覆盖 |
| `reports.get` | `{jobId}` → schemaVersion1 报告 |
| `reports.export` | `{jobId,destination,format:json|md|txt|csv,confirmed}`；不覆盖同名报告，CSV 防公式注入 |
| `presets.list/save/remove/preview` | 预设 fields 为 date/event/location/device/kind/custom；preview `{presetId,values}` 生成安全相对目录；默认预设不可删除 |
| `settings.get/update/reset` | 白名单设置、类型/范围校验；拒绝明文秘密及未配置的模型/Webhook 启用 |
| `delivery.create` | `{name,mediaIds,destination,includeContactSheet,includeManifest,confirmed,idempotencyKey}` → 排队 Job |
| `delivery.list` | 校验、清单/联系表均完成后的独立交付包 |
| `workbench.snapshot` | 最近任务、文件数/容量、待复核数 |
| `sync.outbox` | `{afterSequence,limit:1..200}` → v1 envelope 数组；尚无 HTTP 发送/服务端确认，不提供网页 ack |

`worker.runNext` 只在 CLI 验证可用；Tauri 明确拒绝。

## 状态与前端适配

- Job state：queued/running/paused/success/partial/cancelled/failed；phase 另描述复制、校验、来源待确认等。
- runtime：itemId/currentFile/currentBytes/currentTotalBytes/speedBytesPerSecond/updatedAt；复制时约每 200ms 更新 SQLite，UI 可轮询详情。Tauri 的 `local-job-updated` 当前是每次 worker 返回的摘要，不是逐块推送。
- 一项复制失败会记录原因并继续其他目标；部分成功保留成功结果。显式跳过校验的任务 phase 为 copied_without_verification，verified 为 false。
- 前端 `DesktopApi` 中已格式化的 size/speed、sourceId、pattern 等不能直接传给 Rust；正式 adapter 必须处理字节/时间格式及原生目录选择、预检确认、跨端登录门禁。当前 V2 mock 不变。
- `auth.restore` 为 null；真实登录、Agent、账号/网盘连接返回 CLOUD_ADAPTER_NOT_CONNECTED，不产生假会话。

## 云端兼容意见（2026-10-25）

已阅读 `docs/协作交接/云端Agent状态.md`、云端 OpenAPI 与 auth/sync 实现，未改云端目录。

1. 接受服务端 JWT 验签后提供 `sub` / `device_id` / `origin` 的方向；本地暂未实现 JWT 登录/密钥库。桌面安装 UUID 需由可信注册流程绑定到签发的 device_id，不能随意替换已有 outbox 的设备 ID。
2. 本地 sync.outbox 已投影为现有 sync-event-v1：eventId/deviceId/sequence/eventType/occurredAt/payload。owner 始终由服务端身份得到。
3. 路径同步默认关闭；关闭时停发旧的待发含路径事件，新增脱敏事件并分配新 ID/序号，不重写原事件语义。真正联调须先关闭服务端路径偏好并清理既有路径，再发送新事件；当前没有执行远端清理。
4. HTTP `acceptedEventIds` 与 `duplicateEventIds` 才能由受信任桌面传输器确认本地已发送；部分失败/网络断开保留重试。当前未建立传输器/ack，不能把读取 outbox 当作同步成功。
5. 云端 snapshot 的 after_id/WS cursor 是服务端游标，本地 sequence 是设备序号；分别持久保存，不能相互替代。删除 tombstone、防旧事件复活需要单独协议；目前历史清空只是本地记录管理。
6. Agent 提议为不可信输入；当前没有执行桥。后续只允许已认证桌面、已批准目录/fileId、短时预检和本地逐次确认，不允许 mobile 调用桌面执行器。
7. 发布前补设备撤销/注销/删除、不同账号隔离、登录过期、离线补发、路径关闭重放与错误码联调。

图像库实现参考：[image 官方源码与格式说明](https://github.com/image-rs/image)、[Rust Cookbook EXIF 示例](https://github.com/rust-lang-nursery/rust-cookbook/blob/master/src/multimedia/image.md)。只说明依赖用法，不证明质量准确率或 RAW 覆盖。
