"""Smoke-test a packaged local Agent sidecar without any cloud credentials."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import queue
import subprocess
import tempfile
import threading
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def read_ready_line(stream, timeout: float = 45) -> str:
    """Pipes cannot be passed to select() on Windows; bound the blocking read."""
    lines = queue.Queue(maxsize=1)

    def read():
        try:
            lines.put(stream.readline())
        except (OSError, ValueError):
            lines.put("")

    threading.Thread(target=read, daemon=True).start()
    try:
        line = lines.get(timeout=timeout)
    except queue.Empty:
        raise RuntimeError("sidecar did not announce readiness within timeout") from None
    if not line:
        raise RuntimeError("sidecar exited before announcing readiness")
    return line

def verify_manifest(bundle: Path) -> dict[str, Any]:
    """Verify every packaged file against the generated manifest before launch."""
    root = bundle.resolve(strict=True)
    if not root.is_dir():
        raise RuntimeError("sidecar bundle path is not a directory")
    manifest_path = root / "sidecar-manifest.json"
    try:
        manifest_path.resolve(strict=True).relative_to(root)
    except (OSError, ValueError):
        raise RuntimeError("sidecar manifest is missing or escaping the bundle") from None

    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate manifest JSON key")
            result[key] = value
        return result

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"), object_pairs_hook=unique_object)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        raise RuntimeError("sidecar manifest is missing or invalid JSON") from None
    if not isinstance(manifest, dict) or manifest.get("name") != "yingxu-local-agent":
        raise RuntimeError("sidecar manifest identity is invalid")
    entrypoint = manifest.get("entrypoint")
    if not isinstance(entrypoint, str) or not entrypoint or "\\" in entrypoint:
        raise RuntimeError("sidecar manifest entrypoint is invalid")
    listed = manifest.get("files")
    if not isinstance(listed, list) or not listed:
        raise RuntimeError("sidecar manifest file list is invalid")

    expected: dict[str, tuple[int, str]] = {}
    for record in listed:
        if not isinstance(record, dict):
            raise RuntimeError("sidecar manifest file record is invalid")
        relative = record.get("path")
        size = record.get("size")
        digest = record.get("sha256")
        if (
            not isinstance(relative, str)
            or not relative
            or "\\" in relative
            or relative.startswith("/")
            or any(part in {"", ".", ".."} for part in relative.split("/"))
            or not isinstance(size, int)
            or isinstance(size, bool)
            or size < 0
            or not isinstance(digest, str)
            or re.fullmatch(r"[0-9a-f]{64}", digest) is None
            or relative == "sidecar-manifest.json"
            or relative in expected
        ):
            raise RuntimeError("sidecar manifest contains an unsafe or duplicate file record")
        expected[relative] = (size, digest)

    entry = Path(entrypoint)
    if entry.is_absolute() or ".." in entry.parts or "\\" in entrypoint or entrypoint not in expected:
        raise RuntimeError("sidecar manifest entrypoint is not a listed bundle file")

    actual_paths = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path != manifest_path
    }
    if actual_paths != set(expected):
        raise RuntimeError("sidecar bundle file set does not match its manifest")

    for relative, (size, expected_digest) in expected.items():
        path = root / relative
        try:
            resolved = path.resolve(strict=True)
            resolved.relative_to(root)
            if not path.is_file() or path.stat().st_size != size:
                raise RuntimeError("sidecar bundle file size does not match its manifest")
            digest = hashlib.sha256()
            with path.open("rb") as source:
                for block in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(block)
        except (OSError, ValueError):
            raise RuntimeError("sidecar bundle contains a missing or escaping file") from None
        if digest.hexdigest() != expected_digest:
            raise RuntimeError("sidecar bundle file hash does not match its manifest")
    return manifest


def _request(url: str, token: str, payload: dict | None = None) -> dict:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(url, data=data, method="GET" if data is None else "POST", headers={
        "Authorization": "Bearer " + token,
        "Content-Type": "application/json",
    })
    try:
        with urlopen(request, timeout=90) as response:
            return json.loads(response.read())
    except HTTPError as exc:
        raise RuntimeError(f"sidecar HTTP request failed with status {exc.code}") from None
    except URLError:
        raise RuntimeError("sidecar HTTP request failed") from None


def verify(bundle: Path, *, with_ollama: bool) -> dict:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    manifest = verify_manifest(bundle)
    executable = bundle.resolve() / manifest.get("entrypoint", "yingxu-local-agent")
    if not executable.is_file():
        raise SystemExit("packaged sidecar executable is missing")
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    signing_key = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode("ascii")
    token = secrets.token_urlsafe(48)
    with tempfile.TemporaryDirectory(prefix="yingxu-local-agent-verify-") as temp:
        env = {
            **os.environ,
            "APP_ENV": "production",
            "YINGXU_LOCAL_AGENT_MODE": "1",
            "YINGXU_LOCAL_AGENT_TOKEN": token,
            "YINGXU_LOCAL_AGENT_DATABASE_URL": "sqlite:///" + str(Path(temp) / "private.sqlite"),
            "YINGXU_LOCAL_AGENT_SIGNING_KEY": signing_key,
            "YINGXU_LOCAL_AGENT_KEY_ID": "packaged-verification-key",
            "YINGXU_LOCAL_AGENT_MODEL": "qwen2.5:7b",
            # Deliberate decoys: packaged desktop mode must ignore inherited cloud configuration.
            "CLOUDBASE_API_KEY": "ignored-cloud-secret-fixture",
            "MODEL_OWNER_CREDENTIALS": json.dumps({"local-desktop-owner": {"provider": "openai", "apiKey": "ignored-model-secret-fixture"}}),
        }
        process = subprocess.Popen([str(executable)], env=env, stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL, text=True, bufsize=1)
        shutdown_ok = False
        model_requests = 0
        try:
            ready = json.loads(read_ready_line(process.stdout))
            if ready.get("event") != "ready" or ready.get("host") != "127.0.0.1":
                raise RuntimeError("sidecar returned an invalid readiness message")
            base = f"http://127.0.0.1:{ready['port']}"
            health = _request(base + "/healthz", token)
            if health.get("ok") is not True or health.get("service") != "yingxu-local-agent":
                raise RuntimeError("packaged sidecar health check failed")
            model_status = _request(base + "/v1/agent/local-status", token)
            if model_status.get("provider") != "ollama" or model_status.get("cloudFallback") is not False:
                raise RuntimeError("packaged model status is not local-only")
            if with_ollama and model_status.get("configured") is not True:
                raise RuntimeError("configured local Ollama model is not available")
            result = {"health": "passed", "modelAvailability": model_status.get("availability"),
                      "cloudFallback": model_status.get("cloudFallback")}
            if with_ollama:
                turn = _request(base + "/v1/agent/local-turns", token, {
                    "query": "请先使用 search_media 工具，在本机照片索引里搜索文件名包含 IMG_2048 的照片。收到结果后用中文总结，不再调用工具，也不要打开文件。",
                    "sessionId": "packaged-verify-session",
                    "turnId": "packaged-verify-turn",
                    "useModel": True,
                    "capabilities": ["search_media", "get_metadata", "get_backup_job", "open_item"],
                    "destinationGrantIds": [],
                })
                model_requests = turn.get("modelRequests", 0)
                if turn.get("status") != "awaiting_local_tools":
                    raise RuntimeError("Ollama did not request a typed local tool")
                last_payload = None
                for _ in range(2):
                    typed_results = []
                    for call in turn.get("localToolRequests", []):
                        if call.get("tool") == "search_media":
                            typed_results.append({"callId": call["callId"], "tool": "search_media", "status": "succeeded",
                                "data": {"matches": [{"mediaId": "fixture-media-2048", "name": "IMG_2048.jpg",
                                    "kind": "photo", "bytes": 2048, "qualityState": "unanalyzed"}], "truncated": False}})
                        elif call.get("tool") == "open_item":
                            typed_results.append({"callId": call["callId"], "tool": "open_item", "status": "declined", "data": {}})
                        else:
                            typed_results.append({"callId": call["callId"], "tool": call["tool"], "status": "failed", "data": {}})
                    last_payload = {"continuationToken": turn["continuationToken"], "useModel": True, "results": typed_results}
                    turn = _request(base + "/v1/agent/local-turns/packaged-verify-session/packaged-verify-turn/results", token, last_payload)
                    model_requests = turn.get("modelRequests", model_requests)
                    if turn.get("status") == "completed":
                        break
                    if turn.get("status") != "awaiting_local_tools":
                        raise RuntimeError("Ollama continuation entered an unexpected state")
                if turn.get("status") != "completed" or model_requests > 3 or turn.get("executionAuthorized") is not False:
                    raise RuntimeError("Ollama local tool loop did not complete within the bounded contract")
                if "fixture-media-2048" not in json.dumps(turn.get("response", {}).get("matches", [])):
                    raise RuntimeError("Ollama response did not consume the typed local result")
                replay = _request(base + "/v1/agent/local-turns/packaged-verify-session/packaged-verify-turn/results", token, last_payload)
                if replay.get("duplicate") is not True or model_requests > 3:
                    raise RuntimeError("replayed continuation was not served from the local ledger")
                result.update({"ollama": "passed", "modelRequests": model_requests,
                               "toolResult": "synthetic fixture; no filesystem operation", "replay": "no additional model request"})
            stopped = _request(base + "/_host/shutdown", token, {})
            shutdown_ok = stopped.get("stopping") is True
            exit_code = process.wait(timeout=20)
            if not shutdown_ok or exit_code != 0:
                raise RuntimeError("sidecar did not shut down gracefully")
            result.update({"status": "passed", "platform": manifest.get("platform"),
                           "architecture": manifest.get("architecture"), "manifestFiles": len(manifest.get("files", [])),
                           "signedOrNotarized": manifest.get("signedOrNotarized", False),
                           "modelWeightsBundled": False, "inheritedCloudCredentialsIgnored": True,
                           "gracefulShutdown": "passed"})
            return result
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle-dir", type=Path, required=True)
    parser.add_argument("--with-ollama", action="store_true", help="use the configured local Ollama model")
    args = parser.parse_args()
    print(json.dumps(verify(args.bundle_dir, with_ollama=args.with_ollama), separators=(",", ":")))


if __name__ == "__main__":
    main()
