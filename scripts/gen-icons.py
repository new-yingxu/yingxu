#!/usr/bin/env python3
"""Derive every Yingxu app icon from the user-approved 512 px source artwork.

The generator only resizes/converts the approved image. It does not redraw it.
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
DESKTOP_UI_PUBLIC = ROOT / "desktop-ui" / "public"
PREVIEW = ROOT / "docs" / "品牌" / "预览" / "icon-preview.png"
RESAMPLE = Image.Resampling.LANCZOS


def scaled(source: Image.Image, size: int) -> Image.Image:
    return source.resize((size, size), RESAMPLE)


def save_png(source: Image.Image, path: Path, size: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    scaled(source, size).save(path, "PNG", optimize=True)


def build_icns(source: Image.Image, output: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="yingxu-icon-") as temp:
        iconset = Path(temp) / "icon.iconset"
        iconset.mkdir()
        for base in (16, 32, 128, 256, 512):
            for scale in (1, 2):
                px = base * scale
                suffix = "@2x" if scale == 2 else ""
                scaled(source, px).save(iconset / f"icon_{base}x{base}{suffix}.png", "PNG")
        result = subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(output)], capture_output=True, text=True)
        if result.returncode:
            raise RuntimeError(result.stderr.strip() or "iconutil failed")


def main() -> int:
    if not SOURCE.is_file():
        raise SystemExit(f"approved icon source missing: {SOURCE}")
    source = Image.open(SOURCE).convert("RGBA")
    if source.size != (512, 512):
        raise SystemExit(f"approved icon source must be 512x512, got {source.size}")

    ICON_DIR.mkdir(parents=True, exist_ok=True)
    for px in (32, 64, 128, 256, 512, 1024):
        save_png(source, ICON_DIR / f"{px}.png", px)
        save_png(source, ICON_DIR / f"desktop-{px}.png", px)
    for name, px in (("icon.png", 1024), ("32x32.png", 32), ("128x128.png", 128), ("128x128@2x.png", 256)):
        save_png(source, ICON_DIR / name, px)
    build_icns(source, ICON_DIR / "icon.icns")
    source.save(ICON_DIR / "icon.ico", format="ICO", sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])

    DESKTOP_UI_PUBLIC.mkdir(parents=True, exist_ok=True)
    save_png(source, DESKTOP_UI_PUBLIC / "yingxu-app-icon.png", 512)
    SITE_ASSETS.mkdir(parents=True, exist_ok=True)
    for name, px in (("icon-180.png",180),("icon-192.png",192),("icon-512.png",512),("favicon-32.png",32)):
        save_png(source, SITE_ASSETS / name, px)
    source.save(SITE_ASSETS.parent / "favicon.ico", format="ICO", sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])
    ICON_DIR.joinpath("tray-icon.rgba").write_bytes(scaled(source, 32).tobytes())
    PREVIEW.parent.mkdir(parents=True, exist_ok=True)
    source.save(PREVIEW, "PNG", optimize=True)
    print(f"Updated all desktop, web and installer icons from {SOURCE}")
    print(f"Generated {len(list(ICON_DIR.glob('*.png'))) + 2} Tauri icon files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
