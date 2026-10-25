# 影序 YINGXU iOS PWA 移植框架 v1.0（草稿）

> 状态：待产品确认。本文是现有移动端 v1.0 框架的 iOS PWA 专项补充，供移动端负责人评估与实施；不代表已开发、已部署或通过 iPhone 真机验收。
> 目标：以免 iOS 原生签名的 HTTPS PWA 提供 iPhone 可安装到主屏幕的影序体验；沿用现有 Expo 移动端的视觉与业务模型，和 Windows/macOS 桌面端按统一账号/API 协作。

## 1. 先澄清发布方式

- PWA 是通过 HTTPS 访问的网站应用。用户在 iPhone Safari 中打开后，使用“分享 → 添加到主屏幕”安装为 Home Screen Web App；配置 Web App Manifest 后可独立窗口启动。该路径不生成或安装 iOS `.ipa`，不需要 iOS 应用签名，也不经过 App Store/TestFlight 审核。
- 因此“未签名”在这里指不构建 iOS 原生 App；不能把 PWA 描述成“未签名 iOS App”。PWA 仍需 HTTPS、域名/托管、Web App 图标与 manifest。项目沿用现有影序下载网页，PWA 入口和具体部署子路径在仓库核验后确定。
- PWA 不能获得与原生 Expo App 相同的相册、文件系统、后台执行和系统服务能力。此框架将其定位为账号资料/桌面任务查看、Agent 查询、用户主动选取媒体后的前台备份入口；不能承诺完整原生相册扫描或锁屏后持续大文件传输。

## 2. 产品范围与端间职责

| 场景 | iOS PWA v1.0 | 桌面 Windows/macOS | 规则 |
|---|---|---|---|
| 登录/账号 | 手机验证码、邮箱验证码；微信走官方可用的移动 OAuth/网页授权流程 | 手机/邮箱/微信登录 | 同一个人用同一个影序账号；登录能力未获服务配置前用明确的不可用状态，不做假成功 |
| 桌面备份任务 | 看电脑在线状态、阶段、速度、进度、目标结果、错误、历史和更新时间 | 创建、控制、执行本机任务 | PWA 只读，不能启动/暂停/恢复/取消桌面任务 |
| 手机照片/视频 | 用户主动打开系统文件/照片选择器并选取素材；预检后确认，在 PWA 前台上传到用户选定的百度/夸克个人网盘或受支持的目标 | 读取相机卡/磁盘；本地队列、SHA-256、质量分析 | 浏览器只处理用户选择且仍有权限的文件；PWA 关闭/被挂起时不承诺继续 |
| Agent | 查账号已同步记录、会话、缩略图、设备和本次 PWA 任务 | 在本机权限内查找/预览/操作本机文件 | PWA Agent 只管理 PWA 自己发起的手机上传；不控制桌面或桌面文件 |
| 照片预览 | 查看用户授权同步的最长边约 512 px 缩略图；可关闭同步 | 保留原片并生成可选缩略图 | 缩略图不等同原片，不把桌面路径当成可打开链接 |
| 废片处理 | 查看同步记录/缩略图与建议；不能删除桌面文件 | 候选放入待处理隔离目录；确认后移入系统废纸篓 | 原片不动；PWA 不远程执行桌面清理 |
| 通知 | 安装到主屏幕并获准后，可选择接收备份完成/失败等 Web Push | 桌面通知、菜单栏/托盘状态 | Push 是提醒，不是实时进度传输或任务运行保证 |

### 不纳入 iOS PWA v1.0 的原生能力

- 后台持续扫描整个相册、直接监听插入的相机卡、后台常驻复制/上传、大型本机文件数据库和 FTS5 全盘检索。
- PWA 通过桌面完整路径打开本机/电脑文件，或远程操纵桌面 Agent/备份队列。
- 依赖原生模块的 TCP、Expo Media Access、iOS Keychain 直接调用及原生后台任务 API。Web 端必须使用各自的安全 Web adapter；不支持时明确禁用。
- 自动把浏览器选择的原片/视频同步至影序自有云存储。用户选择上传的目标只允许本人授权的百度/夸克官方 API；OAuth/API 权限未就绪则标为待接入。

## 3. iOS PWA 页面与用户流程

### 页面

- **概览**：最近任务、手机任务入口、已配对电脑在线状态、上次同步时间。
- **备份**：选择照片/视频、检查文件数量/大小/类型、选择已授权目的地、命名预览、确认、前台传输进度、哈希/服务端校验结果、失败重试。
- **记录**：手机与桌面任务历史；逐目的地显示成功/失败/部分完成，支持查看可追溯报告。
- **照片**：本机本次选择清单和已同步压缩缩略图；展示源设备/时间；不呈现“原片已云端保存”的误导状态。
- **Agent**：读取同步记录和会话；仅生成/管理手机 PWA 自身备份任务。任何上传/改名等副作用先展示文件、目的地和影响，再由用户确认。
- **我的**：账号/设备、隐私开关、通知、主题、数据导出与删除、安装指引、注销。

### 手机备份流程

1. 用户主动点“选择素材”，通过 iOS 浏览器文件/照片选择器选取照片或视频；用户未选取的系统照片库内容不可见。
2. 页面解析浏览器提供的文件名、大小、类型等信息；无法读取的元信息不得阻止素材备份，清楚显示预检失败项。
3. 检查网络、目标授权状态、文件冲突及当前可用空间（API 可查询时）；展示旧名/新名和目标位置。
4. 用户确认后，在前台开始单文件/分片上传；显示当前文件、字节进度、速率和目的地状态。
5. 以 SHA-256 或目的地 API 提供的等价校验能力核对传输结果。用户要求的冲突规则继续适用：同名同内容跳过，同名不同内容保留双方并给新文件加序号，不覆盖。
6. 单个网盘失败时保留其他目标和已完成状态，只重试失败目标。PWA 被切走、系统挂起或页面关闭后显示“暂停/待恢复”，重开后由服务端查询状态并在前台继续；只有目标 API 支持可恢复分片时才显示断点续传。
7. 用户可撤销网盘授权、删除同步记录与 Agent 会话；注销时清除账号云端同步资料，不删除本地原片或网盘原片。

### 微信登录体验

- iPhone 上不能用当前手机摄像头扫描显示在同一屏幕的二维码。若开放平台具备官方移动网页授权，优先走系统浏览器 OAuth/深链回跳；二维码仅作为跨设备登录备选。
- 微信开放平台应用类型、回调域、Universal Link/关联域名和账号绑定策略需实际核验；能力未具备时隐藏/标注暂不可用，不以模拟页面冒充完成授权。

## 4. 技术方案

### 4.1 Web 构建与部署

- 优先评估在现有 Expo Router + React Native + TypeScript 工程启用 Expo Web 导出和响应式页面，复用设计 token、DTO、表单/状态组件和业务校验；建立 Web 专用 entry/adapter，将 Native-only 模块隔离。若原生依赖阻碍稳定构建，再把 PWA 作为同仓库独立 web workspace，而不复制整套业务逻辑。
- Expo Web 需显式配置 PWA：使用 `npx expo export -p web` 生成静态文件，并通过 Workbox 等工具生成/注册 Service Worker；不能假设 Expo 导出默认就完成了离线缓存、安装元数据和更新策略。[Expo PWA 指南](https://docs.expo.dev/guides/progressive-web-apps/)
- 复用影序现有 HTTPS 网站作为入口/托管候选；确认 GitHub Pages 的实际仓库、base path、缓存头和回退路由后，再确定应用 URL。Service Worker scope 限定在 PWA 子路径，不能误拦截原下载站、隐私页或其他站点资源。
- FastAPI 使用独立 HTTPS/WSS API 域名；配置精确 CORS allowlist、Cookie/CSRF 策略、OAuth 回调、WebSocket Origin 校验和 CSP。GitHub Pages 只托管静态文件，不承载 API 密钥或账号数据服务。

### 4.2 组件与数据层

| 层 | 方案 | 边界 |
|---|---|---|
| UI | Expo Web/React Native Web 优先；必要时使用语义化 DOM 组件做 Web 专属页面 | 复用品牌与任务字段，按 iPhone 窄屏、安全区和动态字体适配 |
| PWA Shell | Web App Manifest、独立启动 display、Apple Touch Icon、Service Worker、安装指引 | HTTPS 必需；manifest id/start URL/scope 固定后纳入版本管理 |
| 网络 | FastAPI HTTPS REST + WSS；前台 WebSocket 增量、断线后快照再补序号 | WSS 断开时显示离线/最后更新时间，不伪造实时 |
| 登录 | 短时 access session；推荐 HttpOnly + Secure + SameSite Cookie 与 CSRF token；OAuth 使用 state/nonce/PKCE（服务端按实际 IdP 能力验证） | 令牌不写 URL、日志、localStorage、IndexedDB 或 Service Worker cache |
| 本地缓存 | IndexedDB 保存小型任务/记录快照与 outbox；Cache API 缓存静态 shell | 浏览器缓存可被系统清除/回收；服务端仍是同步记录权威来源，不存原片作离线保证 |
| 上传 | 前台分块/签名 URL/续传会话由 FastAPI 与网盘官方 API 协作 | 分片和续传能力逐家核实；浏览器挂起/关闭时上传可能中断 |
| 推送 | Web Push + Service Worker；iOS 仅在主屏幕 Web App 上启用，用户主动操作触发授权 | 功能检测、用户可关闭；通知只提醒，不承载敏感文件路径/照片内容 |
| 主题 | 统一移动端 token、系统/浅色/深色主题 | 核心品牌色 `#273D31`，浅色底 `#F4F2ED`；状态同时用文字/图标表达 |

### 4.3 本地媒体和文件能力

- 文件访问只通过用户主动选择产生的 `File`/授权句柄；不枚举未选择的相册、iCloud Drive 或外接存储。用户重新进入选择器前，不假定上次选择句柄永久有效。
- 原片仅临时驻留浏览器传输缓冲区；不放入 Cache API/Service Worker precache。避免默认将大文件完整复制到 IndexedDB 或 OPFS；浏览器配额和清理行为不可作为安全备份承诺。
- 相册照片/视频的 EXIF、HEIC、RAW、Live Photo/sidecar 识别能力逐项真机验证。不能解析的文件仍允许原样上传给用户选择的目的地，标注“未分析”；若浏览器无法可靠读取/上传某格式则反馈可复现错误并保留源素材。
- iOS 文件系统 API 即使可用，其私有 origin 存储也不是用户可随时访问的普通文件夹，不等于原生 app 的文件系统授权。[WebKit File System API](https://webkit.org/blog/12257/the-file-system-access-api-with-origin-private-file-system/)
- PWA 的图像分析只做可选轻量预览；首版主要读取桌面同步来的质量标签/缩略图。云端视觉复核仍须逐张选片并逐次确认；上传原片给视觉模型的流程需清楚显示模型服务商与数据流。

## 5. iOS PWA 权限和能力矩阵

| 能力 | v1.0 策略 | 验收要求 |
|---|---|---|
| 安装到主屏幕 | Safari 分享菜单添加；manifest `display: standalone`，应用名/图标正确 | 新装、更新、移除、重新添加后路径和登录状态可预期 |
| HTTPS | 全站 HTTPS；无混合内容 | iOS Safari 与主屏幕模式均通过证书/资源加载检查 |
| 选取照片/视频 | 用户手动触发文件选择；只访问已选文件 | 多选、取消、权限撤销、HEIC/RAW/视频格式在支持设备测试 |
| 后台大文件上传 | 不承诺；PWA 前台上传，离开页面可能暂停/断开 | 切后台/锁屏/杀页后清楚呈现状态；重开能安全恢复或要求重新选文件 |
| 桌面实时进度 | 前台 WSS，回前台重新同步快照 | 断线、网络切换、序号缺失和服务端离线均有状态说明 |
| Web Push | iOS 16.4+ 的主屏幕 Web App 可选支持；需要用户手势触发授权 | 不在 Safari 普通标签页假设可用；拒绝/关闭通知时主流程仍可用 |
| 摄像头/扫码 | v1.0 不要求相机连续扫描；需要时用系统选择器/官方链接 | 不把二维码登录设为同屏唯一登录方式 |
| 本机安全存储 | Cookie/浏览器托管凭据；敏感长期密钥由后端托管 | 不把百度/夸克 client secret 或模型 Key 发到浏览器 |
| 离线 | 只提供有限静态 shell 和最近非敏感记录快照 | 不把离线缓存当作备份原片或权威任务结果 |

## 6. 隐私与 Agent 安全

- 同步范围与移动端框架一致：备份记录、Agent 对话/动作摘要必需；压缩缩略图最长边约 512px、电脑名/完整路径分别可关闭；原始照片/视频、访问令牌、模型 Key、未授权 GPS/EXIF 不进影序云端。
- PWA 中的桌面完整路径只作为文本查看，不可点击打开或用于文件操作。桌面文件只有桌面本地 Agent 能访问。
- iOS PWA Agent 工具仅包括查询账号同步记录、查看可授权缩略图/路径和管理当前 PWA 的手机上传任务。服务端必须按 `device_id`/capability 拒绝桌面任务写操作，不能只靠前端隐藏按钮。
- 改名、移动、上传、清理等副作用先展示具体对象、目标与影响并由用户确认。iOS PWA v1.0 不提供桌面文件改名/移动/清理按钮。
- 云端会话、缩略图和路径按个人敏感数据传输和授权；用户可手动删除，注销触发账号云端同步资料删除。明确显示删除处理中/已完成，不能只在前端清除列表。

## 7. 阶段实施与验收

| 阶段 | 内容 | 完成条件 |
|---|---|---|
| P0 可行性 Spike | iPhone Safari/主屏幕模式、Expo Web 构建、manifest、Service Worker scope、登录回跳 | 真实设备安装/启动可用；服务 Worker 不影响旧网站；确认 Native-only 依赖可隔离 |
| P1 只读体验 | 登录、桌面在线/任务快照、历史、Agent 会话、缩略图/路径设置 | WSS 断连恢复、路径只读、隐私开关和空/失败状态可验收 |
| P2 前台手机备份 | 文件选择、预检、确认、上传进度、部分完成、哈希校验/错误处理 | 逐格式、弱网、锁屏、切后台、页面回收、重新打开、授权过期真机测试 |
| P3 Agent/通知/云盘 | 手机任务 Agent 工具、Web Push、百度/夸克 OAuth 上传 | Web Push 仅 Home Screen 场景验收；API 权限未获批则保持禁用/待接入 |
| P4 发布准备 | 原下载网站入口、安装说明、隐私声明、Service Worker 更新与回滚 | 更新/缓存清理可恢复；明确“不上 App Store、不是 IPA”的发布说明 |

### 关键验收项

- iPhone 真机通过 Safari 添加到主屏幕，名称、图标、standalone 启动、深链登录回跳和版本更新正常。
- 从系统照片选择器选择文件才开始扫描；取消/权限撤销时没有后台读取；所有任务写入前显示目的地和命名预览并要求确认。
- PWA 无法控制桌面任务；桌面离线显示最近同步快照与时间，恢复后补齐记录。
- 上传遇断网、切后台、锁屏、Safari 退出、网盘授权失效、单目的地失败时，不丢失源文件、不报告虚假成功；无法续传时说明需重新选择/继续。
- 服务 Worker 更新不永久卡旧版；清理站点数据后可重新登录和恢复服务端记录；私密内容不放入静态缓存。
- iOS Web Push 授权仅在用户操作后请求，通知拒绝不阻塞查看记录与前台备份。
- 质量指标仍按照片/视频分别评测：候选精确率 ≥98%、可用素材误报 ≤1%、明显废片召回 ≥80%；iOS PWA 首版不承诺执行完整设备端视频分析。

## 8. 主要风险与决定点

1. **免签名不是免托管**：需要 HTTPS 站点和应用入口；GitHub Pages 是否可继续承载当前子路径/缓存策略需检查。
2. **PWA 不等于 Expo 原生端**：相册选择、长时上传、原生后台、SecureStore/TCP 等能力有差异；必须先做 P0 设备验证，再估计范围。
3. **上传可靠性受 iOS 生命周期制约**：如果要求锁屏/退到后台后长时间持续备份，纯 PWA 不满足强保证；需保留原生 iOS App 方案或调整产品承诺。当前用户选择是先走 PWA，因此首版按前台上传设计。
4. **手机微信登录依赖授权类型**：二维码不是单机手机的合适主流程；需要确认微信开放平台是否批准移动网页 OAuth/深链方案。
5. **跨端实时信息需后端支持**：GitHub Pages 只负责静态站点；账号、WebSocket、缩略图、任务记录和云盘凭据仍由 FastAPI/CloudBase/官方云盘 API 承担。

## 参考资料

- [Expo PWA 指南](https://docs.expo.dev/guides/progressive-web-apps/)
- [WebKit：iOS/iPadOS 主屏幕 Web App 与 Web Push](https://webkit.org/blog/13878/)
- [Apple Safari：配置 Web Applications](https://developer.apple.com/library/archive/documentation/AppleApplications/Reference/SafariWebContent/ConfiguringWebApplications/ConfiguringWebApplications.html)
- [WebKit：File System API 与 Origin Private File System](https://webkit.org/blog/12257/the-file-system-access-api-with-origin-private-file-system/)
- [Apple：调试 iOS/iPadOS Home Screen Web Apps](https://developer.apple.com/documentation/safari-developer-tools/inspecting-ios)

