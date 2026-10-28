#!/usr/bin/env python3
"""RAW 样本矩阵测试：逐个真实样本跑 `library.analyze`，记录成功率/耗时/内存峰值。

用法：
    cd backend && cargo build -p yingxu-core
    python3 scripts/raw-sample-matrix.py \
        --corpus "/Volumes/NO NAME/RAW-TEST-2026-10-25" \
        --out "../docs/协作交接/raw-样本矩阵-2026-10-25.md"

做的事：
1. 用 `yingxu-core --data-dir <临时库> rpc` 索引整个语料目录（只读）
2. 对每个 RAW **单独起一个进程** 调 `library.analyze`，便于拿到单文件内存峰值（macOS 用 /usr/bin/time -l）
3. 分析前后各算一次 SHA-256，确认原片未被改动
4. 输出 Markdown 表格 + JSON（含错误码），不做"全部通过"的粉饰
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BIN = ROOT / "target" / "debug" / "yingxu-core"
RAW_EXT = {".cr2", ".cr3", ".nef", ".nrw", ".arw", ".sr2", ".raf", ".rw2", ".orf", ".dng",
           ".pef", ".srw", ".raw", ".3fr", ".fff", ".iiq", ".mos", ".mrw", ".erf", ".kdc"}


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class Rpc:
    """单进程 RPC；可选套 /usr/bin/time -l 采内存峰值。"""

    def __init__(self, data_dir, measure=False):
        cmd = [str(BIN), "--data-dir", str(data_dir), "rpc"]
        if measure and Path("/usr/bin/time").exists():
            cmd = ["/usr/bin/time", "-l"] + cmd
        self.measure = measure and Path("/usr/bin/time").exists()
        self.proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        self.n = 0

    def call(self, method, params=None):
        self.n += 1
        req = {"requestId": f"m{self.n}", "version": 1, "method": method, "params": params or {}}
        self.proc.stdin.write(json.dumps(req) + "\n")
        self.proc.stdin.flush()
        try:
            return json.loads(self.proc.stdout.readline())
        except json.JSONDecodeError:
            return {"ok": False, "error": {"code": "NO_REPLY", "message": ""}}

    def close(self):
        try:
            self.proc.stdin.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            self.proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        peak = None
        if self.measure:
            err = self.proc.stderr.read()
            m = re.search(r"(\d+)\s+maximum resident set size", err)
            if m:
                peak = int(m.group(1))  # macOS 单位 byte
        return peak


def index_corpus(corpus, store):
    rpc = Rpc(store)
    r = rpc.call("library.index", {"source": str(corpus)})
    rpc.close()
    return r


def find_media_id(store, name):
    rpc = Rpc(store)
    r = rpc.call("library.search", {"query": name, "kind": "photo", "quality": "all"})
    rpc.close()
    if not r.get("ok"):
        return None
    for item in r["data"]:
        if item.get("name") == name:
            return item.get("id")
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", required=True, help="含 RAW 样本的目录")
    ap.add_argument("--out", help="Markdown 输出路径")
    ap.add_argument("--json-out", help="JSON 输出路径")
    ap.add_argument("--limit", type=int, default=0, help="只测前 N 个（调试用）")
    args = ap.parse_args()

    if not BIN.exists():
        sys.exit("先构建：cd backend && cargo build -p yingxu-core")

    corpus = Path(args.corpus).resolve()
    if not corpus.is_dir():
        sys.exit(f"语料目录不存在: {corpus}")

    files = sorted(p for p in corpus.iterdir() if p.is_file() and p.suffix.lower() in RAW_EXT)
    if args.limit:
        files = files[: args.limit]
    if not files:
        sys.exit(f"{corpus} 里没有 RAW 文件（支持的后缀：{', '.join(sorted(RAW_EXT))}）")

    store = Path(tempfile.mkdtemp(prefix="yingxu-rax-matrix-"))
    print(f"语料: {corpus}\n文件: {len(files)} 个\n临时库: {store}\n")

    t0 = time.time()
    idx = index_corpus(corpus, store)
    if not idx.get("ok"):
        sys.exit(f"索引失败: {idx.get('error')}")
    print(f"索引完成：{idx['data']}（{time.time() - t0:.2f}s）\n")

    rows = []
    for i, path in enumerate(files, 1):
        before = sha256(path)
        size_mb = path.stat().st_size / 1024 / 1024
        media_id = find_media_id(store, path.name)
        if not media_id:
            rows.append({"name": path.name, "format": path.suffix.lstrip(".").upper(),
                         "bytes": path.stat().st_size, "status": "not_indexed",
                         "error": "NOT_FOUND", "elapsedMs": 0, "peakMiB": None})
            print(f"[{i}/{len(files)}] {path.name} → 未入库")
            continue

        started = time.time()
        rpc = Rpc(store, measure=True)
        r = rpc.call("library.analyze", {"mediaId": media_id})
        peak = rpc.close()
        elapsed = (time.time() - started) * 1000

        after = sha256(path)
        d = r.get("data") or {}
        meta = d.get("metadata") or {}
        rows.append({
            "name": path.name,
            "format": d.get("rawFormat") or path.suffix.lstrip(".").upper(),
            "bytes": path.stat().st_size,
            "sizeMiB": round(size_mb, 1),
            "status": "ok" if r.get("ok") else "failed",
            "error": (r.get("error") or {}).get("code", ""),
            "message": (r.get("error") or {}).get("message", ""),
            "previewSource": d.get("previewSource"),
            "previewBytes": meta.get("previewBytes"),
            "previewWidth": meta.get("previewWidth"),
            "previewHeight": meta.get("previewHeight"),
            "cameraMake": meta.get("cameraMake"),
            "cameraModel": meta.get("cameraModel"),
            "lensModel": meta.get("lensModel"),
            "capturedAt": meta.get("capturedAt"),
            "iso": meta.get("iso"),
            "fNumber": meta.get("fNumber"),
            "exposureTime": meta.get("exposureTime"),
            "thumbnail": d.get("thumbnailAvailable"),
            "qualityCandidates": (meta.get("quality") or {}).get("candidates"),
            "elapsedMs": round(elapsed),
            "peakMiB": round(peak / 1024 / 1024, 1) if peak else None,
            "sourceUnchanged": before == after,
        })
        flag = "OK " if r.get("ok") else "ERR"
        print(f"[{i}/{len(files)}] {flag} {path.name}  {size_mb:5.1f}MB  {elapsed:6.0f}ms  "
              f"{(str(round(peak / 1024 / 1024, 1)) + 'MiB') if peak else '-':>8}  "
              f"{d.get('previewSource') or (r.get('error') or {}).get('code', '')}")

    shutil.rmtree(store, ignore_errors=True)

    ok = [r for r in rows if r["status"] == "ok"]
    failed = [r for r in rows if r["status"] != "ok"]
    peaks = [r["peakMiB"] for r in rows if r.get("peakMiB")]
    lines = []
    lines.append("# RAW 真实样本矩阵（自动生成）\n")
    lines.append(f"- 语料目录：`{corpus}`")
    lines.append(f"- 样本数：{len(rows)}，成功 {len(ok)}，失败 {len(failed)}")
    if peaks:
        lines.append(f"- 单文件内存峰值：{max(peaks):.1f} MiB（最大样本 {max(r['sizeMiB'] for r in rows):.1f} MB）")
    lines.append(f"- 原片校验：{sum(1 for r in rows if r.get('sourceUnchanged'))}/{len(rows)} 个文件分析前后 SHA-256 不变\n")
    lines.append("| # | 文件 | 格式 | 体积 | 结果 | 预览来源 | 预览尺寸 | 机型 | 拍摄时间 | 耗时 | 内存峰值 |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|")
    for i, r in enumerate(rows, 1):
        res = "✅" if r["status"] == "ok" else f"❌ {r.get('error','')}"
        lines.append(
            f"| {i} | {r['name']} | {r['format']} | {r['sizeMiB']} MB | {res} | "
            f"{r.get('previewSource') or '-'} | "
            f"{(str(r['previewWidth']) + '×' + str(r['previewHeight'])) if r.get('previewWidth') else '-'} | "
            f"{(str(r.get('cameraMake') or '') + ' ' + str(r.get('cameraModel') or '')).strip() or '-'} | "
            f"{r.get('capturedAt') or '-'} | {r['elapsedMs']} ms | "
            f"{(str(r['peakMiB']) + ' MiB') if r.get('peakMiB') else '-'} |"
        )
    if failed:
        lines.append("\n## 失败明细\n")
        for r in failed:
            lines.append(f"- `{r['name']}`：`{r.get('error')}` {r.get('message','')}")
    md = "\n".join(lines) + "\n"

    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(md, encoding="utf-8")
        print(f"\n表格已写入 {out}")
    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps({"corpus": str(corpus), "rows": rows}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"JSON 已写入 {args.json_out}")
    print(md)
    return 0 if len(failed) == 0 or len(ok) > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
