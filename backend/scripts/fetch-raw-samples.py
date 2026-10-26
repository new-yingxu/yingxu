#!/usr/bin/env python3
"""下载真实 RAW 样本（raw.pixls.us 公共样本库）到指定目录。

只用公开的 CC0 样本，按品牌/格式各取一份最小可用的全尺寸原片，
下载后校验文件头魔数、记录 SHA-256，并写 `manifest.json` 供后续矩阵测试。

用法：
    python3 backend/scripts/fetch-raw-samples.py \
        --dest "/Volumes/NO NAME/RAW-TEST-2026-10-25" \
        --per-format 1

注意：
- 站点会按 User-Agent 拒绝脚本请求，这里带浏览器 UA + Referer。
- 只读样本，不修改；文件落在目标盘上的独立目录里。
- 若站点改版或不可达，脚本会报出具体 HTTP 状态，不做静默跳过。
"""
import argparse
import hashlib
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

INDEX = "https://raw.pixls.us/json/getrepository.php?set=all"
SITE = "https://raw.pixls.us/"
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

# 目标格式 → 用于在索引里筛选的扩展名
FORMATS = {
    "cr2": [".cr2"],
    "cr3": [".cr3"],
    "nef": [".nef", ".nrw"],
    "arw": [".arw", ".sr2"],
    "raf": [".raf"],
    "rw2": [".rw2"],
    "orf": [".orf"],
    "dng": [".dng"],
}
MAGIC = {
    # (偏移, 期望字节)：ISO BMFF 的 ftyp 在第 4 字节；ORF 有 IIRO/IIRS/MMOR 三种头
    ".cr2": [(0, b"II\x2a\x00"), (0, b"MM\x00\x2a")],
    ".nef": [(0, b"II\x2a\x00"), (0, b"MM\x00\x2a")],
    ".arw": [(0, b"II\x2a\x00"), (0, b"MM\x00\x2a")],
    ".dng": [(0, b"II\x2a\x00"), (0, b"MM\x00\x2a")],
    ".rw2": [(0, b"II\x2a\x00"), (0, b"IIU\x00")],
    ".orf": [(0, b"IIRO"), (0, b"IIRS"), (0, b"MMOR")],
    ".raf": [(0, b"FUJIFILMCCD-RAW")],
    ".cr3": [(4, b"ftypcrx"), (4, b"ftyp")],
}


def get(url, referer=INDEX, timeout=60):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": UA,
            "Referer": referer,
            "Accept": "application/json,text/html,*/*",
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    return urllib.request.urlopen(req, timeout=timeout)


def load_index():
    try:
        with get(INDEX) as r:
            raw = r.read()
    except urllib.error.HTTPError as e:
        sys.exit(f"索引请求失败：HTTP {e.code}（{INDEX}）")
    except Exception as e:  # noqa: BLE001
        sys.exit(f"索引请求失败：{e}（{INDEX}）")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        sys.exit(f"索引不是合法 JSON：{e}")


URL_RE = re.compile(r"href='([^']*getfile[^']+)'")
SHA_RE = re.compile(r"<span title='sha256 Checksum'>([a-f0-9]{64})</span>")
SIZE_RE = re.compile(r"\(([\d.]+)MB\)")


def iter_samples(index):
    """索引是二维数组行：[make, model, version, ratio, ?, license, date, file_cell, exif_cell]。"""
    rows = index.get("data") if isinstance(index, dict) else index
    for row in rows or []:
        if not isinstance(row, list) or len(row) < 8:
            continue
        cell = row[7]
        m = URL_RE.search(cell)
        if not m:
            continue
        url = m.group(1)
        name = Path(url.split("?")[0]).name
        # 文件名里带空格/中文，站点用原样路径；urllib 需要转义
        sha = SHA_RE.search(cell)
        size = SIZE_RE.search(cell)
        yield {
            "name": name,
            "url": urllib.parse.quote(url, safe=":/?&=#%"),
            "bytes": int(float(size.group(1)) * 1024 * 1024) if size else 0,
            "sha256": sha.group(1) if sha else "",
            "make": (row[0] or "").strip(),
            "model": (row[1] or "").strip(),
            "version": (row[2] or "").strip(),
            "year": (row[6] or "")[:4],
            # 只接受 CC0：样本会进仓库/卡里，避免 BY-NC-SA 带来的传播限制
            "cc0": "publicdomain/zero" in (row[5] or "") or "CC0" in (row[5] or ""),
        }


SCANNER_HINTS = ("coolscan", "scanner", "filmscan", "super coolscan", "print")


def pick(samples, ext_list, per_format):
    """同格式内：只要 CC0 → 排除扫描仪/缩略 RAW → 机型越新越好 → 同品牌只留一份。"""
    cands = []
    for s in samples:
        if Path(s["name"]).suffix.lower() not in ext_list or not s["cc0"]:
            continue
        model = (s["model"] or "").lower()
        if any(h in model for h in SCANNER_HINTS):
            continue
        if "sraw" in (s["version"] or "").lower() or "mraw" in (s["version"] or "").lower():
            continue
        if s["bytes"] > 80 * 1024 * 1024:
            continue
        cands.append(s)
    # 新的机型优先；同为 0 年时按体积从小到大
    cands.sort(key=lambda s: (-int(s["year"] or 0), s["bytes"]))
    out, seen = [], set()
    for s in cands:
        brand = (s["make"] or s["name"].split(" - ")[0]).strip().lower()
        if brand in seen:
            continue
        seen.add(brand)
        out.append(s)
        if len(out) >= max(per_format, 1):
            break
    return out


def download(sample, dest: Path):
    target = dest / sample["name"]
    if target.exists() and target.stat().st_size > 1024:
        print(f"  已存在，跳过：{target.name}")
        return target
    tmp = target.with_suffix(target.suffix + ".part")
    with get(sample["url"]) as r, open(tmp, "wb") as f:
        total = 0
        while chunk := r.read(1 << 20):
            f.write(chunk)
            total += len(chunk)
        if total < 1024:
            tmp.unlink(missing_ok=True)
            raise RuntimeError(f"下载过小（{total} 字节）")
    tmp.replace(target)
    return target


def check_magic(path: Path):
    ext = path.suffix.lower()
    want = MAGIC.get(ext)
    if not want:
        return True
    with open(path, "rb") as f:
        head = f.read(64)
    return any(head[off: off + len(m)] == m for off, m in want)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dest", required=True, help="下载目录（例如内存卡上的 RAW-TEST 目录）")
    ap.add_argument("--per-format", type=int, default=1, help="每种格式取几个样本")
    ap.add_argument(
        "--formats",
        default="cr2,cr3,nef,arw,raf,rw2,orf,dng",
        help="逗号分隔的目标格式",
    )
    args = ap.parse_args()

    dest = Path(args.dest).expanduser()
    dest.mkdir(parents=True, exist_ok=True)
    wanted = [f.strip().lower() for f in args.formats.split(",") if f.strip()]
    unknown = [f for f in wanted if f not in FORMATS]
    if unknown:
        sys.exit(f"不支持的格式: {unknown}")

    print(f"读取样本索引 {INDEX} …")
    index = load_index()
    samples = list(iter_samples(index))
    print(f"索引共 {len(samples)} 条")
    if not samples:
        sys.exit("索引里没有解析到任何样本，可能站点结构变了，请人工确认")

    manifest = []
    for fmt in wanted:
        picked = pick(samples, FORMATS[fmt], args.per_format)
        if not picked:
            print(f"[{fmt}] 索引里没有匹配的样本")
            continue
        for s in picked:
            label = s["make"] or s["name"].split(" - ")[0]
            print(f"[{fmt}] {label} :: {s['name']}")
            try:
                path = download(s, dest)
            except Exception as e:  # noqa: BLE001
                print(f"  下载失败：{e}")
                manifest.append({**s, "format": fmt, "status": "download_failed", "error": str(e)})
                continue
            ok = check_magic(path)
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if s.get("sha256") and s["sha256"] != digest:
                ok = False
                print(f"  校验和不一致：站点 {s['sha256'][:16]}… / 本地 {digest[:16]}…")
            manifest.append(
                {
                    "format": fmt,
                    "name": path.name,
                    "path": str(path),
                    "bytes": path.stat().st_size,
                    "sha256": digest,
                    "expectedSha256": s.get("sha256", ""),
                    "magicOk": ok,
                    "source": s["url"],
                    "status": "ok" if ok else "magic_mismatch",
                }
            )
            print(f"  → {path.stat().st_size / 1024 / 1024:.1f} MB  magic={'OK' if ok else '不匹配'}")

    out = dest / "manifest.json"
    out.write_text(
        json.dumps(
            {
                "fetchedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "index": INDEX,
                "items": manifest,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    ok_n = sum(1 for m in manifest if m.get("status") == "ok")
    print(f"\n完成：{ok_n}/{len(manifest)} 个样本可用，清单写入 {out}")
    return 0 if ok_n else 1


if __name__ == "__main__":
    sys.exit(main())
