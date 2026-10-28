#!/usr/bin/env python3
"""Analyze an explicitly selected removable volume without writing to its source."""

import argparse
import collections
import hashlib
import json
import pathlib
import subprocess
import tempfile
import time


PHOTO_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".cr2", ".cr3", ".crw", ".nef", ".nrw",
    ".arw", ".srf", ".sr2", ".raf", ".dng", ".orf", ".rw2", ".raw",
    ".pef", ".srw", ".x3f", ".3fr", ".fff", ".iiq", ".mos", ".mrw",
    ".erf", ".kdc", ".dcr", ".rwl",
}


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            value.update(chunk)
    return value.hexdigest()


def rpc(process, method, params, sequence):
    sequence += 1
    process.stdin.write(json.dumps({
        "requestId": str(sequence), "version": 1,
        "method": method, "params": params,
    }) + "\n")
    process.stdin.flush()
    reply = json.loads(process.stdout.readline())
    if not reply["ok"]:
        raise RuntimeError(reply["error"]["code"])
    return reply.get("data"), sequence


parser = argparse.ArgumentParser()
parser.add_argument("--source", required=True)
parser.add_argument("--evidence", required=True)
args = parser.parse_args()

source = pathlib.Path(args.source).resolve(strict=True)
binary = pathlib.Path(__file__).resolve().parents[1] / "target/release/yingxu-core"
photos = sorted(
    path for path in source.rglob("*")
    if path.is_file()
    and not path.is_symlink()
    and not any(part.startswith(".") for part in path.relative_to(source).parts)
    and path.suffix.lower() in PHOTO_EXTENSIONS
)
formats = collections.Counter(path.suffix.lower() for path in photos)
stat_before = {
    path: (path.stat().st_size, path.stat().st_mtime_ns, path.stat().st_ino)
    for path in photos
}
start = time.monotonic()
result = {
    "sourceType": "user-authorized removable volume",
    "photoFileCount": len(photos),
    "photoFormats": dict(formats),
    "photoBytes": sum(value[0] for value in stat_before.values()),
    "privatePathsIncluded": False,
    "networkUsed": False,
}

with tempfile.TemporaryDirectory(prefix="yingxu-readonly-card-") as temporary:
    root = pathlib.Path(temporary).resolve()
    destination = root / "copy"
    destination.mkdir()
    process = subprocess.Popen(
        [str(binary), "--data-dir", str(root / "db"), "rpc"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    sequence = 0
    try:
        result["index"], sequence = rpc(
            process, "library.index", {"source": str(source)}, sequence
        )
        analysis_by_extension = collections.defaultdict(collections.Counter)
        decode_reasons = collections.Counter()
        quality = collections.Counter()
        hints = collections.Counter()
        timestamps = collections.Counter()
        errors = collections.Counter()
        thumbnail_checks = 0
        analyzed_ids = set()

        # Search by suffix to stay below library.search's 500-row ceiling.
        for extension in sorted(formats):
            rows, sequence = rpc(process, "library.search", {
                "query": extension, "kind": "photo", "quality": "all",
            }, sequence)
            for row in rows:
                media_id = row["id"]
                if media_id in analyzed_ids:
                    continue
                analyzed_ids.add(media_id)
                try:
                    response, sequence = rpc(
                        process, "library.analyze", {"mediaId": media_id}, sequence
                    )
                    metadata = response["metadata"]
                    actual_extension = pathlib.Path(row["name"]).suffix.lower()
                    analysis_by_extension[actual_extension][metadata["decodeStatus"]] += 1
                    if metadata.get("decodeReason"):
                        decode_reasons[metadata["decodeReason"]] += 1
                    quality[response["qualityState"]] += 1
                    timestamps[metadata.get("timestampSource", "unknown")] += 1
                    hints.update(metadata.get("quality", {}).get("candidates", []))
                    if response["thumbnailAvailable"] and thumbnail_checks < 3:
                        thumbnail, sequence = rpc(
                            process, "library.thumbnail", {"mediaId": media_id}, sequence
                        )
                        assert thumbnail["maxEdge"] == 512
                        assert thumbnail["exifIncluded"] is False
                        assert len(thumbnail["bytes"]) < 1024 * 1024
                        thumbnail_checks += 1
                except RuntimeError as error:
                    analysis_by_extension[extension][f"error:{error}"] += 1
                    errors[str(error)] += 1

        result.update({
            "analyzedPhotoCount": len(analyzed_ids),
            "analysisByExtension": {
                extension: dict(counts)
                for extension, counts in sorted(analysis_by_extension.items())
            },
            "decodeReasons": dict(decode_reasons),
            "qualityCandidates": dict(quality),
            "signalCounts": dict(hints),
            "timestampSources": dict(timestamps),
            "analysisErrors": dict(errors),
            "thumbnailReadChecks": thumbnail_checks,
            "qualityAccuracy": "not_established_no_ground_truth_labels",
        })

        # Verify one representative JPEG; large RAWs are analyzed but not copied
        # by this smoke test, so the test does not unexpectedly consume disk space.
        jpeg = next((path for path in photos if path.suffix.lower() in {".jpg", ".jpeg"}), None)
        if jpeg:
            plan, sequence = rpc(process, "backup.preview", {
                "source": str(source), "destinations": [str(destination)],
                "name": "Read-only card verification", "verify": True,
                "selected": [str(jpeg.relative_to(source))],
            }, sequence)
            job, sequence = rpc(process, "jobs.start", {
                "planId": plan["id"], "confirmed": True,
                "idempotencyKey": "card-check",
            }, sequence)
            completed, sequence = rpc(process, "worker.runNext", {}, sequence)
            assert completed["state"] == "success"
            item = completed["items"][0]
            matches = digest(jpeg) == digest(pathlib.Path(item["output"]))
            result["sampleBackup"] = {
                "files": 1,
                "state": completed["state"],
                "verified": completed["targets"][0]["verified"],
                "independentSha256Match": matches,
            }
    finally:
        process.stdin.close()
        process.wait(timeout=10)

result["sourceMetadataUnchanged"] = all(
    (path.stat().st_size, path.stat().st_mtime_ns, path.stat().st_ino) == before
    for path, before in stat_before.items()
)
result["elapsedSeconds"] = round(time.monotonic() - start, 2)
assert result["sourceMetadataUnchanged"]
pathlib.Path(args.evidence).write_text(
    json.dumps(result, ensure_ascii=False, indent=2) + "\n"
)
print(json.dumps(result, ensure_ascii=False))
