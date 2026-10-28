"""Scan a packaged app bundle for common embedded credential signatures.

The scanner deliberately reports only rule IDs and filenames, never matched
values. It is a release hygiene check, not a proof that no secret can exist.
"""
import argparse
import json
from pathlib import Path
import re
from typing import Any


PATTERNS: tuple[tuple[str, re.Pattern[bytes]], ...] = (
    ("private_key_pem", re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----")),
    ("aws_access_key_id", re.compile(rb"\bAKIA[0-9A-Z]{16}\b")),
    ("openai_api_key", re.compile(rb"\bsk-(?:proj-)?[A-Za-z0-9_-]{24,}\b")),
    ("github_token", re.compile(rb"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b")),
    ("slack_token", re.compile(rb"\bxox[baprs]-[A-Za-z0-9-]{20,}\b")),
    ("google_api_key", re.compile(rb"\bAIza[0-9A-Za-z_-]{35}\b")),
    (
        "named_secret_assignment",
        re.compile(
            rb"(?i)(?<![A-Z0-9_])(?:CLOUDBASE_API_KEY|TCB_SECRET_KEY|OPENAI_API_KEY|DASHSCOPE_API_KEY|"
            rb"AWS_SECRET_ACCESS_KEY|AWS_SESSION_TOKEN|AZURE_CLIENT_SECRET|GOOGLE_API_KEY|"
            rb"GITHUB_TOKEN|SLACK_BOT_TOKEN|BEARER_TOKEN)(?![A-Z0-9_])"
            rb"\s*[:=]\s*[\"']?[A-Za-z0-9._~+/=-]{24,}"
        ),
    ),
    (
        "bearer_credential",
        re.compile(rb"(?i)\bAuthorization\s*:\s*Bearer\s+[A-Za-z0-9._~+/=-]{32,}"),
    ),
)


def scan_bundle(root: Path) -> dict[str, Any]:
    root = root.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("scan target must be a directory")
    hits: dict[str, set[str]] = {rule: set() for rule, _ in PATTERNS}
    files_scanned = 0
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        try:
            resolved = path.resolve(strict=True)
            resolved.relative_to(root)
            data = path.read_bytes()
        except (OSError, ValueError):
            raise ValueError("bundle contains an unreadable or escaping file") from None
        files_scanned += 1
        relative = path.relative_to(root).as_posix()
        for rule, pattern in PATTERNS:
            if pattern.search(data):
                hits[rule].add(relative)
    findings = [
        {"rule": rule, "files": sorted(files)}
        for rule, files in hits.items()
        if files
    ]
    return {
        "status": "findings" if findings else "passed",
        "filesScanned": files_scanned,
        "findingCount": sum(len(item["files"]) for item in findings),
        "findings": findings,
        "coverage": [rule for rule, _ in PATTERNS],
        "limitations": "Checks common credential signatures and assignments; not an exhaustive secret proof.",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = scan_bundle(args.bundle_dir)
    except (OSError, ValueError) as exc:
        raise SystemExit(f"secret scan failed: {exc}") from None
    print(json.dumps(result, separators=(",", ":")))
    if result["findings"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
