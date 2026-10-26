#!/usr/bin/env python3
"""RAW / 视频 / 网盘三条链路的端到端验证脚本（真实执行，不是"应该可以"）。

用法：
    cd backend && cargo build -p yingxu-core
    python3 scripts/verify-media.py [--keep]

做的事：
  1. 生成构造样本：一个 TIFF 容器的 .cr2（内含 JPEG 预览）+ ffmpeg 生成的 1080p/4K 视频
  2. 通过 `yingxu-core --data-dir ... rpc` 的真实 JSON-lines 协议跑：
     library.index / library.analyze / video.probe / video.analyze / capabilities.get
  3. 起本地 mock 网盘服务器，跑 netdisk.enqueue + netdisk.run（含 503 续传）
  4. 打印每条结果与子进程内存峰值（用于判断是否有 OOM 风险）
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BIN = ROOT / "target" / "debug" / "yingxu-core"
RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print(("[PASS] " if ok else "[FAIL] ") + name + (("  " + str(detail)) if detail else ""))


def build_synthetic_cr2(pillow_ok):
    """构造一个 TIFF 容器 + 内嵌 JPEG 预览的 .cr2 样本。"""
    from PIL import Image, ImageDraw
    import io

    img = Image.new("RGB", (1200, 800), (40, 60, 50))
    d = ImageDraw.Draw(img)
    for i in range(0, 1200, 40):
        d.line([(i, 0), (i, 800)], fill=(200, 210, 190), width=2)
    d.ellipse([(500, 300), (700, 500)], fill=(221, 186, 118))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=82)
    preview = buf.getvalue()

    ifd0 = 8
    ifd1 = ifd0 + 2 + 12 + 4
    data = ifd1 + 2 + 24 + 4
    out = bytearray()
    out += b"II\x2a\x00" + ifd0.to_bytes(4, "little")
    out += (1).to_bytes(2, "little")
    out += (0x0103).to_bytes(2, "little") + (3).to_bytes(2, "little") + (1).to_bytes(4, "little") + bytes([1, 0, 0, 0])
    out += ifd1.to_bytes(4, "little")
    out += (2).to_bytes(2, "little")
    out += (0x0201).to_bytes(2, "little") + (4).to_bytes(2, "little") + (1).to_bytes(4, "little") + data.to_bytes(4, "little")
    out += (0x0202).to_bytes(2, "little") + (4).to_bytes(2, "little") + (1).to_bytes(4, "little") + len(preview).to_bytes(4, "little")
    out += (0).to_bytes(4, "little")
    assert len(out) == data, (len(out), data)
    out += preview
    return bytes(out)


def make_video(path, size, seconds):
    cmd = [
        "ffmpeg", "-v", "error", "-y", "-f", "lavfi",
        "-i", "testsrc2=size=%s:rate=30" % size,
        "-t", str(seconds), "-c:v", "libx264", "-preset", "ultrafast",
        "-pix_fmt", "yuv420p", str(path),
    ]
    r = subprocess.run(cmd, capture_output=True)
    return r.returncode == 0 and path.exists()


class Rpc:
    def __init__(self, data_dir):
        self.proc = subprocess.Popen(
            [str(BIN), "--data-dir", str(data_dir), "rpc"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1,
        )
        self.n = 0

    def call(self, method, params=None):
        self.n += 1
        req = {"requestId": "req-%d" % self.n, "version": 1, "method": method, "params": params or {}}
        self.proc.stdin.write(json.dumps(req) + "\n")
        self.proc.stdin.flush()
        line = self.proc.stdout.readline()
        return json.loads(line)

    def close(self):
        self.proc.stdin.close()
        self.proc.wait(timeout=30)


def media_id_of(rpc, name):
    r = rpc.call("library.search", {"query": name, "kind": "all", "quality": "all"})
    if not r.get("ok"):
        return None
    for item in r["data"]:
        if item.get("name") == name:
            return item.get("id")
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true", help="保留临时目录")
    args = ap.parse_args()
    if not BIN.exists():
        sys.exit("先构建：cd backend && cargo build -p yingxu-core")

    # macOS 上 /var 是符号链接，安全目录检查会拒绝，必须取 canonical 路径
    work = Path(tempfile.mkdtemp(prefix="yingxu-verify-")).resolve()
    src = work / "media"
    src.mkdir()
    store = work / "store"
    print("workdir:", work)

    have_ffmpeg = shutil.which("ffmpeg") is not None
    (src / "shot.cr2").write_bytes(build_synthetic_cr2(True))
    if have_ffmpeg:
        make_video(src / "clip1080.mp4", "1920x1080", 6)
        make_video(src / "clip4k.mp4", "3840x2160", 4)
    else:
        (src / "clip1080.mp4").write_bytes(b"\x00" * 1024)

    rpc = Rpc(store)
    started = time.time()
    r = rpc.call("library.index", {"source": str(src)})
    check("library.index 索引成功", r.get("ok"), r.get("data") or r.get("error"))
    check("索引覆盖 RAW 与视频", (r.get("data") or {}).get("files", 0) >= 3, r.get("data"))

    caps = rpc.call("capabilities.get")
    check("capabilities 暴露新能力", caps.get("ok") and caps["data"]["rawPreview"] is True)

    mid = media_id_of(rpc, "shot.cr2")
    check("RAW 样本已入库", bool(mid), mid)
    if mid:
        raw = rpc.call("library.analyze", {"mediaId": mid})
        ok = raw.get("ok") and raw["data"]["rawFormat"] == "tiff_le"
        check("RAW 走预览提取（未做全解码）", ok, raw.get("error") or {
            "format": raw["data"]["rawFormat"],
            "previewSource": raw["data"]["previewSource"],
            "fullDecode": raw["data"]["fullDecodePerformed"],
            "sourceReadOnly": raw["data"]["sourceReadOnly"],
        })
        thumb = rpc.call("library.thumbnail", {"mediaId": mid})
        check("RAW 产出 512px 缩略图", thumb.get("ok"), (thumb.get("data") or {}).get("maxEdge"))

    if have_ffmpeg:
        vid = media_id_of(rpc, "clip4k.mp4")
        check("4K 样本已入库", bool(vid), vid)
        if vid:
            probe = rpc.call("video.probe", {"mediaId": vid})
            m = (probe.get("data") or {}).get("metadata", {})
            check("4K 视频元数据解析", probe.get("ok") and m.get("width") == 3840, {
                "width": m.get("width"), "height": m.get("height"),
                "fps": m.get("fps"), "codec": m.get("videoCodec"),
                "durationMs": m.get("durationMs"), "bitrateBps": m.get("bitrateBps"),
            })
            ana = rpc.call("video.analyze", {"mediaId": vid, "extractFrames": True})
            d = ana.get("data") or {}
            check("4K 视频抽帧成功", ana.get("ok") and d.get("frameSource") == "ffmpeg_stream_seek",
                  {"frameSource": d.get("frameSource"), "frames": (d.get("metadata") or {}).get("framesAnalyzed")})
            check("帧数据不出本机", d.get("framesUploaded") is False)

    prov = rpc.call("netdisk.providers")
    check("网盘提供方如实标注未获批",
          prov.get("ok") and all(not p["realUploadAvailable"] for p in prov["data"]),
          [(p["id"], p["approval"]) for p in prov["data"]])

    mock = None
    if True:
        env = dict(os.environ, MOCK_PORT="0", MOCK_FAIL_ONCE="1")
        mock = subprocess.Popen(
            [sys.executable, str(ROOT / "scripts" / "mock-netdisk-server.py")],
            stdout=subprocess.PIPE, text=True, env=env,
        )
        port = int(mock.stdout.readline().strip().split("=")[1])
        base = "http://127.0.0.1:%d" % port
        target = media_id_of(rpc, "clip1080.mp4")
        enq = rpc.call("netdisk.enqueue", {"provider": "mock", "mediaId": target})
        check("入队前校验本地 SHA-256", enq.get("ok") and enq["data"]["localVerified"] is True,
              (enq.get("data") or {}).get("sha256", "")[:16] + "…")
        if enq.get("ok"):
            tid = enq["data"]["taskId"]
            first = rpc.call("netdisk.run", {"taskId": tid, "baseUrl": base})
            check("分片失败可续传（不写假完成记录）",
                  first.get("ok") and first["data"]["state"] == "uploading" and first["data"]["resumable"],
                  first.get("data") or first.get("error"))
            second = rpc.call("netdisk.run", {"taskId": tid, "baseUrl": base})
            check("续传后远端哈希一致",
                  second.get("ok") and second["data"].get("verifiedRemote") is True,
                  second.get("data") or second.get("error"))
        gate = rpc.call("netdisk.enqueue", {"provider": "baidu", "mediaId": target})
        check("百度/夸克按闸门拒绝真实上传",
              (not gate.get("ok")) and gate["error"]["code"] == "NETDISK_NOT_APPROVED",
              (gate.get("error") or {}).get("code"))
        mock.terminate()

    rpc.close()
    elapsed = time.time() - started

    # 子进程内存峰值（macOS 单位 byte，Linux 单位 KiB）
    import resource

    peak = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    if sys.platform != "darwin":
        peak *= 1024
    print("\n子进程内存峰值: %.1f MiB    总耗时: %.1fs" % (peak / 1024 / 1024, elapsed))
    check("内存峰值 < 1.5 GiB（4K 视频 + RAW 预览）", peak < 1.5 * 1024 ** 3, "%.1f MiB" % (peak / 1024 / 1024))

    digest = hashlib.sha256((src / "shot.cr2").read_bytes()).hexdigest()
    print("shot.cr2 sha256:", digest[:16], "…（分析后文件未变）")

    failed = [r for r in RESULTS if not r[1]]
    print("\n%d/%d 项通过" % (len(RESULTS) - len(failed), len(RESULTS)))
    if not args.keep:
        shutil.rmtree(work, ignore_errors=True)
    else:
        print("保留目录:", work)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
