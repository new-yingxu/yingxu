#!/usr/bin/env python3
"""从构建产物目录生成发布清单：SHA256SUMS + latest.json。

用法：
    python3 scripts/gen-latest-json.py \
        --artifacts dist-artifacts \
        --version 0.2.0-draft.1 \
        --repo new-yingxu/yingxu \
        --tag v0.2.0-draft.1 \
        --out release-out

latest.json 采用 Tauri updater 的字段形状（version/pub_date/platforms），
但当前**没有签名密钥**，`signature` 一律留空 —— 应用内更新尚未启用，
不要把它当成已可用的自动更新通道。官网的下载信息来自 GitHub Releases API，
latest.json 只是为后续更新预留的清单。
"""
import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

def targets_for(name):
    """按文件名里的架构标识判定平台，**不猜测**。

    Tauri 产物名带 target triple（如 `..._aarch64.dmg`、`..._x64-setup.exe`），
    因此可以精确判断。判不出就跳过并告警，宁可少一项也不让 Intel 用户下到 arm64 包。
    """
    lower = name.lower()
    arch = None
    if "aarch64" in lower or "arm64" in lower:
        arch = "arm64"
    elif "x86_64" in lower or "x64" in lower or "intel" in lower or "amd64" in lower:
        arch = "x64"
    if lower.endswith(".dmg"):
        return {"arm64": ["darwin-aarch64"], "x64": ["darwin-x86_64"]}.get(arch, [])
    if lower.endswith(".tar.gz"):
        return {"arm64": ["darwin-aarch64"], "x64": ["darwin-x86_64"]}.get(arch, [])
    if lower.endswith(".exe") or lower.endswith(".msi"):
        return ["windows-x86_64"] if arch in (None, "x64") else ["windows-aarch64"]
    if lower.endswith(".appimage"):
        return ["linux-x86_64"] if arch in (None, "x64") else ["linux-aarch64"]
    return []


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--artifacts", required=True)
    ap.add_argument("--version", required=True)
    ap.add_argument("--repo", required=True, help="owner/repo")
    ap.add_argument("--tag", required=True)
    ap.add_argument("--out", default="release-out")
    args = ap.parse_args()

    art = Path(args.artifacts)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    files = sorted(
        p for p in art.rglob("*")
        if p.is_file() and p.suffix.lower() in {".dmg", ".exe", ".msi"} | {".gz", ".appimage"}
    )
    if not files:
        sys.exit(f"没有在 {art} 找到可发布的安装包（.dmg/.exe/.msi）")

    assets, sums = [], []
    for p in files:
        digest = sha256(p)
        url = f"https://github.com/{args.repo}/releases/download/{args.tag}/{p.name}"
        assets.append({"name": p.name, "bytes": p.stat().st_size, "sha256": digest, "url": url})
        sums.append(f"{digest}  {p.name}")

    platforms, unmapped = {}, []
    for a in assets:
        targets = targets_for(a["name"])
        if not targets:
            unmapped.append(a["name"])
        for t in targets:
            # 同一平台只留第一个（避免 tar.gz 覆盖 dmg）
            platforms.setdefault(t, {
                "url": a["url"],
                "signature": "",
                "sha256": a["sha256"],
            })

    manifest = {
        "version": args.version,
        "pub_date": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "notes": "见 Release 页面「发布说明」与 SHA256SUMS",
        "signature_status": "unsigned",
        "updater_enabled": False,
        "platforms": platforms,
        "assets": assets,
    }
    (out / "latest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (out / "SHA256SUMS").write_text("\n".join(sums) + "\n", encoding="utf-8")
    print(f"已生成 {out / 'latest.json'} 与 {out / 'SHA256SUMS'}")
    for a in assets:
        print(f"  {a['sha256'][:16]}…  {a['bytes']:>12,}  {a['name']}")
    print("平台:", ", ".join(platforms) or "（无）")
    if unmapped:
        print("警告：以下资产无法判定架构，未写入 platforms（需人工确认）:")
        for name in unmapped:
            print("  -", name)


if __name__ == "__main__":
    main()
