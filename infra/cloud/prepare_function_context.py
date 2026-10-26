from pathlib import Path
import shutil
import subprocess
import ast
import argparse
import hashlib
import json


ROOT = Path(__file__).resolve().parents[2]
SERVICE = ROOT / "cloud-service"
OUTPUT = Path(__file__).resolve().parent / "function-build"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--output-dir', type=Path, default=OUTPUT)
    args = parser.parse_args()
    output = args.output_dir.resolve()
    if not output.is_relative_to(Path(__file__).resolve().parent):
        parser.error('Output must stay inside infra/cloud')
    if output.exists():
        raise SystemExit(f"Refusing to overwrite existing function context: {output}; move it aside manually first.")
    output.mkdir(parents=True)
    shutil.copytree(SERVICE / "app", output / "app", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copytree(ROOT / "contracts", output / "contracts")
    lock = SERVICE / "requirements.linux.lock"
    shutil.copy2(lock, output / "requirements.txt")
    shutil.copy2(lock, output / "requirements.lock")
    shutil.copy2(Path(__file__).resolve().parent / "scf_bootstrap", output / "scf_bootstrap")
    (output / "scf_bootstrap").chmod(0o755)
    subprocess.run([
        "uv", "pip", "install", "--target", str(output / "third_party"),
        "--python-version", "3.11", "--python-platform", "x86_64-manylinux2014",
        "--only-binary", ":all:", "-r", str(lock),
    ], check=True)
    files = {str(path.relative_to(output)): hashlib.sha256(path.read_bytes()).hexdigest()
             for path in sorted(output.rglob('*')) if path.is_file()}
    tree = ast.parse((SERVICE / 'app/config.py').read_text())
    version = next(ast.literal_eval(node.value) for node in tree.body if isinstance(node, ast.Assign)
                   and any(isinstance(target, ast.Name) and target.id == 'DEFAULT_APP_VERSION' for target in node.targets))
    schema = next(ast.literal_eval(node.value) for node in tree.body if isinstance(node, ast.Assign)
                  and any(isinstance(target, ast.Name) and target.id == 'REQUIRED_SCHEMA_VERSION' for target in node.targets))
    build_id = hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()
    manifest = {'appVersion': version, 'buildId': build_id, 'schemaVersion': schema,
                'runtime': 'Python3.11', 'platform': 'x86_64-manylinux2014', 'files': files}
    (output / 'build-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(f"CloudBase HTTP function context prepared: {output}")
    print("Linux/Python 3.11 wheels are pinned and included; no credentials or unrelated workspace files are included.")


if __name__ == "__main__":
    main()
