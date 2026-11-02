#!/usr/bin/env python3
"""Click through Pocket's non-destructive Android UI smoke flow on an emulator."""

from __future__ import annotations

import re
import subprocess
import sys
import time
import traceback
import xml.etree.ElementTree as ET
from pathlib import Path


APP_ID = "com.yingxuapp.pocket"
ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "test-results" / "android-emulator"


def command(*args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["adb", *args], check=check, capture_output=True, text=True, timeout=30
    )
    return result.stdout.strip()


def hierarchy() -> ET.Element:
    RESULTS.mkdir(parents=True, exist_ok=True)
    last_error = "no attempt made"
    for attempt in range(1, 6):
        dump = subprocess.run(
            ["adb", "shell", "uiautomator", "dump", "/sdcard/window.xml"],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        read = subprocess.run(
            ["adb", "exec-out", "cat", "/sdcard/window.xml"],
            check=False,
            capture_output=True,
            timeout=30,
        )
        try:
            if dump.returncode:
                raise RuntimeError(f"uiautomator dump exited {dump.returncode}")
            if read.returncode:
                raise RuntimeError(f"adb cat exited {read.returncode}")
            root = ET.fromstring(read.stdout)
            if root.tag != "hierarchy":
                raise RuntimeError(f"unexpected XML root: {root.tag!r}")
            return root
        except (ET.ParseError, RuntimeError) as exc:
            last_error = (
                f"attempt={attempt}/5; error={exc}; "
                f"dump stdout={dump.stdout[-2000:]!r}; dump stderr={dump.stderr[-2000:]!r}; "
                f"read stdout prefix={read.stdout[:2000]!r}; read stderr={read.stderr[:2000]!r}"
            )
            (RESULTS / "hierarchy-read-failures.txt").write_text(
                last_error + "\n", encoding="utf-8"
            )
            time.sleep(0.8)
    raise RuntimeError(f"Could not read a valid UI hierarchy after retries: {last_error}")


def visible_texts(root: ET.Element) -> set[str]:
    return {
        value.strip()
        for node in root.iter()
        for value in (node.attrib.get("text", ""), node.attrib.get("content-desc", ""))
        if value.strip()
    }


def wait_for_text(expected: str, timeout: float = 25) -> ET.Element:
    deadline = time.monotonic() + timeout
    last_texts: set[str] = set()
    while time.monotonic() < deadline:
        root = hierarchy()
        last_texts = visible_texts(root)
        if expected in last_texts:
            return root
        time.sleep(1)
    raise AssertionError(f"Timed out waiting for {expected!r}; visible text: {sorted(last_texts)}")


def bounds_center(value: str) -> tuple[int, int] | None:
    match = re.fullmatch(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", value)
    if not match:
        return None
    left, top, right, bottom = map(int, match.groups())
    return (left + right) // 2, (top + bottom) // 2


def click_text(expected: str, timeout: float = 15) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        root = hierarchy()
        nodes = list(root.iter())
        parents = {child: parent for parent in nodes for child in parent}
        for node in nodes:
            label = node.attrib.get("text", "").strip() or node.attrib.get("content-desc", "").strip()
            if label != expected:
                continue
            target = node
            while target is not None and target.attrib.get("clickable") != "true":
                target = parents.get(target)
            if target is None:
                continue
            center = bounds_center(target.attrib.get("bounds", ""))
            if center:
                command("shell", "input", "tap", str(center[0]), str(center[1]))
                time.sleep(1.2)
                return
        time.sleep(1)
    raise AssertionError(f"Could not find a tappable UI element named {expected!r}")


def capture(name: str) -> None:
    RESULTS.mkdir(parents=True, exist_ok=True)
    with (RESULTS / f"{name}.png").open("wb") as screenshot:
        subprocess.run(
            ["adb", "exec-out", "screencap", "-p"],
            check=True,
            stdout=screenshot,
            timeout=30,
        )
    (RESULTS / f"{name}.xml").write_text(
        ET.tostring(hierarchy(), encoding="unicode"), encoding="utf-8"
    )


def assert_visible(*expected: str) -> None:
    texts = visible_texts(hierarchy())
    missing = [item for item in expected if item not in texts]
    if missing:
        raise AssertionError(f"Missing UI labels: {missing}; visible text: {sorted(texts)}")


def assert_absent(*unexpected: str) -> None:
    texts = visible_texts(hierarchy())
    present = [item for item in unexpected if item in texts]
    if present:
        raise AssertionError(f"Unexpected desktop controls visible on phone: {present}")


def run_smoke() -> None:
    apk = Path(sys.argv[1]).resolve()
    if not apk.is_file():
        raise SystemExit(f"APK not found: {apk}")
    command("install", "-r", str(apk))
    command("shell", "am", "force-stop", APP_ID, check=False)
    command("shell", "pm", "clear", APP_ID)
    command("shell", "monkey", "-p", APP_ID, "-c", "android.intent.category.LAUNCHER", "1")

    for label in ("跳过登录，仅使用本机备份", "手机本机备份"):
        try:
            wait_for_text(label, timeout=12)
            if label == "跳过登录，仅使用本机备份":
                click_text(label)
                wait_for_text("手机本机备份")
            break
        except AssertionError:
            if label == "手机本机备份":
                raise
    assert_visible("手机本机备份", "开始校验备份")
    capture("01-home")

    click_text("历史")
    assert_visible("本机历史", "异地进度与历史")
    capture("02-history")

    click_text("连接桌面端")
    assert_visible("连接影序桌面端")
    assert_absent("开始电脑备份", "暂停电脑备份", "继续电脑备份", "取消任务")
    capture("03-desktop-read-only")

    click_text("Agent")
    assert_visible("Agent", "AI 审片")
    assert_absent("UI 原型", "固定 mock 数据")
    capture("04-agent")

    click_text("AI 审片")
    assert_visible("AI 审片", "选择素材目录")
    capture("05-ai-review")
    command("shell", "input", "keyevent", "4")
    wait_for_text("Agent")

    click_text("设置")
    assert_visible("备份偏好", "软件更新")
    capture("06-settings")

    (RESULTS / "result.txt").write_text(
        "PASS: Android Emulator launch, login skip, home, local history and "
        "cross-device history screen, disconnected desktop page without task controls, "
        "Agent screen, AI review screen, and settings navigation.\n"
        "Not covered: authenticated account sync, real model requests, media permission "
        "and copy, paired desktop status/network, iOS behavior, and production distribution.\n",
        encoding="utf-8",
    )
    print((RESULTS / "result.txt").read_text(encoding="utf-8"))


def save_failure_evidence() -> None:
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / "failure.txt").write_text(traceback.format_exc(), encoding="utf-8")
    diagnostics = []
    try:
        with (RESULTS / "failure.png").open("wb") as screenshot:
            capture = subprocess.run(
                ["adb", "exec-out", "screencap", "-p"],
                check=False,
                stdout=screenshot,
                stderr=subprocess.PIPE,
                timeout=30,
            )
        diagnostics.append(f"## failure screenshot\nexit={capture.returncode}\nstderr={capture.stderr!r}")
    except Exception as exc:
        diagnostics.append(f"## failure screenshot\nerror={exc!r}")
    for label, args in (
        ("adb devices", ["adb", "devices", "-l"]),
        ("uiautomator dump", ["adb", "shell", "uiautomator", "dump", "/sdcard/failure.xml"]),
        ("hierarchy XML", ["adb", "exec-out", "cat", "/sdcard/failure.xml"]),
        ("logcat tail", ["adb", "logcat", "-d", "-t", "500"]),
    ):
        try:
            result = subprocess.run(args, check=False, capture_output=True, text=True, timeout=30)
            diagnostics.append(
                f"## {label}\nexit={result.returncode}\nstdout:\n{result.stdout[-20000:]}\nstderr:\n{result.stderr[-4000:]}"
            )
        except Exception as exc:
            diagnostics.append(f"## {label}\nerror={exc!r}")
    (RESULTS / "device-diagnostics.txt").write_text("\n\n".join(diagnostics), encoding="utf-8")


def main() -> None:
    RESULTS.mkdir(parents=True, exist_ok=True)
    try:
        run_smoke()
    except BaseException:
        save_failure_evidence()
        raise


if __name__ == "__main__":
    main()
