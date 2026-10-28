#!/usr/bin/env python3
"""影序应用图标资产生成器（按 docs/品牌/影序应用图标设计任务书-v1.0-草稿.md）。

创意方向「叠影归序」：深森林绿底 + 三层错位米白画框（影像 → 层叠副本）+ 暖金光点。
不写文字、不用相机/云盘/文件夹拼贴、不做重渐变。

产出（可直接被 Tauri bundle 引用）：
  backend/tauri-host/icons/icon.png            1024 母版（方形、不预置圆角）
  backend/tauri-host/icons/{32,64,128,256,512,1024}.png
  backend/tauri-host/icons/128x128.png / 128x128@2x.png / 32x32.png
  backend/tauri-host/icons/icon.icns           macOS（圆角桌面变体，iconutil 打包）
  backend/tauri-host/icons/icon.ico            Windows（多尺寸）
  site/assets/icon-{180,192,512}.png           网页 favicon / PWA
  docs/品牌/预览/icon-preview.png               评审预览（各尺寸 + 灰度）

用法：python3 scripts/gen-icons.py
"""
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
ICON_DIR = ROOT / "backend" / "tauri-host" / "icons"
SITE_ASSETS = ROOT / "site" / "assets"
DESKTOP_UI_PUBLIC = ROOT / "desktop-ui" / "public"
PREVIEW_DIR = ROOT / "docs" / "品牌" / "预览"

FOREST = (39, 61, 49)        # #273D31 品牌主色
FOREST_DARK = (37, 58, 47)   # #253A2F 品牌深色
IVORY = (244, 242, 237)      # #F4F2ED 暖米白
GOLD = (221, 186, 118)       # #DDBA76 暖金（少量点缀）
SUPERSAMPLE = 4


def rounded(draw, box, radius, **kw):
    draw.rounded_rectangle(box, radius=radius, **kw)


def draw_mark(size, detail=3, round_corners=False, margin=0.0):
    """按尺寸决定细节层级：小尺寸只留主轮廓 + 金点，避免细节糊成一团。"""
    s = size * SUPERSAMPLE
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    pad = int(s * margin)
    body = [pad, pad, s - pad - 1, s - pad - 1]
    # 母版满幅直角（不预置平台圆角），桌面变体才加圆角与留边
    radius = int((s - 2 * pad) * 0.225) if round_corners else 0
    ImageDraw.Draw(img, "RGBA").rounded_rectangle(body, radius=radius, fill=FOREST + (255,))

    # 极轻的底部色阶，避免死平；不用重渐变
    shade = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    ImageDraw.Draw(shade).rounded_rectangle(
        [body[0], body[1] + int((s - 2 * pad) * 0.55), body[2], body[3]],
        radius=radius,
        fill=FOREST_DARK + (110,),
    )
    clip = Image.new("L", (s, s), 0)
    ImageDraw.Draw(clip).rounded_rectangle(body, radius=radius, fill=255)
    # 用 alpha_composite 做混合，composite 会整体替换导致底色丢失
    shade.putalpha(Image.composite(shade.getchannel("A"), Image.new("L", (s, s), 0), clip))
    img = Image.alpha_composite(img, shade)
    d = ImageDraw.Draw(img, "RGBA")

    field = s - 2 * pad
    card = int(field * 0.435)
    step = int(field * 0.082)
    frame_w = max(2, int(field * (0.052 if detail > 1 else 0.085)))
    cx = s // 2
    cy = s // 2 + int(field * 0.03)

    def box(dx, dy, side):
        x0 = cx - side // 2 + dx
        y0 = cy - side // 2 + dy
        return [x0, y0, max(x0 + 2, x0 + side), max(y0 + 2, y0 + side)]

    # 后两层：错位描边，表达「层叠副本」
    if detail >= 2:
        layers = 3 if detail >= 3 else 2
        for i in range(layers - 1, 0, -1):
            alpha = 60 + int(140 * (layers - i) / max(1, layers))
            d.rounded_rectangle(
                box(-step * i, -step * i, card),
                radius=int(field * 0.05),
                outline=IVORY + (alpha,),
                width=frame_w,
            )

    # 前景：一张「影像窗口」
    front = box(0, 0, card)
    if detail >= 3:
        d.rounded_rectangle(front, radius=int(field * 0.05), fill=IVORY + (255,))
        inset = max(2, int(field * 0.052))
        inner = [front[0] + inset, front[1] + inset, front[2] - inset, front[3] - inset]
        d.rounded_rectangle(inner, radius=int(field * 0.03), fill=FOREST_DARK + (255,))
    else:
        d.rounded_rectangle(front, radius=int(field * 0.05), outline=IVORY + (255,), width=frame_w)

    dot_r = max(2, int(field * (0.05 if detail > 1 else 0.075)))
    dcx = (front[0] + front[2]) // 2
    dcy = (front[1] + front[3]) // 2
    d.ellipse([dcx - dot_r, dcy - dot_r, dcx + dot_r, dcy + dot_r], fill=GOLD + (255,))
    return img.resize((size, size), Image.LANCZOS)


def export_png(img, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, "PNG", optimize=True)
    return path


def build_icns(master_rounded, out):
    """用 iconutil 打包 macOS icns；缺 iconutil 时跳过并如实提示。"""
    import tempfile

    # iconutil 要求目录名以 .iconset 结尾
    iconset = Path(tempfile.mkdtemp(prefix="yingxu-icon-")) / "icon.iconset"
    iconset.mkdir()
    for base in (16, 32, 128, 256, 512):
        for scale in (1, 2):
            px = base * scale
            name = "icon_%dx%d%s.png" % (base, base, "@2x" if scale == 2 else "")
            master_rounded.resize((px, px), Image.LANCZOS).save(iconset / name, "PNG")
    r = subprocess.run(
        ["iconutil", "-c", "icns", str(iconset), "-o", str(out)],
        capture_output=True, text=True,
    )
    return r.returncode == 0, r.stderr.strip()


def build_preview(master_square, master_rounded):
    tiles = []
    for px in (1024, 256, 128, 64, 32):
        icon = master_square if px in (1024, 256) else master_rounded
        tiles.append(icon.resize((px, px), Image.LANCZOS))
    pad = 24
    w = sum(t.width for t in tiles) + pad * (len(tiles) + 1)
    h = 1024 + pad * 2
    sheet = Image.new("RGB", (w, h), (255, 255, 255))
    x = pad
    for t in tiles:
        sheet.paste(t, (x, pad + (1024 - t.height) // 2), t if t.mode == "RGBA" else None)
        x += t.width + pad
    gray = master_rounded.convert("L").resize((1024, 1024), Image.LANCZOS).convert("RGB")
    out = Image.new("RGB", (w + 1024 + pad, h), (255, 255, 255))
    out.paste(sheet, (0, 0))
    out.paste(gray, (w, pad))
    export_png(out, PREVIEW_DIR / "icon-preview.png")
    return out


def main():
    ICON_DIR.mkdir(parents=True, exist_ok=True)
    square = draw_mark(1024, detail=3)
    rounded_master = draw_mark(1024, detail=3, round_corners=True, margin=0.09)

    export_png(square, ICON_DIR / "icon.png")
    for px in (32, 64, 128, 256, 512, 1024):
        detail = 1 if px <= 48 else (2 if px <= 128 else 3)
        export_png(draw_mark(px, detail=detail), ICON_DIR / ("%d.png" % px))

    # Tauri 默认约定的文件名
    export_png(draw_mark(32, detail=1), ICON_DIR / "32x32.png")
    export_png(draw_mark(128, detail=3), ICON_DIR / "128x128.png")
    export_png(draw_mark(256, detail=3), ICON_DIR / "128x128@2x.png")
    # 桌面变体（macOS/Windows 图标习惯圆角 + 留边）
    for px in (32, 64, 128, 256, 512, 1024):
        detail = 1 if px <= 48 else (2 if px <= 128 else 3)
        export_png(
            draw_mark(px, detail=detail, round_corners=True, margin=0.09),
            ICON_DIR / ("desktop-%d.png" % px),
        )

    ok, err = build_icns(rounded_master, ICON_DIR / "icon.icns")
    print("icns:", "ok" if ok else "跳过 - " + (err or "缺少 iconutil"))

    ico_sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    ico_images = [draw_mark(max(w, h), detail=1 if w <= 48 else 3,
                            round_corners=True, margin=0.09).resize((w, h), Image.LANCZOS)
                  for w, h in ico_sizes]
    ico_images[-1].save(
        ICON_DIR / "icon.ico", format="ICO",
        sizes=ico_sizes, append_images=ico_images[:-1],
    )
    print("ico: ok")

    SITE_ASSETS.mkdir(parents=True, exist_ok=True)
    DESKTOP_UI_PUBLIC.mkdir(parents=True, exist_ok=True)
    export_png(draw_mark(512, detail=3, round_corners=True, margin=0.06), DESKTOP_UI_PUBLIC / "yingxu-app-icon.png")
    export_png(draw_mark(180, detail=3, round_corners=True, margin=0.06), SITE_ASSETS / "icon-180.png")
    export_png(draw_mark(192, detail=3, round_corners=True, margin=0.06), SITE_ASSETS / "icon-192.png")
    export_png(draw_mark(512, detail=3, round_corners=True, margin=0.06), SITE_ASSETS / "icon-512.png")
    export_png(draw_mark(32, detail=1), SITE_ASSETS / "favicon-32.png")

    preview = build_preview(square, rounded_master)
    print("母版:", ICON_DIR / "icon.png", square.size)
    print("预览:", PREVIEW_DIR / "icon-preview.png", preview.size)
    sizes = sorted(p.name for p in ICON_DIR.iterdir() if p.suffix in (".png", ".icns", ".ico"))
    print("图标目录:", len(sizes), "个文件")
    for s in sizes:
        print("  ", s, (ICON_DIR / s).stat().st_size, "bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
