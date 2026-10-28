# 影序 YINGXU · Agent 智能影像资料管理

面向摄影师的照片导入、备份、校验、整理、检索的桌面应用。AI 定位为 L3 Agent 驱动，本地文件操作由确定性代码执行，模型只调用白名单工具。

## 仓库结构

| 目录 | 内容 | 状态 |
|---|---|---|
| `backend/` | Rust 本地核心（`core/`）+ Tauri 宿主（`tauri-host/`） | **33 项测试通过**（21 集成 + 6 media + 6 api/delivery） |
| `desktop-ui/` | Vue 3 + TypeScript + Vite 桌面前端（V2） | 界面已确认，接 Mock，`vite build` 通过 |
| `desktop-ui-v1/` | V1 前端源码快照（升级 V2 前留档） | 仅回看 |
| `cloud-service/` | FastAPI 云端服务 + Agent 边界（源码唯一真源） | 11 个 REST 路由 + WS，本地烟测通过 |
| `infra/cloud/` | CloudBase 部署上下文生成脚本、SQL schema | `build/` 由脚本生成 |
| `contracts/` | 桌面 ↔ 云端共享协议 schema（v1） | 基线已冻结，改动需提变更请求 |
| `docs/` | 功能/技术框架、协作交接、品牌、移动端 | 见 `docs/README.md` |

## 快速开始

```sh
# 本地核心（macOS 需指定 SDK，系统默认 27 与当前链接器不兼容）
SDKROOT=/Library/Developer/CommandLineTools/SDKs/MacOSX26.5.sdk \
  cargo test --manifest-path backend/Cargo.toml

# 前端
cd desktop-ui && pnpm install && pnpm dev     # http://127.0.0.1:5173

# 云端（开发态，需显式 dev token；生产不接受开发认证）
cd cloud-service && pip install -r requirements.txt
APP_ENV=development AUTH_MODE=dev DEV_AUTH_TOKEN=xxx \
  DATABASE_URL=sqlite:///./dev.db uvicorn app.main:app --reload

# 生成 CloudBase 部署上下文
python3 infra/cloud/prepare_context.py          # → infra/cloud/build/
python3 infra/cloud/prepare_function_context.py # → infra/cloud/function-build/
```

## 环境

- CloudBase 环境：`yingxu-dev` / `yingxu-dev-d8gowpye17ee27a61`（体验版，到期 2027-04-25）
- 本机尚无 `tcb` CLI / Docker / 腾讯云密钥，真实部署前需先配置

## 边界

- 原片不进影序云端；只同步任务摘要、对话和可关闭的缩略图/路径
- 备份必须完全离线可跑；云端不可用不阻断本地复制与校验
- Agent 只生成提议，写操作需桌面端本地二次确认
- 每项目标结果独立，部分成功不得显示为全成功
