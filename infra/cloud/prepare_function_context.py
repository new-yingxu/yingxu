from pathlib import Path
import shutil
import subprocess


ROOT = Path(__file__).resolve().parents[2]
SERVICE = ROOT / "cloud-service"
OUTPUT = Path(__file__).resolve().parent / "function-build"


def main() -> None:
    if OUTPUT.exists():
        raise SystemExit(f"Refusing to overwrite existing function context: {OUTPUT}; move it aside manually first.")
    OUTPUT.mkdir(parents=True)
    shutil.copytree(SERVICE / "app", OUTPUT / "app", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copytree(ROOT / "contracts", OUTPUT / "contracts")
    lock = SERVICE / "requirements.linux.lock"
    shutil.copy2(lock, OUTPUT / "requirements.txt")
    shutil.copy2(lock, OUTPUT / "requirements.lock")
    shutil.copy2(Path(__file__).resolve().parent / "scf_bootstrap", OUTPUT / "scf_bootstrap")
    (OUTPUT / "scf_bootstrap").chmod(0o755)
    subprocess.run([
        "uv", "pip", "install", "--target", str(OUTPUT / "third_party"),
        "--python-version", "3.11", "--python-platform", "x86_64-manylinux2014",
        "--only-binary", ":all:", "-r", str(lock),
    ], check=True)
    print(f"CloudBase HTTP function context prepared: {OUTPUT}")
    print("Linux/Python 3.11 wheels are pinned and included; no credentials or unrelated workspace files are included.")


if __name__ == "__main__":
    main()
