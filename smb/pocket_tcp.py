"""Opt-in, LAN-only newline-framed TCP bridge for the native Pocket app.

The bridge deliberately exposes only Pocket collaboration data. It is disabled
unless the desktop user enables it in Preferences.
"""
from __future__ import annotations

import json
import base64
import hashlib
import os
import tempfile
import secrets
from pathlib import Path
import re
import socketserver
import threading
import time
from collections.abc import Callable
from urllib.parse import quote, unquote


MAX_REQUEST_BYTES = 8 * 1024
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
PAIR_WINDOW_SECONDS = 10 * 60
MAX_PAIR_FAILURES = 5
MAX_TRANSFER_CHUNK_BYTES = 4096
MAX_DESKTOP_READ_CHUNK_BYTES = 1024 * 1024
MAX_TRANSFER_FILE_BYTES = 20 * 1024 * 1024 * 1024
MAX_DESKTOP_SOURCE_PAGE = 40


class DesktopMediaSource:
    """Expose only the most recent desktop media scan to a paired Pocket client."""

    def __init__(self, scan_provider: Callable[[], dict]):
        self.scan_provider = scan_provider

    @staticmethod
    def _contained_file(root_value: str, path_value: str) -> tuple[Path, os.stat_result]:
        root = Path(root_value).expanduser().resolve(strict=True)
        raw_path = Path(path_value).expanduser()
        if raw_path.is_symlink():
            raise ValueError("来源文件已变化或不可访问")
        path = raw_path.resolve(strict=True)
        try:
            path.relative_to(root)
        except ValueError:
            raise ValueError("来源文件不在当前扫描目录内") from None
        if not path.is_file():
            raise ValueError("来源文件已不存在或不可读取")
        return path, path.stat()

    def _manifest(self, session: dict) -> list[dict]:
        cached = session.get("desktop_source_manifest")
        if cached is not None:
            return cached
        snapshot = self.scan_provider() or {}
        root = str(snapshot.get("mount_point") or snapshot.get("source") or "")
        if not root or not isinstance(snapshot.get("files"), list):
            raise ValueError("请先在电脑端扫描素材来源")
        root_path = Path(root).expanduser().resolve()
        items = []
        for item in snapshot["files"]:
            if not isinstance(item, dict) or item.get("media_type") not in {"photo", "raw", "video"}:
                continue
            try:
                path, stat = self._contained_file(root, str(item.get("path") or ""))
            except (OSError, ValueError):
                continue
            source_id = secrets.token_urlsafe(18)
            name = re.sub(r"[\x00-\x1f\x7f]", "_", str(item.get("filename") or path.name)
                          .replace("\\", "/").split("/")[-1])[:240]
            items.append({
                "source_id": source_id,
                "name": name,
                "size": stat.st_size,
                "modified_at": int(stat.st_mtime * 1000),
                "captured_at": str(item.get("date") or "")[:32],
                "camera": str(item.get("camera") or "")[:100],
                "media_type": "video" if item.get("media_type") == "video" else "photo",
                "path": str(path),
                "root": str(root_path),
                "mtime_ns": stat.st_mtime_ns,
            })
        session["desktop_source_manifest"] = items
        return items

    def list_page(self, session: dict, offset: int, limit: int) -> dict:
        if offset < 0 or offset > 1_000_000:
            raise ValueError("素材列表位置无效")
        page_size = max(1, min(int(limit), MAX_DESKTOP_SOURCE_PAGE))
        manifest = self._manifest(session)
        page = manifest[offset:offset + page_size]
        public = [{key: item[key] for key in (
            "source_id", "name", "size", "modified_at", "captured_at", "camera", "media_type"
        )} for item in page]
        next_offset = offset + len(page)
        return {"items": public, "next_offset": next_offset if next_offset < len(manifest) else None,
                "total": len(manifest)}

    def begin(self, session: dict, source_id: str) -> dict:
        item = self._source_item(session, source_id)
        path, stat = self._current_source(item)
        hasher = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                hasher.update(block)
        current = path.stat()
        if current.st_size != stat.st_size or current.st_mtime_ns != stat.st_mtime_ns:
            raise ValueError("电脑来源文件在校验期间发生变化")
        session["desktop_source_active"] = {
            "source_id": source_id, "size": stat.st_size, "mtime_ns": stat.st_mtime_ns,
            "sha256": hasher.hexdigest(),
        }
        return {"status": "ready", "size": stat.st_size, "sha256": hasher.hexdigest()}

    def read_chunk(self, session: dict, source_id: str, offset: int, length: int) -> dict:
        item = self._source_item(session, source_id)
        active = session.get("desktop_source_active") or {}
        if active.get("source_id") != source_id:
            raise ValueError("请先开始该电脑文件的传输")
        if offset < 0 or offset > active["size"] or length < 1 or length > MAX_DESKTOP_READ_CHUNK_BYTES:
            raise ValueError("电脑文件分块位置或大小无效")
        path, stat = self._current_source(item)
        if stat.st_size != active["size"] or stat.st_mtime_ns != active["mtime_ns"]:
            raise ValueError("电脑来源文件在传输期间发生变化")
        with path.open("rb") as stream:
            stream.seek(offset)
            data = stream.read(min(length, active["size"] - offset))
        return {"data": base64.b64encode(data).decode("ascii"), "bytes_read": len(data)}

    def finish(self, session: dict, source_id: str) -> dict:
        active = session.get("desktop_source_active") or {}
        if active.get("source_id") != source_id:
            raise ValueError("电脑文件传输状态无效")
        item = self._source_item(session, source_id)
        path, stat = self._current_source(item)
        if stat.st_size != active["size"] or stat.st_mtime_ns != active["mtime_ns"]:
            raise ValueError("电脑来源文件在传输期间发生变化")
        session.pop("desktop_source_active", None)
        return {"sha256": active["sha256"], "size": active["size"]}

    @staticmethod
    def _source_item(session: dict, source_id: str) -> dict:
        if not isinstance(source_id, str) or not source_id:
            raise ValueError("电脑素材编号无效")
        for item in session.get("desktop_source_manifest", []):
            if secrets.compare_digest(item["source_id"], source_id):
                return item
        raise ValueError("电脑素材列表已失效，请刷新后重试")

    def _current_source(self, item: dict) -> tuple[Path, os.stat_result]:
        path, stat = self._contained_file(item["root"], item["path"])
        if stat.st_size != item["size"] or stat.st_mtime_ns != item["mtime_ns"]:
            raise ValueError("电脑来源文件已变化，请重新扫描")
        return path, stat


class MobileInbox:
    """Accept verified phone uploads under a desktop-selected backup root."""

    def __init__(self, root_provider: Callable[[], str | Path | None]):
        self.root_provider = root_provider
        self._finalize_lock = threading.Lock()

    @staticmethod
    def _safe_component(value: str) -> str:
        cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(value)).strip(" .")[:120]
        return cleaned or "未命名"

    def begin(self, session: dict, relative_path: str, size: int) -> dict:
        if session.get("mobile_upload"):
            self.abort(session)
        if size < 0 or size > MAX_TRANSFER_FILE_BYTES:
            raise ValueError("文件大小超出手机到电脑传输限制")
        relative = str(relative_path or "")
        parts = relative.replace("\\", "/").split("/")
        if not relative or len(relative) > 1200 or len(parts) > 12 or any(part in ("", ".", "..") for part in parts):
            raise ValueError("手机上传路径无效")
        root_value = self.root_provider()
        if not root_value:
            raise ValueError("请先在电脑端设置备份目标位置")
        root = Path(root_value).expanduser().resolve()
        if not root.is_dir():
            raise ValueError("电脑备份目标盘当前不可用")
        inbox = root / "影序手机备份"
        target = inbox.joinpath(*(self._safe_component(part) for part in parts))
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            target.parent.resolve().relative_to(inbox.resolve())
        except (OSError, ValueError):
            raise ValueError("手机上传目标目录无效") from None
        descriptor, temporary = tempfile.mkstemp(prefix=".yingxu-pocket-", suffix=".part", dir=target.parent)
        session["mobile_upload"] = {
            "file": os.fdopen(descriptor, "wb"), "temporary": Path(temporary),
            "target": target, "inbox": inbox, "size": size, "received": 0,
        }
        return {"status": "receiving", "size": size}

    def append(self, session: dict, offset: int, encoded: str,
               max_chunk_bytes: int = MAX_TRANSFER_CHUNK_BYTES) -> dict:
        transfer = session.get("mobile_upload")
        if not transfer:
            raise ValueError("没有已开始的手机文件传输")
        data = base64.b64decode(str(encoded), validate=True)
        if len(data) > max_chunk_bytes or offset != transfer["received"]:
            raise ValueError("手机文件分块格式或偏移无效")
        if transfer["received"] + len(data) > transfer["size"]:
            raise ValueError("接收的数据超过声明文件大小")
        transfer["file"].write(data)
        transfer["received"] += len(data)
        return {"status": "receiving", "received": transfer["received"]}

    def finish(self, session: dict, expected_hash: str) -> dict:
        transfer = session.get("mobile_upload")
        if not transfer:
            raise ValueError("没有已开始的手机文件传输")
        session.pop("mobile_upload", None)
        temporary: Path = transfer["temporary"]
        target: Path = transfer["target"]
        try:
            transfer["file"].flush()
            os.fsync(transfer["file"].fileno())
            transfer["file"].close()
            if transfer["received"] != transfer["size"]:
                raise ValueError("文件尚未完整接收")
            hasher = hashlib.sha256()
            with temporary.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    hasher.update(block)
            digest = hasher.hexdigest()
            if not re.fullmatch(r"[a-fA-F0-9]{64}", str(expected_hash or "")) or digest != expected_hash.lower():
                raise ValueError("SHA-256 校验失败；桌面端未保留不完整文件")
            with self._finalize_lock:
                if target.exists():
                    existing = hashlib.sha256()
                    with target.open("rb") as stream:
                        for block in iter(lambda: stream.read(1024 * 1024), b""):
                            existing.update(block)
                    if existing.hexdigest() == digest:
                        temporary.unlink(missing_ok=True)
                        return {"status": "skipped", "sha256": digest, "size": transfer["size"],
                                "relative_path": target.relative_to(transfer["inbox"]).as_posix()}
                    stem, suffix = target.stem, target.suffix
                    index = 2
                    while True:
                        candidate = target.with_name(f"{stem} ({index}){suffix}")
                        if not candidate.exists():
                            target = candidate
                            break
                        index += 1
                temporary.replace(target)
            return {"status": "copied", "sha256": digest, "size": transfer["size"],
                    "relative_path": target.relative_to(transfer["inbox"]).as_posix()}
        except Exception:
            try:
                transfer["file"].close()
            except OSError:
                pass
            temporary.unlink(missing_ok=True)
            raise

    def abort(self, session: dict) -> None:
        transfer = session.pop("mobile_upload", None)
        if not transfer:
            return
        try:
            transfer["file"].close()
        finally:
            transfer["temporary"].unlink(missing_ok=True)


class PocketTcpBridge:
    """Serve the allowlisted Pocket API over a small text-framed TCP protocol."""

    def __init__(
        self,
        pairing,
        status_provider: Callable[[], dict],
        history_provider: Callable[[int], list],
        notes_store,
        host: str = "0.0.0.0",
        port: int = 18080,
        mobile_inbox: MobileInbox | None = None,
        desktop_media_source: DesktopMediaSource | None = None,
    ):
        self.pairing = pairing
        self.status_provider = status_provider
        self.history_provider = history_provider
        self.notes_store = notes_store
        self.host = host
        self.port = int(port)
        self.mobile_inbox = mobile_inbox
        self.desktop_media_source = desktop_media_source
        self._server = None
        self._thread = None
        self._lock = threading.Lock()
        self._pair_failures: dict[str, list[float]] = {}

    @property
    def running(self) -> bool:
        return self._server is not None

    @property
    def bound_port(self) -> int:
        if self._server is None:
            return self.port
        return int(self._server.server_address[1])

    def start(self) -> int:
        with self._lock:
            if self._server is not None:
                return self.bound_port
            bridge = self

            class Handler(socketserver.StreamRequestHandler):
                def handle(self):
                    connection_tokens: set[str] = set()
                    session: dict = {}
                    try:
                        self.connection.settimeout(60)
                        while True:
                            raw = self.rfile.readline(MAX_REQUEST_BYTES + 1)
                            if not raw:
                                return
                            if len(raw) > MAX_REQUEST_BYTES or not raw.endswith(b"\n"):
                                self._write_response({"ok": False, "error": "请求过长或格式错误"})
                                return
                            try:
                                encoded = raw.decode("ascii").strip()
                                request = json.loads(unquote(encoded))
                                response = bridge.handle_message(
                                    request,
                                    str(self.client_address[0]),
                                    connection_tokens=connection_tokens,
                                    session=session,
                                )
                            except (UnicodeDecodeError, ValueError, TypeError):
                                response = {"ok": False, "error": "请求格式错误"}
                            if not self._write_response(response):
                                return
                    finally:
                        if bridge.mobile_inbox:
                            bridge.mobile_inbox.abort(session)
                        for token in connection_tokens:
                            bridge.pairing.revoke(token)
                        connection_tokens.clear()

                def _write_response(self, response: dict) -> bool:
                    encoded = quote(
                        json.dumps(response, ensure_ascii=False, separators=(",", ":")),
                        safe="",
                    ).encode("ascii") + b"\n"
                    if len(encoded) > MAX_RESPONSE_BYTES:
                        encoded = quote(
                            json.dumps({"ok": False, "error": "响应过大"}), safe=""
                        ).encode("ascii") + b"\n"
                    try:
                        self.wfile.write(encoded)
                        self.wfile.flush()
                        return True
                    except OSError:
                        return False

            class Server(socketserver.ThreadingTCPServer):
                allow_reuse_address = True
                daemon_threads = True

                def __init__(self, *args, **kwargs):
                    self._connection_slots = threading.BoundedSemaphore(8)
                    super().__init__(*args, **kwargs)

                def process_request(self, request, client_address):
                    if not self._connection_slots.acquire(blocking=False):
                        request.close()
                        return
                    try:
                        super().process_request(request, client_address)
                    except Exception:
                        self._connection_slots.release()
                        raise

                def process_request_thread(self, request, client_address):
                    try:
                        super().process_request_thread(request, client_address)
                    finally:
                        self._connection_slots.release()

            server = Server(
                (self.host, self.port), Handler, bind_and_activate=True
            )
            self._server = server
            self._thread = threading.Thread(
                target=server.serve_forever,
                kwargs={"poll_interval": 0.25},
                name="yingxu-pocket-tcp",
                daemon=True,
            )
            self._thread.start()
            return self.bound_port

    def stop(self) -> None:
        with self._lock:
            server = self._server
            thread = self._thread
            self._server = None
            self._thread = None
        if server is not None:
            server.shutdown()
            server.server_close()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2)

    def _recent_failures(self, peer: str, now: float) -> list[float]:
        recent = [
            timestamp for timestamp in self._pair_failures.get(peer, [])
            if now - timestamp < PAIR_WINDOW_SECONDS
        ]
        self._pair_failures[peer] = recent
        return recent

    def _pair(self, peer: str, code: str) -> dict:
        now = time.time()
        with self._lock:
            failures = self._recent_failures(peer, now)
            if len(failures) >= MAX_PAIR_FAILURES:
                return {"ok": False, "error": "尝试次数过多，请稍后重试"}
        token = self.pairing.connect(code)
        if not token:
            with self._lock:
                self._pair_failures.setdefault(peer, []).append(now)
            return {"ok": False, "error": "配对码无效或已过期"}
        with self._lock:
            self._pair_failures.pop(peer, None)
        return {"ok": True, "token": token}

    def handle_message(
        self,
        request: dict,
        peer: str = "unknown",
        connection_tokens: set[str] | None = None,
        session: dict | None = None,
    ) -> dict:
        if not isinstance(request, dict):
            return {"ok": False, "error": "请求格式错误"}
        request_id = request.get("id")
        if not isinstance(request_id, (str, int)) or len(str(request_id)) > 64:
            request_id = None
        command = str(request.get("command", ""))
        if command == "pair":
            code = str(request.get("code", "")).strip()
            result = self._pair(peer, code)
            if connection_tokens is not None and result.get("ok"):
                connection_tokens.add(result["token"])
        elif command in {"status", "history", "notes.get", "notes.save", "disconnect", "file.begin", "file.chunk", "file.finish", "file.abort", "desktop.files.list", "desktop.file.begin", "desktop.file.chunk", "desktop.file.finish", "desktop.import.progress"}:
            token = str(request.get("token", ""))
            if (
                not self.pairing.is_authorized(token)
                or (connection_tokens is not None and token not in connection_tokens)
            ):
                result = {"ok": False, "error": "未配对或会话已过期"}
            elif command == "status":
                result = {"ok": True, "data": self.status_provider()}
            elif command == "history":
                try:
                    limit = max(1, min(int(request.get("limit", 20)), 50))
                except (TypeError, ValueError):
                    limit = 20
                result = {"ok": True, "items": self.history_provider(limit)}
            elif command == "notes.get":
                result = {"ok": True, "notes": self.notes_store.load()}
            elif command == "notes.save":
                data = request.get("data")
                if not isinstance(data, dict):
                    result = {"ok": False, "error": "拍摄信息格式错误"}
                else:
                    result = {"ok": True, "notes": self.notes_store.save(data)}
            elif command == "file.begin":
                try:
                    if not self.mobile_inbox or session is None:
                        raise ValueError("电脑端未开放手机到电脑备份")
                    result = {"ok": True, **self.mobile_inbox.begin(session, request.get("relativePath", ""), int(request.get("size", -1)))}
                except (OSError, TypeError, ValueError) as error:
                    result = {"ok": False, "error": str(error)}
            elif command == "file.chunk":
                try:
                    if not self.mobile_inbox or session is None:
                        raise ValueError("电脑端未开放手机到电脑备份")
                    result = {"ok": True, **self.mobile_inbox.append(session, int(request.get("offset", -1)), request.get("data", ""))}
                except (OSError, TypeError, ValueError) as error:
                    result = {"ok": False, "error": str(error)}
            elif command == "file.finish":
                try:
                    if not self.mobile_inbox or session is None:
                        raise ValueError("电脑端未开放手机到电脑备份")
                    result = {"ok": True, **self.mobile_inbox.finish(session, request.get("sha256", ""))}
                except (OSError, TypeError, ValueError) as error:
                    result = {"ok": False, "error": str(error)}
            elif command == "file.abort":
                if self.mobile_inbox and session is not None:
                    self.mobile_inbox.abort(session)
                result = {"ok": True, "status": "aborted"}
            elif command == "desktop.files.list":
                try:
                    if not self.desktop_media_source or session is None:
                        raise ValueError("电脑端素材导入当前不可用")
                    offset = int(request.get("offset", 0))
                    limit = int(request.get("limit", MAX_DESKTOP_SOURCE_PAGE))
                    result = {"ok": True, **self.desktop_media_source.list_page(session, offset, limit)}
                except (OSError, TypeError, ValueError) as error:
                    result = {"ok": False, "error": str(error)}
            elif command == "desktop.file.begin":
                try:
                    if not self.desktop_media_source or session is None:
                        raise ValueError("电脑端素材导入当前不可用")
                    result = {"ok": True, **self.desktop_media_source.begin(session, str(request.get("sourceId", "")))}
                except (OSError, TypeError, ValueError) as error:
                    result = {"ok": False, "error": str(error)}
            elif command == "desktop.file.chunk":
                try:
                    if not self.desktop_media_source or session is None:
                        raise ValueError("电脑端素材导入当前不可用")
                    result = {"ok": True, **self.desktop_media_source.read_chunk(
                        session, str(request.get("sourceId", "")),
                        int(request.get("offset", -1)), int(request.get("length", MAX_TRANSFER_CHUNK_BYTES))
                    )}
                except (OSError, TypeError, ValueError) as error:
                    result = {"ok": False, "error": str(error)}
            elif command == "desktop.file.finish":
                try:
                    if not self.desktop_media_source or session is None:
                        raise ValueError("电脑端素材导入当前不可用")
                    result = {"ok": True, **self.desktop_media_source.finish(session, str(request.get("sourceId", "")))}
                except (OSError, TypeError, ValueError) as error:
                    result = {"ok": False, "error": str(error)}
            elif command == "desktop.import.progress":
                data = request.get("data")
                if not isinstance(data, dict):
                    result = {"ok": False, "error": "电脑导入进度格式错误"}
                else:
                    result = {"ok": True, "progress": self._save_mobile_transfer_progress(data)}
            else:
                self.pairing.revoke(token)
                if connection_tokens is not None:
                    connection_tokens.discard(token)
                result = {"ok": True, "disconnected": True}
        else:
            result = {"ok": False, "error": "不支持的操作"}
        result["id"] = request_id
        return result

    def _save_mobile_transfer_progress(self, data: dict) -> dict:
        allowed_statuses = {"copying", "paused", "completed", "partial", "cancelled", "queued", "failed"}
        status = str(data.get("status", ""))
        if status not in allowed_statuses:
            raise ValueError("电脑导入进度状态无效")
        current_file = re.sub(r"[\x00-\x1f\x7f]", "_", str(data.get("currentFile") or "")
                              .replace("\\", "/").split("/")[-1])[:160]
        safe = {"status": status, "current_file": current_file, "updated_at": time.time()}
        for source, target in (("totalFiles", "total_files"), ("processedFiles", "processed_files"),
                               ("copiedFiles", "copied_files"), ("skippedFiles", "skipped_files"),
                               ("verifiedFiles", "verified_files"), ("failedFiles", "failed_files"),
                               ("totalBytes", "total_bytes"), ("bytesCopied", "bytes_copied")):
            try:
                safe[target] = max(0, min(int(data.get(source, 0)), 2**63 - 1))
            except (TypeError, ValueError, OverflowError):
                safe[target] = 0
        progress = getattr(self, "_mobile_transfer_progress", {})
        progress = {**progress, **safe}
        self._mobile_transfer_progress = progress
        return dict(progress)

    @property
    def mobile_transfer_progress(self) -> dict:
        return dict(getattr(self, "_mobile_transfer_progress", {"status": "idle"}))
