# site/ · 影序官网与下载分发

纯静态站点，**零构建、零后端**：直接提交 HTML/CSS/JS，托管到 GitHub Pages 或任意静态托管。
与桌面应用完全分开构建，互不影响。

## 本地预览

```bash
cd site && python3 -m http.server 8099 --bind 127.0.0.1
# 打开 http://127.0.0.1:8099
```

## 页面

| 文件 | 内容 |
|---|---|
| `index.html` | 首页：定位、能力概览、与网盘的区别、当前状态 |
| `features.html` | 功能、格式支持矩阵、明确不做的事 |
| `download.html` | 下载（见下）、系统要求、首次打开说明、校验方法、更新策略 |
| `changelog.html` | 版本说明，直接渲染 GitHub Releases |
| `privacy.html` | 隐私政策 |
| `support.html` | 常见问题、状态说明、反馈渠道 |
| `404.html` | 兜底页 |

## 下载页怎么拿到版本

`assets/site.js` 里 `REPO = 'new-yingxu/yingxu'`，向
`https://api.github.com/repos/new-yingxu/yingxu/releases` 取列表，规则：

1. 过滤掉 draft；取第一条（含 prerelease，因为当前只发测试版）。
2. 按扩展名识别平台：`.dmg` → macOS，`.exe`/`.msi` → Windows。
3. 版本号、发布日期、文件大小、下载次数都取自 API，**不硬编码**。
4. SHA-256 从发布资产里的 `SHA256SUMS` 读取；没有就不显示，并说明原因。
5. 无 Release / API 限流 / 网络失败 → 显示「暂未发布」或提示前往 Releases，**不产生死链**。

未认证的 GitHub API 每小时 60 次（按 IP）。若站点流量上来，改用
`site/assets/releases.json` 静态快照 + CI 定时刷新，或申请 token 走服务端代理。

## 部署（GitHub Pages）

`.github/workflows/pages.yml`：push 到 `main` 且 `site/**` 有变化即部署，产物就是 `site/` 目录。

首次启用：仓库 Settings → Pages → Source 选 **GitHub Actions**。

**回滚**：
- 最快：Actions → 该 workflow 的历史成功运行 → Re-run all jobs。
- 或 `git revert` 站点改动后推回 `main`，自动重新部署。
- 自定义域名若配置错误，Pages 会回退到默认域名，不影响资产。

## 替代方案（CloudBase 静态托管）

```bash
# 一次性：安装 CLI 并登录
npm i -g @cloudbase/cli && tcb login
tcb hosting deploy site -e <env-id>          # 上传
# 回滚：控制台「静态网站托管 → 文件管理 → 版本/历史」回退到上一版本
```

切换方案时请同步更新 `docs/发布/打包与发布手册.md` 里的「站点」一节。

## 维护约定

- 站点上不写死的数字：版本号、发布日期、文件大小、下载量、校验值。
- 状态描述必须与实际一致。未签名就写未签名，未接入就写未接入。
- 新增页面记得在 6 个页面的导航与页脚里都加上链接（此站点没有模板引擎）。
- 改完在移动端视口（375px）过一遍，确认没有横向滚动。
