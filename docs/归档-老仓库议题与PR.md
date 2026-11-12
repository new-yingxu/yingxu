# 归档：yingxu-app/smart-media-backup 的议题与 PR

> 老仓库与新组织已合并删除，此文件保留当时的议题/PR 文本作为追溯依据（不含评论）。

## [PR #23] build(deps): bump node-forge from 1.4.0 to 1.4.2 in /mobile
- 状态：closed · 创建：2026-11-02 · 作者：dependabot[bot]

Bumps [node-forge](https://github.com/digitalbazaar/forge) from 1.4.0 to 1.4.2.
<details>
<summary>Changelog</summary>
<p><em>Sourced from <a href="https://github.com/digitalbazaar/forge/blob/main/CHANGELOG.md">node-forge's changelog</a>.</em></p>
<blockquote>
<h2>1.4.2 - 2026-10-27</h2>
<h3>Fixed</h3>
<ul>
<li>[pem] Convert non-string input to a string in <code>pem.decode()</code> as before
1.4.1. The 1.4.1 PEM ReDoS fix (GHSA-7wch-p9px-956x) caused a <code>TypeError</code>
for <code>Buffer</code> input, which is used by <code>node-jose</code>.
(<a href="https://redirect.github.com/digitalbazaar/forge/issues/1165">#1165</a>)</li>
</ul>
<h2>1.4.1 - 2026-10-27</h2>
<h3>Notes</h3>
<ul>
<li>This release involved coordinating many security reports from many groups,
people, and robots over too long period of time. Thank you for your reports
and patience.</li>
<li>Claude Code was used to help organize, track, untangle, merge, test,
validate, fix, and improve it all. Further verification of fixes would be
welcome.</li>
<li>Tracking credits was challenging for some issues due to multiple email and
GitHub reports, incomplete contact details, or combining of partial reports
and fixes. Please let us know if there are credits changes needed here or in
advisory data.</li>
</ul>
<h3>Security</h3>
<ul>
<li><strong>HIGH</strong>: Signature forgery in RSA-PKCS due to ASN.1 extra field in
DigestAlgorithm.
<ul>
<li>RSASSA PKCS#1 v1.5 signature verification accepts forged signatures for low
public exponent keys (e=3). The fix for CVE-2026-33894 only checked the
element count of the outer DigestInfo. Attackers can add &quot;garbage&quot; elements
inside the nested DigestAlgorithm SEQUENCE in order to construct a
signature that passes verification, enabling Bleichenbacher style forgery.</li>
<li>Reported by Geo (<a href="https://github.com/geo-chen"><code>@​geo-chen</code></a>).</li>
<li>CVE ID: <a href="https://www.cve.org/CVERecord?id=CVE-2026-85393">CVE-2026-85393</a></li>
<li>GHSA ID: <a href="https://github.com/digitalbazaar/forge/security/advisories/GHSA-2g46-22mc-6gwp">GHSA-2g46-22mc-6gwp</a></li>
</ul>
</li>
<li><strong>HIGH</strong>: Signature forgery in RSA-PKCS due to non-canonical DigestInfo
encoding.
<ul>
<li>RSASSA PKCS#1 v1.5 signature verification accepts non-canonical DigestInfo
encodings, such as NULL parameters with content, non-minimal length
encodings, and non-canonical OIDs. These provide attackers space to
construct forged signatures for low public exponent keys (e=3), similar to
CVE-2026-33894 and CVE-2026-85393.</li>
<li>Reported by Oren Yomtov (<a href="https://github.com/orenyomtov"><code>@​orenyomtov</code></a>, Fireblocks), Bronson Yen (Calif.io,
in collaboration with Claude and Anthropic Research), Kamil Hałajda,
<a href="https://github.com/pzartech"><code>@​pzartech</code></a>, <a href="https://github.com/antonlissone"><code>@​antonlissone</code></a>, and <a href="https://github.com/Brechard"><code>@​Brechard</code></a>.</li>
<li>CVE ID: <a href="https://www.cve.org/CVERecord?id=CVE-2026-119192">CVE-2026-119192</a></li>
<li>GHSA ID: <a href="https://github.com/digitalbazaar/forge/security/advisories/GHSA-6qrw-m8cv-hcxf">GHSA-6qrw-m8cv-hcxf</a></li>
</ul>
</li>
<li><strong>HIGH</strong>: ASN.1 resource exhaustion via BIT STRING decoding.
<ul>
<li>An Uncontrolled Recursion (CWE-674) and Uncontrolled Resource Consumption
(CWE-400) vulnerability in node-forge versions 1.4.0 and below enables
remote, unauthenticated attackers to exhaust CPU and memory with small
crafted DER or PEM inputs (certificates, keys, and other structures parsed</li>
</ul>
</li>
</ul>
<!-- raw HTML omitted -->
</blockquote>
<p>... (truncated)</p>
</details>
<details>
<summary>Commits</summary>
<ul>
<li><a href="https://github.com/digitalbazaar/forge/commit/7928054627d93ab8c0216b756246e8ca8d62a93e"><code>7928054</code></a> Release 1.4.2.</li>
<li><a href="https://github.com/digitalbazaar/forge/commit/1d8c2459ad95d7ae7535702d1e55c173773e7b11"><code>1d

## [PR #22] Add Android Pocket v1.0.0 download to the website
- 状态：closed · 创建：2026-10-30 · 作者：nanyu-lu

## Summary
- Replace the internal-only Android placeholder with a public download card for the existing Pocket Android v1.0.0 Release.
- Link the published standalone APK and its SHA256SUMS.txt.
- Disclose that API 35 emulator UI checks passed while physical-device, authenticated sync, media operations, and Google Play acceptance remain unverified.

## Verification
- Release asset SHA-256: `17c3a18efa4beea15bf8b7a8f308fb9f9022c9571c8f04ef431378cf20856cca`.
- APK and checksum asset are published in `yingxu-app/smart-media-backup` Release `pocket-android-v1.0.0`.

## [PR #21] Keep only the latest Windows download on the website
- 状态：closed · 创建：2026-10-30 · 作者：nanyu-lu

## Summary
- Remove the older Windows v1.0.34 download card from the website.
- Keep only the newer Windows x64 v0.2.0-draft.4 prerelease, with its unsigned/preview warnings and SHA-256 links.
- Leave the macOS download section unchanged.

## Version comparison
- v1.0.34 Windows stable Release was published earlier (2026-10-25; installer asset created 2026-09-12).
- v0.2.0-draft.4 Windows desktop prerelease was published 2026-10-30 and is the newer Windows package by publication date. It is an unsigned development preview, not a formally accepted stable release.

## [PR #20] Add Windows v0.2.0-draft.4 preview download
- 状态：closed · 创建：2026-10-30 · 作者：nanyu-lu

## Summary
- Add the Windows x64 v0.2.0-draft.4 desktop prerelease to the official download page.
- Keep the stable Windows v1.0.34 download separate and label the preview as unsigned.
- Link the matching installer, SHA256SUMS, and GitHub prerelease; disclose SmartScreen and missing Windows UI acceptance.

## Verification
- Confirmed the GitHub Release is published as a prerelease and the installer SHA-256 matches the downloaded asset.
- Website deploy workflow runs on merge to `main`.

## [PR #19] chore(repo): 整理目录结构与文档，移出 92 MB 构建产物
- 状态：closed · 创建：2026-10-25 · 作者：GJH-c

## 这个 PR 做什么

把仓库从「AI 干活干出来的工作台」收敛成「别人能看懂的公开仓库」：文件 **430 → 193**，工作区 **95.5 MB → 2.2 MB**。

### 1. 移出 92 MB 构建产物（P0）

`dist/yingxu-pocket-android-preview-1.0.0.apk` 占了仓库体积的 92%，而 `.gitignore` 里本来就写着 `dist/`——它是被 `git add -f` 强制加进去的。现在只取消跟踪，APK 的取用方式写进 `docs/ANDROID-PREVIEW.md`（Release / Actions artifact）。

### 2. 移出 241 个过程文件（P0）

| 目录 | 内容 | 数量 |
| --- | --- | --- |
| `docs/snapshots/` | 67 个 AI 会话目录，逐个保存"改之前"的副本 | 226 |
| `docs/site-history/` | 官网改版前的 HTML 副本 | 15 |

其中 170 个是 `*.before-*` 残留。内容仍在 git 历史里，恢复方法写在 `docs/archive/README.md`。

### 3. 文档按用途分层

```text
docs/
├── README.md            新增：文档地图（想用 / 想懂 / 想验收 / 想参赛）
├── ARCHITECTURE.md…     使用说明与技术文档留在根目录
├── releases/            v1.0.24 → v1.0.40 版本与候选验收记录（+ 索引）
├── reports/             跨端审计、闭环计划、验收过程
├── product/             比赛材料、演示脚本
└── archive/             归档说明与找回命令
```

### 4. 根目录脚本归位

`e2e_win_check.py`、`smoke_yingxu_win.py` 移入 `scripts/`。**顺手修了移动后必崩的路径定位**——不改就是 CI 里 `ModuleNotFoundError: smb`：

| 文件 | 改动 |
| --- | --- |
| `scripts/e2e_win_check.py` | `ROOT = dirname(__file__)` → `dirname(dirname(__file__))` |
| `scripts/smoke_yingxu_win.py` | 去掉失效的 `ROOT/"smart-media-backup"`，改为插入仓库根 |

`build-windows.yml` 里 5 处引用（2 处路径过滤 + 3 处执行命令）已同步。

### 5. 文档与护栏

- 重写 `README.md`（产品定位 → 三步 → 安全边界 → 平台现状表）、`AGENTS.md`（对齐现状，不再抄一份会过时的路由表）、`.gitignore`（挡 `*.before*`）
- 新增 `scripts/check_links.py`，并作为 `Reliability tests` 的一个步骤接入 CI
- 新增 Bug / 功能建议 Issue 表单（要求版本、系统、脱敏日志、失败时如何保住原卡）

## 验证

在完整镜像上（含真实 APK）实际跑过一遍：

- 文件守恒 430 − 242 − 3 + 8 = 193；21 个移动全部被识别为 rename
- `scripts/check_links.py`：41 个 md，**0 断链**
- `python -m unittest discover -s tests`：**136 通过 / 1 跳过**
- `compileall` 通过；6 个 workflow + 3 个 Issue 模板 YAML 全部可解析
- 移动后的两个脚本 `ROOT` 解析到仓库根，`import smb` 成功

## 合并后要接着做

1. **先把 APK 挂到 pre-release**（仓库里已经没有它了）：
   `gh release create android-preview-1.0.0 --prerelease --title "Android 预览包 1.0.0（未验收）" dist/yingxu-pocket-android-preview-1.0.0.apk`
2. git 历史里仍有 92 MB，`clone` 体积不会立刻变小；要瘦身得 `git filter-repo`（会改写所有 SHA，需权衡）。
3. 15 个 `codex/*` 分支里 5 个已完全合并可删，其余 9 个需逐个确认。

## [PR #18] Android preview release
- 状态：closed · 创建：2026-10-25 · 作者：yangjili6

_（无正文）_

## [PR #17] fix: query and display Baidu storage quota
- 状态：closed · 创建：2026-10-21 · 作者：nanyu-lu

Query Baidu storage quota after authorization and display total, used, and available capacity in Settings, with a refresh button. Network/API failures are shown as errors instead of zero capacity.

The quota response uses `total` and `used`. Available capacity is calculated as `max(0, total - used)`; the upstream `free` field describes the free-tier allowance and must not be displayed as unused storage. Field definitions were checked against Baidu's own MCP documentation: https://github.com/baidu-netdisk/mcp#用户基础信息-获取网盘容量信息.

Only four files change: quota client, server error response, Settings UI, and changelog. Live account verification remains assigned to the separate acceptance session. `git diff --check` passed; repository CI checks the submitted head.

## [PR #16] docs: retain GitHub Pages and ChatGPT Sites hosting only
- 状态：closed · 创建：2026-10-21 · 作者：nanyu-lu

Keep GitHub Pages as the primary website and ChatGPT Sites as the backup. Remove obsolete hosting references from the current deployment guidance and audit records; add one canonical hosting document. Documentation only; git diff --check passed.

## [PR #15] Website: unify YINGXU pages and Pocket console design
- 状态：closed · 创建：2026-10-21 · 作者：nanyu-lu

## Summary
- Apply the approved warm paper, graphite, moss green and lime visual system across the home page, download, update, privacy, 404 and Pocket console pages.
- Update Pocket PWA theme colors and bump the cached shell to v5 so installed clients fetch the redesigned shell.
- Preserve Pocket application scripts and all existing routes and behavior.

## Verification
- Local routes and Pocket resources returned HTTP 200.
- Desktop browser visual review covered home, download, update, privacy, 404 and Pocket login/transfer screens.
- This PR contains only `site/` files. No account credentials were entered; account sync and real-device transfer were not part of this visual deployment check.

Deploys to GitHub Pages on merge to `main`.

## [PR #14] Add Qwen, DeepSeek, and OpenRouter vision providers
- 状态：closed · 创建：2026-10-21 · 作者：nanyu-lu

## Summary
- Add Qwen, DeepSeek, and OpenRouter vision-provider presets for event naming and photo-quality suggestions.
- Store each provider key in the OS credential vault; fix provider endpoints and restrict settings/test endpoints to loopback.
- Add a generated 1x1 image connection probe, setup/privacy guidance, and credential/provider regression tests.
- Keep duplicate detection local (SHA-256/perceptual hash); single-image AI does not classify cross-image duplicates.

## Verification
- `python -m unittest discover -s tests`: 136 passed, 2 Windows-only skipped.
- Inline settings JavaScript syntax check passed.
- macOS PyInstaller build succeeded; archive inspection found `openai` and macOS keyring backend.
- No provider API key exists in this environment, so no live provider call was made.

## Release note
This PR does not publish a public release. Real provider account/API validation and existing device/account release gates remain outstanding.

## [PR #13] docs: record PR 12 merged deployment evidence
- 状态：closed · 创建：2026-10-21 · 作者：nanyu-lu

## Summary\n- Record PR #12 merge and post-merge workflow results.\n- Record live Pages route checks and latest candidate artifact verification.\n- Keep Firebase and iPhone PWA device acceptance as external gates; v1.0.40 remains a draft.\n\n## Verification\n- Documentation-only change;  passed.\n- PR #12 code-head CI and merge-push CI completed successfully.\n- Pages routes returned HTTP 200 after deployment.

## [PR #12] feat: add resumable PWA phone backup and Windows VM acceptance
- 状态：closed · 创建：2026-10-21 · 作者：nanyu-lu

## Summary
- Add an iPhone Safari PWA handoff for user-selected photos/videos to the paired desktop Pocket page, with progress controls and a local IndexedDB queue/retry.
- Add Windows x64 VM checks for the packaged service, native pywebview UI, legacy history compatibility, upgrade and rollback.
- Fix CP1252 console output handling for threaded Windows service logs and capture regression coverage.
- Record implementation boundaries, CI evidence, candidate artifact hashes, and remaining account/device acceptance in the closure docs.

## Verification
- Windows x64 runner: isolated backup 20/20, packaged v1.0.40 service 21/21, native UI 8/8. Covers scan/copy/hash, duplicate skip, report/history, source preservation, and UI navigation.
- v1.0.34 database schema fixture migrates and preserves history. The actual old v1.0.34 frozen EXE still fails to start its server on CP1252 because its localized startup banner crashes its thread; this is documented and is not claimed as fixed in that old binary.
- Windows preview and macOS candidate artifact ZIP hashes match GitHub metadata; Windows EXE matches its manifest and is x64 PE. DMG verification and app signature integrity checks passed; app is ad-hoc signed without Team ID.
- Reliability PR/push, macOS, Windows, Pocket and Android workflows are being checked on the latest documentation head; see the PR checks for terminal results.

## Acceptance boundaries
- No real Firebase account registration/login or cross-account isolation test has been performed.
- iPhone Safari photo/video transfer, interruption/recovery and desktop hash/history comparison still need device operation.
- PWA does not guarantee continuous lock-screen transfer, silently scan the full photo library, or write arbitrary phone paths. Raw media is not uploaded to Firebase.
- Public Latest remains v1.0.34. v1.0.40 remains a candidate and is not formally released pending real Firebase and iPhone PWA acceptance.

## [PR #11] docs: refresh post-merge closure evidence
- 状态：closed · 创建：2026-10-21 · 作者：nanyu-lu

## Summary\n- Record merged PR #9/#10, completed post-merge CI and Pages checks, and Firebase endpoint probes.\n- Record Windows x64 VM scope and remaining iPhone/Firebase gates.\n- Correct the Windows preview workflow release note to refer to VM acceptance.\n- Preserve the prior closure and audit documents under docs/snapshots.\n\n## Validation\n- YAML workflow parsed successfully.\n- git diff --check passed.\n- Existing merged-head Pocket/Android and Reliability runs are terminal success; no rerun was started for them.\n\nThis does not publish v1.0.40; it remains Draft pending real Firebase and iPhone PWA acceptance.

## [PR #10] CI: run an isolated backup flow on Windows
- 状态：closed · 创建：2026-10-20 · 作者：nanyu-lu

## Summary
- Fixed the Windows E2E script's repository path and isolated its config, synthetic source card, backup target, and report under runner temporary storage.
- Added a Windows hosted backup flow test that runs after the Windows preview EXE is built.
- Added deterministic `YINGXU_E2E_HEADLESS=1` service mode for the packaged EXE. Normal launches still use the pywebview desktop window.
- Preserved before-snapshots and updated the closure plan with exact run evidence and coverage limits.

## Verification
- Current PR head `7558c41adc9cc47359cb7dbf84bb75dda11e5953` is docs-only after code head `bbfb942a83fe2c4d9412c4f6c3c0ac788cbbc560`. Current-head Windows preview run `39452224534`, macOS candidate run `39452224552`, and Reliability runs `39452213060` / `39452224532` all succeeded.
- On exact code head `bbfb942`, Windows preview run `39451574588` built the v1.0.40 EXE, launched that package in isolated service mode, confirmed `/api/health` HTTP 200 and version `1.0.40`, then passed 21 packaged-service E2E assertions for scan, invalid-source rejection, copy, SHA-256 verification, duplicate skipping, history/reports, and source preservation. x64 PE, unsigned status, checksum manifest, and artifact upload also passed.
- Current-head Windows artifact `12277958676`: 27,879,784 bytes, Actions metadata digest `sha256:dfcbbe3022e06243472626f3c938889788f4eb164474f38005ae47640c32bfe4`. E2E report artifact `12278433281`: 1,972 bytes, digest `sha256:53fe9e4b3f878259d5fa9da5def3e5d6156f34ba47b53639e44df87a9562af84`.
- Earlier GUI-mode probes did not provide reliable runner evidence; the final passing test explicitly covers the packaged service, not pywebview GUI interaction.

This is Windows-hosted runner evidence with synthetic files. It does not establish GUI interaction, installation/upgrade on the user's Windows PC, or physical card-reader behavior. Keep this PR in draft until the remaining acceptance gates are resolved.

## [PR #9] feat: add Pocket phone backup and verified updates
- 状态：closed · 创建：2026-10-20 · 作者：nanyu-lu

## Summary
- Add a paired iPhone Safari / Pocket page flow to select photos, RAW files, videos, or Files folders and back them up to the desktop over the same Wi-Fi.
- Scan selected media with incremental SHA-256, skip repeated content, check target disk capacity, upload in bounded chunks, and verify the complete file before saving it into desktop history. Preserve source files and clean failed partial uploads.
- Add phone-side progress, pause/resume/cancel controls, recent history, and desktop Preferences progress visibility.
- Add verified update-package download in Preferences. The desktop downloads the platform release asset, checks size and SHA-256, and only then serves it to the browser for manual installation; no running app is silently replaced.
- Update the Pocket, privacy, download, update, and closure documentation. Keep the platform boundaries explicit: iOS is a PWA, no native iOS or mini program, no Android device acceptance, and Windows is tested on a Windows x64 hosted VM.

## Verification
- Local Python reliability suite: 121 tests, 119 passed, 2 Windows-only tests skipped on macOS.
- Pocket cloud-sync + SHA-256 Node tests: 10/10 passed.
- Targeted upload/protocol/update Python tests: 24/24 passed.
- Local Pocket inline JavaScript syntax, app JS syntax, and `git diff --check` passed.
- PR #10 was merged first; this branch includes its Windows x64 packaged-service E2E workflow. The current branch head must pass fresh checks, including its Windows runner build and packaged backup flow, before merge.
- Public Pages routes `/`, `/pocket/`, `/download/`, `/privacy.html`, and `/update/` returned HTTP 200 in the last live check. The new site content still needs deployment after merge.

## Acceptance boundaries
- The Windows hosted runner verifies the packaged EXE service and synthetic backup behavior. It does not verify the pywebview GUI, physical SD reader, or a user Windows install; physical Windows testing is explicitly out of scope.
- PWA transfer requires a paired desktop on the same Wi-Fi and the page to remain active. Browser restrictions prevent guaranteed lock-screen copying, silent full-library/external-drive scanning, arbitrary local-folder writes, and reliable offline transfer queuing.
- A real iPhone transfer and Firebase account/device synchronization still need device/account evidence before the public release is promoted. Official Latest Release remains v1.0.34; v1.0.40 is still a draft candidate.

## [PR #8] fix: expose safe Firebase login diagnostics
- 状态：closed · 创建：2026-10-19 · 作者：nanyu-lu

## Summary

- Replace the desktop Firebase login fallback with a safe local exception-type diagnostic. Do not return the underlying exception text, password, or request URL.
- Add safe HTTP/TLS/timeout/network diagnostics to the desktop update check without exposing raw exception details.
- Refresh the closure plan and cross-platform audit to match the agreed scope: desktop, iPhone Safari PWA, Android CI/debug candidate, Firebase metadata sync, Windows device acceptance, and unsigned desktop release/update validation. Preserve pre-edit snapshots.

## Verification

- `./.venv/bin/python -m unittest tests.test_firebase_server -v` — 8 passed.
- `./.venv/bin/python -m unittest tests.test_update_checker -v` — 5 passed.
- `git diff --check` — passed.
- Current head `4478ff024fe37565c1c5adb8882da566068c8c56`: Reliability PR/push, macOS candidate, and Windows preview runs passed. Pocket run `39315226872` is still in progress; it was not restarted.
- macOS artifact `12218485076` ZIP size 21,079,360, SHA-256 `dd48ff3d3938f2e21169aedf97c106e6beb09254000a819d18a02c4869129c19`, matches API metadata. Inner v1.0.40 DMG SHA-256 `8ca032747a6f559ac1f2d16c17f84df65829a643936197f2ffb5c335da9d0e0d`, manifest matches, `hdiutil verify` valid; app is arm64 ad-hoc with no Team ID.
- Windows artifact `12218355386` ZIP size 27,877,449, SHA-256 `3b6bc60f33cf53f7810312ad46c7f95302d751ce6f9fd45310230e0e5568e12d`, matches API metadata. EXE SHA-256 `62382865eb0145e23f6e9d36833d7e17af72f0b6ba2569cfddf68f8444ad75f1` matches its checksum manifest; it is an x86-64 unsigned preview.
- Previous exact-head Pocket run `39310316189` completed successfully. Android APK artifact was locally downloaded and validated, but it is debug-only and there is no Android device acceptance.
- Main contains the same PWA `index.html`, `cloud-sync.js`, and service-worker Git blobs as the reviewed source. A recent Pages request returned HTTP 200 for `/pocket/`, `/download/`, `/update/`, and `/privacy.html`; later fetches intermittently hit TLS EOF.
- GitHub Latest Release API confirms v1.0.34 with a Windows EXE and `SHA256SUMS.txt`.

## Still pending

Real Firebase account registration/login and cross-device sync; iPhone PWA installation/offline restoration; Windows physical-device acceptance; publishing and reading back the official unsigned desktop release/download page; and upgrade/rollback verification. The old local desktop process returned HTTP 502 from `/api/updates/latest`. The updated source test client and the exact-head packaged macOS candidate both returned HTTP 200 and accurately report current 1.0.40 vs public 1.0.34 with no macOS asset. The package was launched on isolated port 18081 with a temporary config; `/api/status` reported 1.0.40/idle, and the isolated process/config were stopped and removed. The user’s existing process on port 8080 was not restarted, so its live UI still needs a later refresh/restart check.

Formal desktop signing/notarization, native iOS, Android device acceptance, Mini Program, browser online backup, and remote original-media transfer are excluded by the user's decisions. Android store distribution is not a completion gate in the current objective. The source fixes in this PR can be merged independently; real-account, device and release gates remain open and are not represented as complete.



## Verification update — 2026-10-20

- Current PR head `5b8f680c3680e40b470ec1750b70ffb73fdf4131`; Pocket run `39319086301` completed successfully, including the Android debug build.
- Android artifact `12221325113` metadata size 94,609,016 bytes and SHA-256 `8e28a960e4bee3f7c6f71f730db1bb703f96d92b0d446fa52421c92b3fbe57a5` match the downloaded ZIP. ZIP integrity passes. Inner debug APK SHA-256 is `37942c7fcd793f984e418df89e410fba8698078c2df7e837c2c5a3d5b30757a3`; it contains arm64-v8a, armeabi-v7a, x86 and x86_64. This remains a debug artifact, not device acceptance.
- The public Pocket route renders in a desktop browser; the standalone manifest and ac

## [PR #7] docs: reflect live PWA deployment status
- 状态：closed · 创建：2026-10-19 · 作者：nanyu-lu

## Summary

- Update the homepage and site README to reflect that the iPhone Pocket PWA is deployed at the public HTTPS Pages route.
- Keep the remaining status explicit: Firebase real-account login, iPhone home-screen installation, and offline/recovery acceptance are still pending.

## Verification

- After PR #4 merged as `40cb4dfe82e6917fe0d404b5d5a9eb8be8a7f005`, the GitHub Pages deployment run `39306460338` completed successfully.
- A real browser opened the public homepage, `/pocket/`, `/download/`, `/privacy.html`, and `/update/`; the Pocket login form and expected routes rendered.
- `git diff --check`, HTML parsing, and the local PWA status-copy assertions passed.

## [PR #6] docs(site): align macOS candidate status to v1.0.40
- 状态：closed · 创建：2026-10-16 · 作者：nanyu-lu

## Summary
- Update the public site from the stale macOS v1.0.39 candidate label to the verified v1.0.40 local candidate status.
- Keep Windows v1.0.34 as the only official download; do not publish the unsigned, unnotarized macOS candidate.
- Preserve the current main site files and add rollback copies before updating them.

## Verification
- Local HTML links and release-state copy checked.
- Browser preview confirmed the download page shows Windows v1.0.34 as official and macOS v1.0.40 as unavailable for download.
- Python suite: 76 passed, 2 Windows-only skips; mini-program Node tests: 4 passed.
- Reliability run 38924060909 passed all six OS/Python matrices on the source PR branch.

After merge, verify the GitHub Pages deployment and read back the public page with a cache-busting query.

## [PR #5] docs(site): align platform status with v1.0.39 candidate
- 状态：closed · 创建：2026-10-16 · 作者：nanyu-lu

## Summary
- Align the public homepage and download page with the v1.0.39 macOS candidate state.
- Clarify that the Mini Program prototype has not passed device acceptance, privacy review, or release.
- Keep all public download links on the verified v1.0.34 release.

## Verification
- `git diff --check` passed.
- Local preview was reviewed before commit.
- The Pages workflow deploys on `main` changes under `site/**`; public verification will follow merge.

The previous v1.0.38 site snapshot is retained locally in `site-snapshots/2026-10-16-v1.0.38-status/` and is not included in the deployment.

## [PR #4] feat: cross-device backup sync with Pocket and Firebase
- 状态：closed · 创建：2026-10-16 · 作者：nanyu-lu

## Summary

- Add a standalone HTTPS iPhone Safari PWA at `site/pocket/` with Firebase email/password registration and login, required username, initials avatar, account-scoped live progress and archive history, IndexedDB session/history cache, offline app shell, install manifest and icons.
- Keep photo/video bytes, computer paths and passwords out of Firebase; the PWA is a metadata viewer and does not scan iPhone photos/SD cards or run native background copies.
- Persist Firebase usernames through the profile update endpoint in desktop and mobile clients. Keep the password field after failed auth and clear it only after success.
- iOS native app, IPA, TestFlight and App Store are out of scope. Android device acceptance is deferred because no Android handset is available; the APK remains internal. Mini Program development is discontinued.
- Add PWA CI checks, update/download guidance, and installation/rollback instructions. Preserve prior snapshots for rollback.

## Candidate verification

Latest PR head: `f60055b19fa7ca21cafa18076d432a7916212b5a` (`fix: use trusted CA bundle for update checks`).

- Reliability PR run `39299786326` and push run `39299775136`: success.
- Windows preview run `39299786336`: success; artifact `12211951153`, 27,877,439 bytes, digest `sha256:7eb8d2a879b9898a3a1fa5822a95308eb27080ceaccbb13967b9bcc9ce7efc63`. Downloaded x64 PE EXE SHA-256 `5b456df54964ba9ba56f8db398c53b3ea964e43371a0b687bf692f4a7d688f56` matches `SHA256SUMS.txt`; workflow verifies Authenticode `NotSigned`. This is a CI candidate, not Windows device acceptance.
- Pocket run `39296494880` completed `success`; PWA/check and Android debug jobs succeeded. Android artifact `12209584250`, metadata size 94,609,015 bytes, digest `sha256:b01d2998695912c5b95c74d1b586028fd09707e749c17739990688296dcdbfb9`. Downloaded `app-debug.apk` SHA-256 `18455f05985e2731563d314fa629f74b8b8a2adc113f968d6249f0c42093ca8a`; ZIP integrity passed and four ABIs are present. It is a debug APK and was not installed on a device.
- macOS run `39296495009` on the prior head failed because its check incorrectly required a completely unsigned app. The correction in this head retains only an ad-hoc technical signature for macOS arm64, with no Team ID or Developer ID authority. Fresh candidate run `39299786369` succeeded, including the ad-hoc/no-developer-identity gate and DMG packaging. Artifact `12210720943` metadata: 21,080,933 bytes, digest `sha256:88d258dea4aa527d59e5ce1ba86ccb958934e0aca6d1c900dd1c357937aeaf68`. Local artifact download returned TLS EOF, so the inner DMG was not locally inspected.
- The update-check endpoint previously returned 502 because Python could not verify GitHub API TLS using the system CA store. It now uses the project certifi bundle with verification enabled; source-based request to the real latest-release API returned HTTP 200 and reports public v1.0.34. The full desktop suite passes 113 tests (2 Windows-only skips).
- Pocket Android artifact `12213876388`, metadata 94,609,019 bytes, digest `sha256:7ce022bb565f628b9574f92034f9fd5d443db5dccb1b989c52adaf00e2509baf`. Downloaded APK SHA-256 `d84ae160383bd292b5fbc97bfbee25638a231c9ea93f9a6fb4f7a8d60a4ccc30`; ZIP integrity passed and the APK includes four ABIs. It is a debug APK; no Android device installation was done.
- At exact head `f60055b`, macOS `39303078835`, Windows `39303078967`, Pocket `39303078833`, Reliability PR `39303078757` and Reliability push `39303056912` all completed successfully.
- Existing candidate packages are not public release assets. Their build/hash checks do not replace physical-device acceptance.

## Remaining acceptance and release gates

- Merge the reviewed PR to deploy the PWA to GitHub Pages, then verify the public HTTPS routes and complete device/account gates.
- Deploy the reviewed PWA to GitHub Pages and verify the public HTTPS routes.
- Enter the Firebase password locally, then verify login/username, same-account progress and history, different-account isolati

## [议题 #2] [共创]测试1
- 状态：closed · 创建：2026-07-23 · 作者：nanyu-lu

### 发生在哪个场景

婚礼 / 活动

### 你遇到的问题

测试1

### 你希望影序替你省掉哪一步

测试1

## [议题 #1] [共创] 测试
- 状态：closed · 创建：2026-07-23 · 作者：nanyu-lu

### 发生在哪个场景

其他

### 你遇到的问题

测试

### 你希望影序替你省掉哪一步

测试
