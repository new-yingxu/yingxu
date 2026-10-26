#!/usr/bin/env python3
"""影序应用图标资产生成器（以设计源图为唯一母版）。

母版：`docs/品牌/影序图标-源图-512.png`（512×512 RGBA，圆角方形，透明四角）。
设计：深墨绿底 + 柠檬绿叠层卡片 + 米白卡片与「Ai」字标。

所有尺寸都从母版派生，不再用代码重绘，保证各端图标与设计稿一致：

  backend/tauri-host/icons/icon.png            1024 母版（512 源图 2× LANCZOS）
  backend/tauri-host/icons/{32,64,128,256,512,1024}.png
  backend/tauri-host/icons/{32x32,128x128,128x128@2x}.png   Tauri 约定名
  backend/tauri-host/icons/desktop-*.png        macOS/Windows 桌面变体（留 8% 边距）
  backend/tauri-host/icons/icon.icns            macOS（iconutil）
  backend/tauri-host/icons/icon.ico             Windows 多尺寸
  site/assets/icon-{180,192,512}.png、favicon-32.png、site/favicon.ico
  docs/品牌/预览/icon-preview.png               评审预览（各尺寸 + 灰度）

用法：python3 scripts/gen-icons.py
"""
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "docs" / "品牌" / "影序图标-源图-512.png"
ICON_DIR = ROOT / "backend" / "tauri-host" / "icons"
SITE_ASSETS = ROOT / "site" / "assets"
PREVIEW_DIR = ROOT / "docs" / "品牌" / "预览"

MASTER = 1024
# 桌面图标传统上四周留白（macOS 网格 / Windows 任务栏），母版本身是满幅圆角方形
DESKTOP_MARGIN = 0.08


def load_master():
    if not SOURCE.exists():
        sys.exit(f"缺少设计源图: {SOURCE}")
    src = Image.open(SOURCE).convert("RGBA")
    if src.width != src.height:
        sys.exit(f"源图必须是正方形，当前 {src.size}")
    if src.width >= MASTER:
        return src.resize((MASTER, MASTER), Image.LANCZOS)
    # 512 → 1024 是 2× 整数放大，平面色块几乎无损
    return src.resize((MASTER, MASTER), Image.LANCZOS)


def with_margin(master, ratio):
    side = int(MASTER * (1 - 2 * ratio))
    inner = master.resize((side, side), Image.LANCZOS)
    canvas = Image.new("RGBA", (MASTER, MASTER), (0, 0, 0, 0))
    canvas.paste(inner, ((MASTER - side) // 2, (MASTER - side) // 2))
    return canvas


def export_png(img, path, size=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    out = img if size is None else img.resize((size, size), Image.LANCZOS)
    out.save(path, "PNG", optimize=True)
    return path


def build_icns(master, out):
    """iconutil 要求目录名以 .iconset 结尾。"""
    iconset = Path(tempfile.mkdtemp(prefix="yingxu-icon-")) / "icon.iconset"
    iconset.mkdir()
    for base in (16, 32, 128, 256, 512):
        for scale in (1, 2):
            px = base * scale
            name = "icon_%dx%d%s.png" % (base, base, "@2x" if scale == 2 else "")
            master.resize((px, px), Image.LANCZOS).save(iconset / name, "PNG")
    r = subprocess.run(
        ["iconutil", "-c", "icns", str(iconset), "-o", str(out)],
        capture_output=True, text=True,
    )
    return r.returncode == 0, r.stderr.strip()


def build_preview(master, desktop):
    tiles = [master.resize((px, px), Image.LANCZOS) for px in (512, 256, 128, 64, 32)]
    pad = 24
    w = sum(t.width for t in tiles) + pad * (len(tiles) + 1)
    h = 512 + pad * 2
    sheet = Image.new("RGB", (w + 512 + pad, h), (255, 255, 255))
    x = pad
    for t in tiles:
        sheet.paste(t, (x, pad + (512 - t.height) // 2), t)
        x += t.width + pad
    gray = desktop.convert("L").resize((512, 512), Image.LANCZOS).convert("RGB")
    sheet.paste(gray, (w, pad))
    export_png(sheet, PREVIEW_DIR / "icon-preview.png")
    return sheet


def main():
    master = load_master()
    desktop = with_margin(master, DESKTOP_MARGIN)
    ICON_DIR.mkdir(parents=True, exist_ok=True)

    export_png(master, ICON_DIR / "icon.png")
    for px in (32, 64, 128, 256, 512, 1024):
        export_png(master, ICON_DIR / ("%d.png" % px), px)

    # Tauri 默认约定的文件名
    export_png(master, ICON_DIR / "32x32.png", 32)
    export_png(master, ICON_DIR / "128x128.png", 128)
    export_png(master, ICON_DIR / "128x128@2x.png", 256)

    # 桌面变体：带留白，供 icns / ico 使用
    for px in (32, 64, 128, 256, 512, 1024):
        export_png(desktop, ICON_DIR / ("desktop-%d.png" % px), px)

    ok, err = build_icns(desktop, ICON_DIR / "icon.icns")
    print("icns:", "ok" if ok else "跳过 - " + (err or "缺少 iconutil"))

    ico_sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    ico_images = [desktop.resize(s, Image.LANCZOS) for s in ico_sizes]
    ico_images[-1].save(
        ICON_DIR / "icon.ico", format="ICO", sizes=ico_sizes, append_images=ico_images[:-1]
    )
    print("ico: ok")

    SITE_ASSETS.mkdir(parents=True, exist_ok=True)
    for px in (180, 192, 512):
        export_png(master, SITE_ASSETS / ("icon-%d.png" % px), px)
    export_png(master, SITE_ASSETS / "favicon-32.png", 32)
    fav = [master.resize((n, n), Image.LANCZOS) for n in (16, 32, 48)]
    fav[-1].save(
        ROOT / "site" / "favicon.ico",
        format="ICO",
        sizes=[(16, 16), (32, 32), (48, 48)],
        append_images=fav[:-1],
    )

    preview = build_preview(master, desktop)
    print("母版:", ICON_DIR / "icon.png", master.size)
    print("源图:", SOURCE, Image.open(SOURCE).size)
    print("预览:", PREVIEW_DIR / "icon-preview.png", preview.size)
    files = sorted(p for p in ICON_DIR.iterdir() if p.suffix in (".png", ".icns", ".ico"))
    print("图标目录:", len(files), "个文件")
    for p in files:
        print("   %-20s %8d bytes" % (p.name, p.stat().st_size))
    return 0


if __name__ == "__main__":
    sys.exit(main())
