#!/usr/bin/env bash
# 统一版本号：所有端必须一致，改完请跑 scripts/check-versions.sh 复核。
#
#   backend/tauri-host/tauri.conf.json   bundle 版本（打包实际使用）
#   backend/tauri-host/Cargo.toml        宿主 crate（Tauri 要求与 conf 一致）
#   backend/core/Cargo.toml              本地核心库（health 里回报的版本）
#   desktop-ui/package.json              桌面前端（V2，当前使用）
#   desktop-ui-v1/package.json           桌面前端（V1 留档）
#   source/package.json                  移动端（Expo）包
#   source/app.json                      移动端（Expo）应用版本
#
# 用法：scripts/bump-version.sh 1.0.0
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VERSION="${1:-}"

if [[ -z "$VERSION" ]]; then
  echo "用法: $0 <version>   例: $0 0.2.0-draft.2" >&2
  exit 1
fi
if [[ ! "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.-]+)?$ ]]; then
  echo "版本号需符合 semver（可带 prerelease），收到: ${VERSION}" >&2
  exit 1
fi

python3 - "$ROOT" "$VERSION" <<'PY'
import json
import re
import sys
from pathlib import Path

root, version = Path(sys.argv[1]), sys.argv[2]


def replace(path, pattern, repl):
    p = root / path
    text = p.read_text(encoding="utf-8")
    new, n = re.subn(pattern, repl, text, count=1, flags=re.MULTILINE)
    if n == 0:
        sys.exit(f"未匹配到版本号: {path}")
    p.write_text(new, encoding="utf-8")
    print(f"  {path}")


replace("backend/tauri-host/Cargo.toml", r'^version = ".*"$', f'version = "{version}"')
replace("backend/core/Cargo.toml", r'^version = ".*"$', f'version = "{version}"')

conf = root / "backend/tauri-host/tauri.conf.json"
data = json.loads(conf.read_text(encoding="utf-8"))
data["version"] = version
conf.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print("  backend/tauri-host/tauri.conf.json")

pkg = root / "desktop-ui/package.json"
data = json.loads(pkg.read_text(encoding="utf-8"))
data["version"] = version
pkg.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print("  desktop-ui/package.json")

for rel in ("desktop-ui-v1/package.json", "source/package.json"):
    target = root / rel
    data = json.loads(target.read_text(encoding="utf-8"))
    data["version"] = version
    target.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"  {rel}")

app = root / "source/app.json"
data = json.loads(app.read_text(encoding="utf-8"))
data["expo"]["version"] = version
app.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print("  source/app.json")
PY

echo
echo "版本已更新为 ${VERSION}。Cargo.lock 会在下次 cargo build 时自动同步。"
echo "复核：scripts/check-versions.sh"
