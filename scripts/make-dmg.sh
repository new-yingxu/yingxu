#!/usr/bin/env bash
# 手工封装 macOS 安装包（.dmg）。
#
# 什么时候需要它：Tauri 的 `cargo tauri build --bundles dmg` 依赖
# `bundle_dmg.sh`，该脚本内部用 `grep -E` 解析 hdiutil 输出。若 PATH 里的
# `grep` 不是 BSD/GNU grep（例如被工具链注入了 toybox 版），脚本会以
# `bad regex ... brackets ([ ]) not balanced` 失败——这是环境问题，不是产物问题。
# 此时用本脚本从已签名的 .app 直接封装，结果等价。
#
# 用法：scripts/make-dmg.sh --app <X.app> --out <X.dmg> [--volname 名称]
set -euo pipefail

APP=""
OUT=""
VOLNAME=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --app) APP="$2"; shift 2 ;;
    --out) OUT="$2"; shift 2 ;;
    --volname) VOLNAME="$2"; shift 2 ;;
    *) echo "未知参数: $1" >&2; exit 1 ;;
  esac
done

[[ -d "$APP" ]] || { echo "找不到 .app: $APP" >&2; exit 1; }
[[ -n "$OUT" ]] || { echo "需要 --out" >&2; exit 1; }
[[ -n "$VOLNAME" ]] || VOLNAME="$(basename "$APP" .app)"

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"

mkdir -p "$(dirname "$OUT")"
rm -f "$OUT"
hdiutil create \
  -volname "$VOLNAME" \
  -srcfolder "$STAGE" \
  -fs HFS+ \
  -format UDZO \
  -imagekey zlib-level=9 \
  -ov \
  "$OUT" >/dev/null

hdiutil verify "$OUT" >/dev/null
echo "已生成: $OUT"
shasum -a 256 "$OUT"
