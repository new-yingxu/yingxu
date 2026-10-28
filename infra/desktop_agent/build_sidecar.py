"""Build the local LangGraph Agent as a platform-specific Tauri sidecar folder."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
SERVICE = ROOT / "cloud-service"
APP_NAME = "yingxu-local-agent"
PINNED_PYINSTALLER = "6.22.3"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def executable_name(platform_name: str | None = None) -> str:
    target_platform = platform_name or sys.platform
    return APP_NAME + (".exe" if target_platform == "win32" else "")


def stage_bundle(bundle: Path, destination: Path) -> Path:
    bundle = bundle.resolve(strict=True)
    destination = destination.resolve()
    if destination.exists():
        raise SystemExit(f"refusing to overwrite existing sidecar resource: {destination}")
    if destination == bundle or destination.is_relative_to(bundle):
        raise SystemExit("refusing to stage a sidecar bundle inside its own source directory")
    # Validate the complete one-folder package before it becomes a Tauri resource.
    # The verifier keeps cryptography imports inside the runtime smoke function,
    # so build staging needs no model/runtime dependency beyond the standard library.
    from verify_sidecar import verify_manifest

    try:
        verify_manifest(bundle)
    except RuntimeError as exc:
        raise SystemExit(f"refusing to stage invalid sidecar bundle: {exc}") from None
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(bundle, destination)
    return destination


def build(output: Path) -> Path:
    try:
        from PyInstaller.__main__ import run as pyinstaller_run
        from PyInstaller import __version__ as pyinstaller_version
    except ImportError as exc:
        raise SystemExit("install cloud-service/requirements.desktop-build.txt into the runtime build environment") from exc
    if pyinstaller_version != PINNED_PYINSTALLER:
        raise SystemExit(f"PyInstaller {PINNED_PYINSTALLER} required; found {pyinstaller_version}")
    output = output.resolve()
    bundle = output / APP_NAME
    if output.exists():
        raise SystemExit(f"refusing to overwrite existing build output: {output}")
    output.mkdir(parents=True)
    work = output / ".pyinstaller-work"
    spec = output / ".pyinstaller-spec"
    command = [
        str(SERVICE / "app" / "local_agent_runner.py"),
        "--name", APP_NAME,
        "--onedir",
        "--console",
        "--noconfirm",
        "--clean",
        "--paths", str(SERVICE),
        "--distpath", str(output),
        "--workpath", str(work),
        "--specpath", str(spec),
        "--collect-submodules", "app",
        "--collect-all", "langgraph",
        "--collect-all", "langchain_core",
        "--collect-all", "langsmith",
        "--collect-all", "fastapi",
        "--collect-all", "uvicorn",
        "--collect-all", "sqlalchemy",
        "--hidden-import", "app.local_agent_app",
        "--hidden-import", "uvicorn.logging",
        "--hidden-import", "uvicorn.loops.auto",
        "--hidden-import", "uvicorn.protocols.http.auto",
        "--hidden-import", "uvicorn.protocols.websockets.auto",
    ]
    old_cwd = Path.cwd()
    try:
        os.chdir(SERVICE)
        pyinstaller_run(command)
    finally:
        os.chdir(old_cwd)
    binary_name = executable_name()
    executable = bundle / binary_name
    if not executable.is_file():
        raise SystemExit(f"sidecar executable missing after build: {executable}")
    files = [path for path in bundle.rglob("*") if path.is_file()]
    manifest = {
        "name": APP_NAME,
        "architecture": platform.machine(),
        "platform": sys.platform,
        "python": platform.python_version(),
        "pyinstaller": pyinstaller_version,
        "signedOrNotarized": False,
        "entrypoint": binary_name,
        "files": [
            {"path": path.relative_to(bundle).as_posix(), "size": path.stat().st_size, "sha256": sha256(path)}
            for path in sorted(files)
        ],
    }
    (bundle / "sidecar-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    shutil.rmtree(work, ignore_errors=True)
    shutil.rmtree(spec, ignore_errors=True)
    return bundle


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--stage-to", type=Path, help="copy the complete one-folder bundle to a new resource directory")
    args = parser.parse_args()
    bundle = build(args.output_dir)
    staged = stage_bundle(bundle, args.stage_to) if args.stage_to else None
    print(json.dumps({
        "status": "built",
        "bundle": str(bundle),
        "executable": str(bundle / executable_name()),
        "stagedBundle": str(staged) if staged else None,
        "platform": sys.platform,
        "architecture": platform.machine(),
        "manifest": str(bundle / "sidecar-manifest.json"),
    }, separators=(",", ":")))


if __name__ == "__main__":
    main()
