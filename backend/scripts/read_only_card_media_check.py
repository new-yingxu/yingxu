#!/usr/bin/env python3
"""Read-only smoke check for one JPEG, RAW and MP4 on an explicitly selected card.

The card is scanned/indexed read-only. Database rows and derived thumbnails are
written only below a temporary directory on the Mac. Evidence contains aggregate
counts and analyzer states, never source paths, filenames, EXIF or image bytes.
"""
import argparse
import collections
import hashlib
import json
import pathlib
import subprocess
import tempfile
import time

parser = argparse.ArgumentParser()
parser.add_argument("--source", required=True)
parser.add_argument("--evidence", required=True)
args = parser.parse_args()
source = pathlib.Path(args.source).resolve(strict=True)
binary = pathlib.Path(__file__).resolve().parents[1] / "target/release/yingxu-core"
if not binary.is_file():
    raise SystemExit("release CLI not built")

files = sorted(
    p for p in source.rglob("*")
    if p.is_file() and not p.is_symlink()
)
counts = collections.Counter(p.suffix.lower() or "[no extension]" for p in files)
before = {p: (p.stat().st_size, p.stat().st_mtime_ns) for p in files}
result = {
    "sourceType": "user-authorized removable card",
    "indexedFileCount": len(files),
    "formatCounts": dict(counts),
    "networkUsed": False,
    "privatePathsIncluded": False,
    "qualityAccuracy": "not_established_no_ground_truth_labels",
}
start = time.monotonic()

with tempfile.TemporaryDirectory(prefix="yingxu-card-media-", dir="/private/var/tmp") as tmp:
    process = subprocess.Popen(
        [str(binary), "--data-dir", str(pathlib.Path(tmp) / "db"), "rpc"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
    )
    request_number = 0

    def rpc(method, params):
        nonlocal_request[0] += 1
        payload = {"requestId": str(nonlocal_request[0]), "version": 1, "method": method, "params": params}
        process.stdin.write(json.dumps(payload) + "\n")
        process.stdin.flush()
        reply = json.loads(process.stdout.readline())
        if not reply.get("ok"):
            raise RuntimeError(reply.get("error", {}).get("code", "UNKNOWN"))
        return reply["data"]

    nonlocal_request = [request_number]
    try:
        sample_files = {
            label: next((p for p in files if p.suffix.lower() == extension), None)
            for label, extension, _, _ in [
                ("jpeg", ".jpg", "photo", "library.analyzePhoto"),
                ("raw", ".dng", "photo", "library.analyzeRaw"),
                ("video", ".mp4", "video", "video.analyze"),
            ]
        }
        def digest(path):
            hasher = hashlib.sha256()
            with path.open("rb") as stream:
                while chunk := stream.read(1024 * 1024):
                    hasher.update(chunk)
            return hasher.hexdigest()
        sample_digests = {label: digest(path) for label, path in sample_files.items() if path is not None}
        indexed = rpc("library.index", {"source": str(source)})
        result["index"] = {
            "files": indexed.get("files"),
            "changed": indexed.get("changed"),
            "sourceReadOnly": indexed.get("sourceReadOnly"),
            "copyStarted": indexed.get("copyStarted"),
        }
        checks = [
            ("jpeg", ".jpg", "photo", "library.analyzePhoto"),
            ("raw", ".dng", "photo", "library.analyzeRaw"),
            ("video", ".mp4", "video", "video.analyze"),
        ]
        results = {}
        for label, extension, kind, method in checks:
            candidate = sample_files[label]
            if candidate is None:
                results[label] = {"available": False}
                continue
            query = extension.lstrip(".")
            rows = rpc("library.search", {"query": query, "kind": kind, "quality": "all"})
            row = next((item for item in rows if pathlib.Path(item["path"]).suffix.lower() == extension), None)
            if row is None:
                results[label] = {"available": True, "indexed": False}
                continue
            try:
                if label == "video":
                    response = rpc(method, {"mediaId": row["id"], "extractFrames": True})
                else:
                    response = rpc(method, {"mediaId": row["id"]})
                metadata = response.get("metadata", {})
                results[label] = {
                    "available": True,
                    "indexed": True,
                    "analysisSucceeded": True,
                    "qualityState": response.get("qualityState"),
                    "decodeStatus": metadata.get("decodeStatus"),
                    "rawFormat": response.get("rawFormat"),
                    "previewSource": response.get("previewSource"),
                    "thumbnailAvailable": response.get("thumbnailAvailable"),
                    "frameSource": response.get("frameSource"),
                    "framesAnalyzed": metadata.get("framesAnalyzed"),
                }
            except RuntimeError as exc:
                results[label] = {"available": True, "indexed": True, "analysisSucceeded": False, "errorCode": str(exc)}
        result["sampleAnalysis"] = results
    finally:
        process.stdin.close()
        process.wait(timeout=10)

result["sourceStatUnchanged"] = all(
    (p.stat().st_size, p.stat().st_mtime_ns) == stat for p, stat in before.items()
)
result["sampleContentUnchanged"] = all(
    digest(path) == sample_digests[label]
    for label, path in sample_files.items() if path is not None
)
result["elapsedSeconds"] = round(time.monotonic() - start, 2)
assert result["sourceStatUnchanged"] and result["sampleContentUnchanged"]
pathlib.Path(args.evidence).write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
print(json.dumps(result, ensure_ascii=False))
