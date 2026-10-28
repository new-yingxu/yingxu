#!/usr/bin/env python3
"""把 docs/发布/发布说明模板.md 渲染成实际 Release 说明。

用法：python3 scripts/render-release-notes.py --tag v0.2.0-draft.1 --out release-out/notes.md
单一来源是模板文件，避免 workflow 里再抄一份正文导致漂移。
"""
import argparse
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", required=True)
    ap.add_argument("--template", default="docs/发布/发布说明模板.md")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    text = (ROOT / args.template).read_text(encoding="utf-8")
    text = text.replace("{{TAG}}", args.tag).replace(
        "{{DATE}}", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    )
    if "{{" in text:
        raise SystemExit(f"模板仍有未替换的占位符: {text[text.index('{{'):][:40]}")
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(f"已渲染 {out}（{len(text)} 字符）")


if __name__ == "__main__":
    main()
