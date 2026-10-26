from pathlib import Path
import shutil


REPO_ROOT = Path(__file__).resolve().parents[2]
SERVICE = REPO_ROOT / "cloud-service"
OUTPUT = Path(__file__).resolve().parent / "build"


def main() -> None:
    if OUTPUT.exists():
        raise SystemExit(f"Refusing to overwrite existing build context: {OUTPUT}; move it aside manually first.")
    OUTPUT.mkdir(parents=True)
    for name in ("app", "requirements.txt", "requirements.lock", "Dockerfile", ".dockerignore"):
        source = SERVICE / name
        if source.is_dir():
            shutil.copytree(source, OUTPUT / name, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        else:
            shutil.copy2(source, OUTPUT / name)
    shutil.copytree(REPO_ROOT / "contracts", OUTPUT / "contracts")
    print(f"CloudBase source context prepared: {OUTPUT}")
    print("Review this directory before deployment; it excludes credentials and unrelated workspace files.")


if __name__ == "__main__":
    main()
