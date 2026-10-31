#!/usr/bin/env bash
# 校验版本号是否所有端一致（桌面 Tauri/Cargo/前端 + 移动 Expo）。CI 的发布门禁会调用它。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

python3 - <<'PY'
import json
import re
import sys
from pathlib import Path

root = Path(".")


def cargo_version(path):
    text = (root / path).read_text(encoding="utf-8")
    section = text.split("[package]", 1)[1]
    return re.search(r'^version = "(.*)"$', section, re.MULTILINE).group(1)


pairs = [
    ("backend/tauri-host/tauri.conf.json", json.loads((root / "backend/tauri-host/tauri.conf.json").read_text(encoding="utf-8"))["version"]),
    ("backend/tauri-host/Cargo.toml", cargo_version("backend/tauri-host/Cargo.toml")),
    ("backend/core/Cargo.toml", cargo_version("backend/core/Cargo.toml")),
    ("desktop-ui/package.json", json.loads((root / "desktop-ui/package.json").read_text(encoding="utf-8"))["version"]),
    ("desktop-ui-v1/package.json", json.loads((root / "desktop-ui-v1/package.json").read_text(encoding="utf-8"))["version"]),
    ("source/package.json", json.loads((root / "source/package.json").read_text(encoding="utf-8"))["version"]),
    ("source/app.json", json.loads((root / "source/app.json").read_text(encoding="utf-8"))["expo"]["version"]),
]

for path, version in pairs:
    print(f"{version:<18} {path}")

unique = {v for _, v in pairs}
if len(unique) != 1:
    print("\n版本号不一致，跑 scripts/bump-version.sh <version> 统一", file=sys.stderr)
    sys.exit(1)
print(f"\n一致：{unique.pop()}")
PY
