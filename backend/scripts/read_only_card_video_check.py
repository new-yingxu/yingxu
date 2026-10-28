#!/usr/bin/env python3
"""Probe and analyze MP4 files on an explicitly selected volume, read-only."""

import argparse
import collections
import json
import pathlib
import shutil
import subprocess
import tempfile
import time


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
videos = sorted(
    path for path in source.rglob("*")
    if path.is_file()
    and not path.is_symlink()
    and not any(part.startswith(".") for part in path.relative_to(source).parts)
    and path.suffix.lower() == ".mp4"
)
stat_before = {
    path: (path.stat().st_size, path.stat().st_mtime_ns, path.stat().st_ino)
    for path in videos
}
start = time.monotonic()
result = {
    "sourceType": "user-authorized removable volume",
    "videoFileCount": len(videos),
    "containerFormats": {".mp4": len(videos)},
    "videoBytes": sum(value[0] for value in stat_before.values()),
    "ffmpegAvailableOnTestHost": shutil.which("ffmpeg") is not None,
    "privatePathsIncluded": False,
    "networkUsed": False,
}

with tempfile.TemporaryDirectory(prefix="yingxu-readonly-video-") as temporary:
    root = pathlib.Path(temporary).resolve()
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
        rows, sequence = rpc(process, "library.search", {
            "query": ".mp4", "kind": "video", "quality": "all",
        }, sequence)
        probe_states = collections.Counter()
        durations = []
        resolutions = collections.Counter()
        for row in rows:
            try:
                probe, sequence = rpc(
                    process, "video.probe", {"mediaId": row["id"]}, sequence
                )
                metadata = probe["metadata"]
                probe_states["metadata_read"] += 1
                durations.append(metadata["durationMs"])
                resolutions[f'{metadata["width"]}x{metadata["height"]}'] += 1
            except RuntimeError as error:
                probe_states[f"error:{error}"] += 1

        analysis_states = collections.Counter()
        frame_sources = collections.Counter()
        frames_analyzed = collections.Counter()
        quality_states = collections.Counter()
        candidate_signals = collections.Counter()
        analysis_errors = collections.Counter()
        for row in rows:
            try:
                analyzed, sequence = rpc(process, "video.analyze", {
                    "mediaId": row["id"], "extractFrames": True,
                }, sequence)
                metadata = analyzed["metadata"]
                analysis_states[metadata.get("decodeStatus", "unknown")] += 1
                frame_sources[metadata.get("frameSource", "unknown")] += 1
                frames_analyzed[str(metadata.get("framesAnalyzed", 0))] += 1
                quality_states[analyzed.get("qualityState", "unknown")] += 1
                for signal in metadata.get("quality", {}).get("frameSignals", []):
                    candidate_signals.update(signal.get("candidates", []))
            except RuntimeError as error:
                analysis_errors[str(error)] += 1

        result.update({
            "probedVideoCount": len(rows),
            "probeResults": dict(probe_states),
            "durationMs": {
                "min": min(durations) if durations else None,
                "max": max(durations) if durations else None,
            },
            "resolutionCounts": dict(resolutions),
            "frameAnalysisResults": dict(analysis_states),
            "frameSources": dict(frame_sources),
            "framesAnalyzedPerVideo": dict(frames_analyzed),
            "qualityStates": dict(quality_states),
            "candidateSignals": dict(candidate_signals),
            "analysisErrors": dict(analysis_errors),
            "qualityAccuracy": "not_established_no_ground_truth_labels",
        })
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
