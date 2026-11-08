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

SUFFIX_MAP = [
    (".dmg", ["darwin-aarch64", "darwin-x86_64"]),
    (".exe", ["windows-x86_64"]),
    (".msi", ["windows-x86_64"]),
    (".AppImage", ["linux-x86_64"]),
    (".tar.gz", ["darwin-aarch64", "darwin-x86_64"]),
]

# 文件名里带架构后缀时按后缀收敛，避免把 arm64 的 dmg 同时标成 darwin-x86_64
ARCH_HINTS = [
    (("aarch64", "arm64"), "darwin-aarch64"),
    (("x86_64", "x64", "amd64"), "darwin-x86_64"),
]


def narrow_targets(name, targets):
    lower = name.lower()
    for needles, target in ARCH_HINTS:
        if any(n in lower for n in needles) and target in targets:
            return [target]
    return targets


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
        if p.is_file()
        and p.suffix.lower() in {".dmg", ".exe", ".msi", ".apk"} | {".gz", ".appimage"}
    )
    if not files:
        sys.exit(f"没有在 {art} 找到可发布的安装包（.dmg/.exe/.msi/.apk）")

    assets, sums = [], []
    for p in files:
        digest = sha256(p)
        url = f"https://github.com/{args.repo}/releases/download/{args.tag}/{p.name}"
        assets.append({"name": p.name, "bytes": p.stat().st_size, "sha256": digest, "url": url})
        sums.append(f"{digest}  {p.name}")

    platforms = {}
    for a in assets:
        lower = a["name"].lower()
        for suffix, targets in SUFFIX_MAP:
            if lower.endswith(suffix.lower()):
                for t in narrow_targets(a["name"], targets):
                    # 同名同平台只留第一个（dmg 优先于 tar.gz）
                    platforms.setdefault(t, {
                        "url": a["url"],
                        "signature": "",
                        "sha256": a["sha256"],
                    })
                break

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
    # 同一份内容输出两个文件名：官网下载页与旧版脚本分别认 SHA256SUMS.txt / SHA256SUMS，
    # 双写可以避免两份清单内容漂移。
    sums_text = "\n".join(sums) + "\n"
    for name in ("SHA256SUMS", "SHA256SUMS.txt"):
        (out / name).write_text(sums_text, encoding="utf-8")
    print(f"已生成 {out/'latest.json'} 与 SHA256SUMS(.txt)")
    for a in assets:
        print(f"  {a['sha256'][:16]}…  {a['bytes']:>12,}  {a['name']}")
    print("平台:", ", ".join(platforms) or "（无）")


if __name__ == "__main__":
    main()
