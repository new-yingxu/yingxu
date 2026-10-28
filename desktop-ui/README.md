# 影序桌面前端

- 草稿编号：`V2`（应用包版本 `0.2.0-draft.1`）
- 基线：桌面端功能框架与技术框架 `v0.4` 草稿
- 当前状态：原生 Tauri 连接本地文件与任务服务，欢迎页可直接进入本机模式；普通浏览器使用明确标识的演示数据。
- Windows 客户界面与文件操作审核记录：[2026-10-29 审核报告](../docs/桌面端/Windows桌面审核-2026-10-29.md)。历史联调记录保留在下方，早期原型说明不代表当前原生能力。
- 视觉：参考影序 Pocket 移动端，统一米白底色、深绿主色、绿色选中态、金色进度提示和圆角卡片

## 预览

开发预览地址：<http://127.0.0.1:5173/>

启动：`pnpm dev`

## 前端页面与交互范围

- 原生启动：无需账号进入本机模式；不展示未接通的登录入口。扫码、手机号与邮箱表单仅在浏览器演示模式显示。
- 工作台：备份概况、最近任务、待复核提示和快速操作。
- 复制新内容：来源扫描、目标选择、命名预设、校验设置和开始任务演示。
- 转移老内容：候选文件选择、目标选择，以及校验后删除来源的明确确认。
- 影像资料库：搜索、媒体类型与质量状态筛选。
- 归档记录：状态筛选、搜索、详情报告、重命名、清理演示、暂停/继续/取消/重试。
- Agent：检索答复与拟执行操作预览；有副作用的操作需用户明确确认。
- 命名预设：新建、设为默认、删除。
- 恢复与健康审计：演示检查结果、恢复路径预览。
- 交付包：选择素材并配置联系表与清单。
- 账号与设备：设备状态、撤销入口、跨端同步范围。
- 偏好设置：主题、启动与关闭行为、校验、质量复核、AI、云盘、限速、Webhook、同步和隐私选项。

## 后端 API 预留

`src/api/contracts.ts` 定义 `DesktopApi` 和领域数据类型；`src/api/mock.ts` 实现本地 `MockDesktopApi`，供预览交互使用。页面通过接口分组预留了以下后端能力：

`auth`、`workbench`、`backup`、`transfer`、`library`、`history`、`agent`、`account`、`settings`、`presets`、`audit`、`delivery`。

普通浏览器的 mock 数据仅在前端进程内演示，不读取磁盘。原生窗口使用真实 IPC 与本机文件服务，不回退到 mock。本机模式停用云账号、同步与网盘；本机 Agent 需要单独打包运行组件，并由用户准备 Ollama 模型。

## 客户界面回归

```sh
pnpm install --frozen-lockfile
pnpm build
pnpm exec playwright install chromium # 无系统浏览器的测试机
pnpm verify:desktop
```

Windows 默认使用系统 Edge；其他平台默认使用 Playwright Chromium。`QA_BROWSER_CHANNEL` 可指定浏览器通道。测试构建只以 `tests/desktop-qa.html` 为入口，使用合成原生适配器，独立输出至 `evidence/desktop-qa/`；正式 `dist/` 不含测试入口。结果和截图输出到项目 `outputs/desktop-qa/`。

## 版本关系

- `desktop-ui-v1/`：保留的 V1 草稿源码快照，供回看和比较。
- `desktop-ui/`：V2 草稿预览，后续修改以此目录为准。

## V2 联调 R1（2026-10-26）

- 原生窗口使用 `createTauriApi`，统一通过 `backend_request` 和 `select_directory`；IPC 失败保持真实失败，不能退成 mock。
- 普通浏览器仍是明确标识的 MockDesktopApi 预览，不读取真实磁盘。
- 已接入来源扫描/索引、预检后确认、任务轮询/事件、历史/设置/预设、照片/RAW 分派与视频分析、缩略图、报告、审计/恢复、交付任务。
- 云端登录/Agent/账号未连接时明确报错。真实登录门禁保留；原生开发模式有独立的“本地联调工作区”，不创建云端会话，生产构建没有此入口。
- 网盘资质未获批时不显示真实连接按钮；排队不等同完成。

验证：

```sh
pnpm build
pnpm verify:adapter
```

`verify:adapter` 使用现有 `../backend/target/debug/yingxu-core`，不构建/修改后端。当前验证脚本使用 Mac 上的 `/opt/homebrew/bin/ffmpeg` 生成隔离 PNG/MP4 样本，输出保留在本地 `evidence/`（已忽略）。目录选择使用注入的固定临时路径，**不代表原生目录选择器/真实 IPC 已验收**。

`tests/browser-harness.html` 与 `browser-fixture-server.mjs` 是开发专用隔离联调工具，不在生产 HTML 入口中打包。测试服务仅监听 loopback、限定来源和临时文件夹，并使用短时随机令牌；测试结束应关闭服务、移除会话令牌文件。

详细证据、原生验收限制和给后端负责人的具体需求见 `../docs/桌面端/前后端联调-R1-2026-10-26.md`。
