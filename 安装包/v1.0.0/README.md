# 影序安装包 · v1.0.0

每个平台**只保留最新一版**安装包；更早版本的安装包不再留在仓库里。

| 平台 | 文件 | 大小 | SHA-256 |
|---|---|---|---|
| Windows x64（NSIS 安装版） | `影序_1.0.0_x64-setup.exe` | 37,037,579 字节 | `3a10c2c801429a391b4386b65abc616a7497909488304dd7ea6d9fc812375762` |
| macOS arm64（dmg） | `影序_1.0.0_aarch64.dmg` | 55,535,312 字节 | `9788cb8f0f9be18eaefe8e382777b97b3b4a5e55c7ce4d7c5eb27dcedfa5293b` |
| Android arm64-v8a（APK） | `影序_1.0.0_android.apk` | 94,991,304 字节 | `3054d512178da54a374bae98de2943386a49df485e7842e8c7535e87e2b8e1f5` |

> macOS dmg 取自 tag `v1.0.0` 的 CI Release 产物。
> Windows 包为本机构建件，与 CI Release 上的 `_1.0.0_x64-setup.exe`
> （37,154,942 字节，sha256 `7ea7c8215d72cf934ebf83896899dddf10f40c617d31544a33a973f0aa880002`）**不是同一次构建**，
> 两者版本号相同；如需与 tag 完全可复现的包，请用 Release 里的那份。

## 版本一致性

- 源码版本统一为 `1.0.0`，由 `scripts/check-versions.sh` 校验（桌面 Tauri/Cargo/前端 + 移动 Expo）
- 移动端：`expo.version = 1.0.0`、`expo.ios.buildNumber = 1`、`expo.android.versionCode = 1`
- APK 已核验：包名 `com.yingxuapp.pocket`、versionName `1.0.0`

## 校验

```bash
cd 安装包/v1.0.0 && shasum -a 256 -c SHA256SUMS.txt
```

## 各端产物来源

| 平台 | 产出方式 |
|---|---|
| Windows x64 | CI `Release` 流程 `build-windows` job（NSIS），或按 `docs/发布/打包与发布手册.md` §6.5 本机交叉编译 |
| Android | `cd source && npx eas-cli@latest build -p android --profile production`（`.aab`）；本地 `npx expo run:android` 产出可直装的 `.apk` |
| macOS arm64 | CI `Release` 流程 `build-macos` job（`.dmg`），产物上传到对应 tag 的 GitHub Release |
| iOS | `cd source && npx eas-cli@latest build -p ios --profile production`（`.ipa`，需 Apple 开发者账号） |

- 未签名提示：Windows 包未签名，SmartScreen 会拦，点「更多信息 → 仍要运行」。
- 移动端上架前请递增 `buildNumber` / `versionCode`，同一版本号无法重复上架。
