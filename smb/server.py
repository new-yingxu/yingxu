"""Flask + SocketIO Web 服务端 — 仪表盘 + 备份控制"""
import os
import shutil
import sys
import json
import threading
import webbrowser
import subprocess
import ipaddress
import socket
import time
from datetime import datetime
from functools import wraps
from pathlib import Path

import flask
import psutil
from flask import Flask, render_template, request, jsonify, send_file
from flask_socketio import SocketIO, emit

# PyInstaller 打包后资源路径修正
if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
    _base = sys._MEIPASS
else:
    _base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

app = Flask(__name__,
            template_folder=os.path.join(_base, "smb", "templates"),
            static_folder=os.path.join(_base, "smb", "static"))
app.config["SECRET_KEY"] = os.urandom(16).hex()

from .config import config
from .detector import (list_removable_volumes, list_all_volumes, list_candidate_source_volumes,
                       find_likely_media_source, SDCardWatcher)
from .backup import BackupEngine
from . import db
from .pocket import PocketPairingManager, PocketNotesStore
from .pocket_tcp import (MAX_TRANSFER_FILE_BYTES, DesktopMediaSource, MobileInbox,
                         PocketTcpBridge)
from .transfer import transfer_manager
from .firebase_sync import FirebaseSyncClient, FirebaseSyncError, KeyringTokenStore

# 初始化数据库
db.init_db()

socketio = SocketIO(app, cors_allowed_origins="*")

engine = BackupEngine()
_scan_cache = {"files": [], "volumes": [], "mount_point": ""}
_scan_response_cache = {}
_scan_lock = threading.Lock()
_open_browser_on_start = True
pocket_pairing = PocketPairingManager(session_hours=config.pocket_session_hours)
pocket_notes = PocketNotesStore()
POCKET_TCP_PORT = 18080
_pocket_tcp_bridge = None
_pocket_tcp_bridge_lock = threading.Lock()
_pocket_mobile_inbox = MobileInbox(lambda: _pocket_mobile_inbox_root())
_pocket_upload_sessions = {}
_pocket_upload_sessions_lock = threading.RLock()
MAX_HTTP_POCKET_CHUNK_BYTES = 512 * 1024
_pocket_http_transfer_progress = {"status": "idle"}
_pocket_http_transfer_progress_lock = threading.Lock()
_firebase_sync_client = None
_firebase_sync_lock = threading.RLock()


def _get_firebase_sync_client():
    global _firebase_sync_client
    with _firebase_sync_lock:
        if _firebase_sync_client is None:
            _firebase_sync_client = FirebaseSyncClient(
                config.firebase_api_key,
                config.firebase_database_url,
                KeyringTokenStore(),
                config.firebase_device_id,
            )
        return _firebase_sync_client


def _restore_firebase_session():
    try:
        client = _get_firebase_sync_client()
        restored = client.restore_session(
            client.uid or _firebase_uid_for_email(config.firebase_email),
            config.firebase_email,
            config.firebase_display_name,
        )
        if restored:
            try:
                client.flush_outbox()
            except Exception as error:
                print(f"[Firebase] 待同步记录仍未上传：{error}", file=sys.stderr)
        return restored
    except Exception:
        return False


def _firebase_uid_for_email(email: str) -> str:
    """Resolve stored credential identity without exposing it to app config."""
    if not email:
        return ""
    return str(config.firebase_uid or "")


def _sync_latest_desktop_history():
    try:
        client = _get_firebase_sync_client()
        if client.uid:
            client.flush_outbox()
        for record in db.get_backups(limit=100):
            backup_id = record.get("id")
            if backup_id is None:
                continue
            files = db.get_backup_files(backup_id, 5000)
            public_record = dict(record)
            public_record["target_name"] = os.path.basename(str(record.get("backup_root") or "").rstrip(os.sep))
            client.publish_history(public_record, files)
    except Exception as error:
        print(f"[Firebase] 桌面历史同步失败：{error}", file=sys.stderr)


def _publish_desktop_progress(data: dict):
    try:
        client = _get_firebase_sync_client()
        if not client.uid:
            client.restore_session(
                _firebase_uid_for_email(config.firebase_email),
                config.firebase_email,
                config.firebase_display_name,
            )
        if not client.uid:
            return
        record = {
            "id": f"active-{config.firebase_device_id}",
            "status": data.get("status", "unknown"),
            "current_file": data.get("current_file", ""),
            "total_files": data.get("total_files", 0),
            "total_size": data.get("total_bytes", 0),
            "processed_files": data.get("processed_files", 0),
            "copied_files": data.get("copied_files", 0),
            "skipped_files": data.get("skipped_files", 0),
            "verified_files": data.get("verified_files", 0),
            "failed_files": data.get("failed_files", 0),
            "started_at": datetime.fromtimestamp(engine.progress.start_time).astimezone().isoformat()
            if engine.progress.start_time else None,
            "updatedAt": int(time.time() * 1000),
        }
        client.publish_progress(record, force=data.get("status") in {"done", "partial", "cancelled", "error"})
    except Exception as error:
        print(f"[Firebase] 桌面进度同步失败：{error}", file=sys.stderr)


def _pocket_tcp_status() -> dict:
    raw = engine.progress.to_dict()
    allowed = (
        "total_files", "copied_files", "skipped_files", "processed_files",
        "verified_files", "failed_files", "current_speed", "bytes_copied",
        "total_bytes", "elapsed_seconds", "status", "phase_progress",
        "current_device", "current_media_type",
    )
    progress = {key: raw.get(key) for key in allowed}
    return {
        "paired": True,
        "status": raw.get("status", "idle"),
        "progress": progress,
        "source": os.path.basename(raw.get("mount_point") or ""),
    }


def _pocket_tcp_history(limit: int) -> list[dict]:
    records = db.get_backups(limit=limit)
    clean = []
    for record in records:
        root = str(record.get("backup_root") or "")
        clean.append({
            "id": record.get("id"),
            "event_name": record.get("event_name") or "未命名任务",
            "started_at": record.get("started_at"),
            "status": record.get("status"),
            "total_files": record.get("total_files", 0),
            "skipped_files": record.get("skipped_files", 0),
            "failed_files": record.get("failed_files", 0),
            "target": os.path.basename(root.rstrip(os.sep)) or "本机",
        })
    return clean


def _pocket_mobile_inbox_root() -> str | None:
    return config.last_backup_root or next(iter(config.backup_targets or []), None)


def _configure_pocket_tcp(enabled: bool) -> None:
    """Start or stop the opt-in native Pocket bridge."""
    global _pocket_tcp_bridge
    if enabled and not config.pocket_enabled:
        raise RuntimeError("请先启用影序 Pocket")
    with _pocket_tcp_bridge_lock:
        if enabled:
            if _pocket_tcp_bridge is None:
                bridge = PocketTcpBridge(
                    pairing=pocket_pairing,
                    status_provider=_pocket_tcp_status,
                    history_provider=_pocket_tcp_history,
                    notes_store=pocket_notes,
                    host="0.0.0.0",
                    port=POCKET_TCP_PORT,
                    mobile_inbox=_pocket_mobile_inbox,
                    desktop_media_source=DesktopMediaSource(lambda: _scan_cache),
                )
                bridge.start()
                _pocket_tcp_bridge = bridge
        elif _pocket_tcp_bridge is not None:
            bridge = _pocket_tcp_bridge
            _pocket_tcp_bridge = None
            bridge.stop()


def _is_loopback_request() -> bool:
    try:
        return ipaddress.ip_address(request.remote_addr or "").is_loopback
    except ValueError:
        return False


def _is_existing_mount(path: str) -> bool:
    """判断一个路径是否是当前已挂载、可直接遍历的卷。

    Windows 的盘符根路径（如 ``D:\\``）与 POSIX 挂载点一样，``os.path.ismount``
    都能正确返回 True；个别读卡器若返回 False，用“目录存在”兜底，避免漏掉
    真实来源卷。统一封装便于测试与后续扩展。
    """
    if not path:
        return False
    try:
        if os.path.ismount(path):
            return True
    except OSError:
        pass
    # 兜底：某些挂载点/盘符在部分系统上 ismount 会误判，目录存在即可遍历。
    try:
        return os.path.isdir(path)
    except OSError:
        return False


def _pocket_token() -> str | None:
    return request.cookies.get("yingxu_pocket") or request.headers.get("X-Yingxu-Pocket")


def _pocket_authorized() -> bool:
    return pocket_pairing.is_authorized(_pocket_token())


def _local_ip() -> str:
    """Prefer an active Wi-Fi/Ethernet address over VPN and virtual adapters."""
    tunnel_prefixes = (
        "utun", "tun", "tap", "ppp", "wg", "tailscale", "zerotier", "zt",
        "vpn", "docker", "veth", "vmnet", "vboxnet",
    )
    benchmark_network = ipaddress.ip_network("198.18.0.0/15")
    candidates: list[tuple[int, str, str]] = []
    try:
        addresses_by_interface = psutil.net_if_addrs()
        stats_by_interface = psutil.net_if_stats()
    except (OSError, RuntimeError):
        addresses_by_interface = {}
        stats_by_interface = {}

    for interface, addresses in addresses_by_interface.items():
        normalized = interface.casefold().replace(" ", "")
        if normalized.startswith(tunnel_prefixes):
            continue
        stats = stats_by_interface.get(interface)
        if stats is not None and not stats.isup:
            continue
        if normalized.startswith(("wifi", "wlan", "wireless")):
            priority = 0
        elif normalized.startswith(("en", "eth", "ethernet")):
            priority = 1
        elif normalized.startswith(("bridge", "ap")):
            priority = 2
        else:
            priority = 3
        for address in addresses:
            if address.family != socket.AF_INET:
                continue
            try:
                parsed = ipaddress.ip_address(address.address)
            except ValueError:
                continue
            if (
                not parsed.is_private
                or parsed.is_loopback
                or parsed.is_link_local
                or parsed.is_unspecified
                or parsed.is_multicast
                or parsed in benchmark_network
            ):
                continue
            candidates.append((priority, normalized, str(parsed)))

    if candidates:
        return min(candidates)[2]

    # Keep a route-based fallback for hosts whose adapter details are unavailable.
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("192.0.2.1", 9))
        return probe.getsockname()[0]
    except OSError:
        try:
            return socket.gethostbyname(socket.gethostname())
        except OSError:
            return "127.0.0.1"
    finally:
        probe.close()


def pocket_login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        # 桌面工作台运行在 loopback，属于受信任的本机界面；手机仍必须配对。
        if _is_loopback_request():
            return view(*args, **kwargs)
        if not _pocket_authorized():
            return jsonify({"error": "请先与桌面端配对", "paired": False}), 401
        return view(*args, **kwargs)
    return wrapped


@app.before_request
def restrict_lan_surface():
    """非本机只能访问 Pocket 壳和配对后的 Pocket API。"""
    if _is_loopback_request():
        return None
    allowed_public = {
        "/pocket", "/pocket/", "/pocket/manifest.webmanifest",
        "/pocket/sw.js", "/static/img/icon.svg",
        "/api/pocket/connect",
    }
    if request.path == "/api/pocket/tcp-settings":
        return jsonify({"error": "Pocket TCP 设置只能从桌面端修改"}), 403
    if not config.pocket_enabled:
        return jsonify({"error": "影序 Pocket 未启用"}), 403
    if request.path in allowed_public or request.path.startswith("/api/pocket/"):
        return None
    return jsonify({"error": "桌面工作台仅允许在本机访问"}), 403


# ====== SocketIO 实时推送 ======

def broadcast_progress(data):
    """备份进度广播到所有连接的前端"""
    try:
        socketio.emit("backup_progress", data)
    except Exception:
        pass
    if config.firebase_api_key and config.firebase_database_url and config.firebase_uid:
        threading.Thread(target=_publish_desktop_progress, args=(dict(data),), daemon=True,
                         name="yingxu-firebase-progress").start()
        if data.get("status") in {"done", "partial", "cancelled", "error"}:
            threading.Timer(1.0, _sync_latest_desktop_history).start()


# 注册进度回调
engine.progress.on_update(broadcast_progress)


# ====== HTTP 路由 ======

@app.route("/")
def dashboard():
    """主仪表盘页面"""
    return render_template("dashboard.html")


@app.route("/api/health")
def api_health():
    """供桌面壳、发布验收和故障排查使用的轻量健康检查。"""
    from . import __version__
    return jsonify({
        "status": "ok",
        "version": __version__,
        "database": "ok",
        "active_backup": engine.progress.status not in ("idle", "done", "error", "cancelled", "partial"),
    })


@app.route("/history")
def history():
    """历史记录页面"""
    return render_template("history.html")


@app.route("/settings")
def settings():
    """设置页面"""
    return render_template("settings.html")


@app.route("/transfer")
def transfer_page():
    return render_template("transfer.html")


@app.route("/api/transfer/choose_folder", methods=["POST"])
def api_transfer_choose_folder():
    data = request.get_json(silent=True) or {}
    purpose = "转移来源" if data.get("purpose") == "source" else "转移目标"
    if sys.platform == "darwin":
        try:
            script = f'POSIX path of (choose folder with prompt "选择影序{purpose}文件夹")'
            result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=120)
            if result.returncode != 0:
                return jsonify({"status": "cancelled"})
            folder = result.stdout.strip().rstrip("/")
        except Exception as exc:
            return jsonify({"error": f"打开文件夹选择器失败: {exc}"}), 500
    elif sys.platform.startswith("win"):
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        try:
            folder = filedialog.askdirectory(title=f"选择影序{purpose}文件夹")
        finally:
            root.destroy()
        if not folder:
            return jsonify({"status": "cancelled"})
    else:
        return jsonify({"error": "当前平台暂不支持原生文件夹选择器"}), 501
    if not folder or not os.path.isdir(folder):
        return jsonify({"error": "未选择有效文件夹"}), 400
    return jsonify({"status": "ok", "path": os.path.realpath(folder), "name": os.path.basename(folder) or folder})


@app.route("/api/transfer/scan", methods=["POST"])
def api_transfer_scan():
    data = request.get_json(silent=True) or {}
    try:
        transfer_manager.start_scan(str(data.get("source", "")))
        return jsonify({"status": "scanning"})
    except (ValueError, RuntimeError) as exc:
        return jsonify({"error": str(exc)}), 400


@app.route("/api/transfer/scan/status")
def api_transfer_scan_status():
    return jsonify(transfer_manager.snapshot_scan())


@app.route("/api/transfer/analyze", methods=["POST"])
def api_transfer_analyze():
    data = request.get_json(silent=True) or {}
    try:
        items = transfer_manager.analyze(data.get("ids", []), str(data.get("target", "")),
                                         bool(data.get("check_waste")), bool(data.get("check_similar")))
        return jsonify({"items": items})
    except (ValueError, RuntimeError) as exc:
        return jsonify({"error": str(exc)}), 400


@app.route("/api/transfer/suggest_name", methods=["POST"])
def api_transfer_suggest_name():
    data = request.get_json(silent=True) or {}
    ids = set(data.get("ids", []))
    with transfer_manager.lock:
        images = [x["path"] for x in transfer_manager.scan_state["items"]
                  if x["id"] in ids and x.get("media_type") == "photo"][:3]
    if not images:
        return jsonify({"error": "所选内容中没有可用于命名建议的照片"}), 400
    from .ai_namer import ai_namer
    suggestion = ai_namer.suggest_event_name(images)
    if not suggestion:
        return jsonify({"error": "AI 命名未启用或暂时无法生成建议，请在偏好设置中配置后重试"}), 400
    return jsonify({"name": suggestion})


@app.route("/api/transfer/start", methods=["POST"])
def api_transfer_start():
    data = request.get_json(silent=True) or {}
    try:
        transfer_manager.start(data.get("ids", []), str(data.get("target", "")),
                               str(data.get("event", "")), move=True)
        return jsonify({"status": "running"})
    except (ValueError, RuntimeError) as exc:
        return jsonify({"error": str(exc)}), 400


@app.route("/api/transfer/status")
def api_transfer_status():
    return jsonify(transfer_manager.snapshot_job())


@app.route("/api/transfer/cancel", methods=["POST"])
def api_transfer_cancel():
    transfer_manager.cancel()
    return jsonify({"status": "cancelling"})


@app.route("/pocket")
@app.route("/pocket/")
def pocket_page():
    return render_template("pocket.html")


@app.route("/pocket/manifest.webmanifest")
def pocket_manifest():
    return flask.send_from_directory(app.static_folder, "pocket/manifest.webmanifest",
                                     mimetype="application/manifest+json")


@app.route("/pocket/sw.js")
def pocket_service_worker():
    response = flask.send_from_directory(app.static_folder, "pocket/sw.js",
                                         mimetype="application/javascript")
    response.headers["Service-Worker-Allowed"] = "/pocket/"
    response.headers["Cache-Control"] = "no-cache"
    return response


@app.route("/api/pocket/pairing", methods=["POST"])
def api_pocket_pairing():
    if not _is_loopback_request():
        return jsonify({"error": "只能从桌面端生成配对码"}), 403
    issued = pocket_pairing.issue_code()
    issued.update({
        "url": f"http://{_local_ip()}:{config.web_port}/pocket/",
        "enabled": config.pocket_enabled,
        "tcp_enabled": bool(
            config.pocket_enabled and _pocket_tcp_bridge and _pocket_tcp_bridge.running
        ),
        "tcp_host": _local_ip(),
        "tcp_port": POCKET_TCP_PORT,
    })
    return jsonify(issued)


@app.route("/api/pocket/tcp-settings", methods=["GET", "POST"])
def api_pocket_tcp_settings():
    """Locally control the optional native Pocket TCP listener."""
    if request.method == "GET":
        return jsonify({
            "enabled": bool(config.pocket_tcp_enabled),
            "running": bool(_pocket_tcp_bridge and _pocket_tcp_bridge.running),
            "host": _local_ip(),
            "port": POCKET_TCP_PORT,
            "phone_transfer": _get_pocket_phone_transfer_progress(),
        })

    data = request.get_json(silent=True)
    if not isinstance(data, dict) or not isinstance(data.get("enabled"), bool):
        return jsonify({"error": "enabled 必须是布尔值"}), 400
    enabled = data["enabled"]
    previous = bool(config.pocket_tcp_enabled)
    try:
        _configure_pocket_tcp(enabled)
    except OSError as exc:
        return jsonify({"error": f"TCP 端口 {POCKET_TCP_PORT} 无法启动: {exc}"}), 409
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 400

    config.pocket_tcp_enabled = enabled
    try:
        config.save()
    except OSError as exc:
        config.pocket_tcp_enabled = previous
        if previous != enabled:
            try:
                _configure_pocket_tcp(previous)
            except OSError:
                pass
        return jsonify({"error": f"设置保存失败: {exc}"}), 500
    return jsonify({
        "enabled": enabled,
        "running": bool(_pocket_tcp_bridge and _pocket_tcp_bridge.running),
        "host": _local_ip(),
        "port": POCKET_TCP_PORT,
    })


@app.route("/api/pocket/connect", methods=["POST"])
def api_pocket_connect():
    data = request.get_json(silent=True) or {}
    token = pocket_pairing.connect(str(data.get("code", "")))
    if not token:
        return jsonify({"error": "配对码无效或已过期"}), 401
    response = jsonify({"status": "ok", "paired": True})
    response.set_cookie(
        "yingxu_pocket", token, max_age=config.pocket_session_hours * 3600,
        httponly=True, samesite="Lax", path="/",
    )
    return response


@app.route("/api/pocket/disconnect", methods=["POST"])
def api_pocket_disconnect():
    token = _pocket_token()
    if token:
        with _pocket_upload_sessions_lock:
            state = _pocket_upload_sessions.pop(token, None)
        if state:
            _pocket_mobile_inbox.abort(state["inbox_session"])
            finished = state.get("completed_indices", set()) | state.get("failed_indices", set())
            for item in state.get("manifest", []):
                if item["index"] not in finished:
                    db.update_file_status(state["backup_id"], item["source_path"], "cancelled",
                                          error="手机断开连接，原始手机文件未修改")
            if state.get("backup_id"):
                db.finish_backup(state["backup_id"], "cancelled", "手机已断开连接，未完成文件已清理")
            with _pocket_http_transfer_progress_lock:
                _pocket_http_transfer_progress.update({"status": "cancelled", "current_file": "",
                                                       "updatedAt": int(time.time() * 1000)})
    pocket_pairing.revoke(token)
    response = jsonify({"status": "ok"})
    response.delete_cookie("yingxu_pocket", path="/")
    return response


@app.route("/api/pocket/status")
@pocket_login_required
def api_pocket_status():
    progress = engine.progress.to_dict()
    return jsonify({
        "paired": True,
        "status": progress.get("status", "idle"),
        "progress": progress,
        "source": os.path.basename(progress.get("mount_point") or ""),
    })


@app.route("/api/pocket/history")
@pocket_login_required
def api_pocket_history():
    records = db.get_backups(limit=min(request.args.get("limit", 20, type=int), 50))
    clean = []
    for record in records:
        root = str(record.get("backup_root") or "")
        clean.append({
            "id": record.get("id"),
            "event_name": record.get("event_name") or "未命名任务",
            "started_at": record.get("started_at"),
            "status": record.get("status"),
            "total_files": record.get("total_files", 0),
            "skipped_files": record.get("skipped_files", 0),
            "failed_files": record.get("failed_files", 0),
            "target": os.path.basename(root.rstrip(os.sep)) or "本机",
        })
    return jsonify({"items": clean})


@app.route("/api/pocket/notes", methods=["GET", "POST"])
@pocket_login_required
def api_pocket_notes():
    if request.method == "POST":
        return jsonify({"status": "ok", "notes": pocket_notes.save(request.get_json(silent=True) or {})})
    return jsonify({"notes": pocket_notes.load()})


def _pocket_http_upload_state(create: bool = False):
    token = _pocket_token()
    if not token:
        return None
    with _pocket_upload_sessions_lock:
        if create:
            return _pocket_upload_sessions.setdefault(token, {
                "inbox_session": {}, "manifest": [], "backup_id": None,
                "active_index": None, "started_at": time.time(),
            })
        return _pocket_upload_sessions.get(token)


def _get_pocket_phone_transfer_progress():
    with _pocket_http_transfer_progress_lock:
        http_progress = dict(_pocket_http_transfer_progress)
    if http_progress.get("status") in {"copying", "paused", "queued"}:
        return http_progress
    return _pocket_tcp_bridge.mobile_transfer_progress if _pocket_tcp_bridge else http_progress


@app.route("/api/pocket/inbox/plan", methods=["POST"])
@pocket_login_required
def api_pocket_inbox_plan():
    """Create a desktop history row for a phone-to-computer LAN backup."""
    raw_data = request.get_json(silent=True)
    data = raw_data if isinstance(raw_data, dict) else {}
    event_name = str(data.get("event_name") or "手机备份").strip()[:120] or "手机备份"
    files = data.get("files")
    if not isinstance(files, list) or not files or len(files) > 1000:
        return jsonify({"error": "请选择 1 到 1000 个照片或视频文件"}), 400
    root_value = _pocket_mobile_inbox_root()
    if not root_value or not Path(root_value).expanduser().is_dir():
        return jsonify({"error": "请先在电脑偏好设置中选择可用的备份目标磁盘"}), 409
    manifest = []
    total_size = 0
    db_files = []
    photo_suffixes = {".arw", ".cr2", ".cr3", ".nef", ".nrw", ".dng", ".orf", ".rw2",
                      ".raf", ".srw", ".x3f", ".3fr", ".fff", ".mef", ".mos", ".jpg",
                      ".jpeg", ".png", ".heic", ".heif", ".tif", ".tiff", ".webp", ".bmp", ".gif"}
    video_suffixes = {".mp4", ".mov", ".m4v", ".avi", ".mxf", ".braw", ".mpg", ".mts",
                      ".m2ts", ".insv", ".webm", ".mkv", ".wmv", ".flv", ".3gp"}
    for index, item in enumerate(files):
        if not isinstance(item, dict):
            return jsonify({"error": "手机文件清单格式无效"}), 400
        relative = str(item.get("relative_path") or "")
        size = item.get("size")
        digest = str(item.get("sha256") or "").lower()
        if not relative or len(relative) > 1200 or not isinstance(size, int) or isinstance(size, bool) or size < 0 or size > MAX_TRANSFER_FILE_BYTES:
            return jsonify({"error": "手机文件名称或大小无效"}), 400
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            return jsonify({"error": "手机文件 SHA-256 清单无效"}), 400
        parts = relative.replace("\\", "/").split("/")
        if any(part in ("", ".", "..") for part in parts):
            return jsonify({"error": "手机文件路径无效"}), 400
        total_size += size
        if total_size > 100 * 1024 * 1024 * 1024:
            return jsonify({"error": "单次手机备份总容量不能超过 100 GB"}), 400
        source_path = f"手机端/{index}/{relative}"
        media_type = "video" if str(item.get("media_type")) == "video" else "photo"
        suffix = Path(parts[-1]).suffix.lower()
        if suffix not in (video_suffixes if media_type == "video" else photo_suffixes):
            return jsonify({"error": "手机上传仅接受支持的照片、RAW 或视频文件"}), 400
        manifest.append({"index": index, "relative_path": relative, "size": size,
                         "sha256": digest, "source_path": source_path, "media_type": media_type})
        db_files.append({"path": source_path, "camera": "手机导入", "media_type": media_type, "size": size})

    backup_root = str(Path(root_value).expanduser().resolve() / "影序手机备份")
    known_hashes = db.get_known_hashes(backup_root)
    skip_indices = {item["index"] for item in manifest if item["sha256"] in known_hashes}
    bytes_to_copy = sum(item["size"] for item in manifest if item["index"] not in skip_indices)
    try:
        free_bytes = shutil.disk_usage(root_value).free
    except OSError:
        return jsonify({"error": "无法读取电脑备份磁盘剩余空间，请重新选择可用的备份目标"}), 409
    if bytes_to_copy > free_bytes:
        return jsonify({"error": f"电脑备份磁盘空间不足：需要 {bytes_to_copy} 字节，可用 {free_bytes} 字节"}), 507

    state = _pocket_http_upload_state(create=True)
    if state.get("active_index") is not None:
        _pocket_mobile_inbox.abort(state["inbox_session"])
    if state.get("backup_id"):
        db.finish_backup(state["backup_id"], "partial", "新的手机任务替代了尚未完成的任务")
    backup_id = db.create_backup(event_name, backup_root, [backup_root])
    db.add_files(backup_id, db_files)
    for item in manifest:
        if item["index"] in skip_indices:
            item["result"] = "skipped"
            db.update_file_status(backup_id, item["source_path"], "skipped", verified=True,
                                  source_hash=item["sha256"], error="相同 SHA-256 已在电脑归档中")
    state.update({"manifest": manifest, "backup_id": backup_id, "active_index": None,
                  "started_at": time.time(), "event_name": event_name,
                  "completed_indices": set(skip_indices), "failed_indices": set()})
    with _pocket_http_transfer_progress_lock:
        _pocket_http_transfer_progress.update({"status": "copying", "current_file": "",
            "total_files": len(manifest), "processed_files": len(skip_indices), "copied_files": 0,
            "skipped_files": len(skip_indices),
            "bytes_copied": sum(item["size"] for item in manifest if item["index"] in skip_indices),
            "total_bytes": total_size,
            "updatedAt": int(time.time() * 1000)})
    return jsonify({"status": "planned", "backup_id": backup_id,
                    "total_files": len(manifest), "total_size": total_size,
                    "skip_indices": sorted(skip_indices)})


@app.route("/api/pocket/inbox/begin", methods=["POST"])
@pocket_login_required
def api_pocket_inbox_begin():
    state = _pocket_http_upload_state()
    raw_data = request.get_json(silent=True)
    data = raw_data if isinstance(raw_data, dict) else {}
    index = data.get("index")
    if not state or not isinstance(index, int) or index < 0 or index >= len(state["manifest"]):
        return jsonify({"error": "手机备份任务无效，请重新扫描"}), 409
    if state.get("active_index") is not None:
        _pocket_mobile_inbox.abort(state["inbox_session"])
    item = state["manifest"][index]
    try:
        result = _pocket_mobile_inbox.begin(
            state["inbox_session"], item["relative_path"], item["size"]
        )
    except (OSError, ValueError) as error:
        return jsonify({"error": str(error)}), 409
    state["active_index"] = index
    with _pocket_http_transfer_progress_lock:
        _pocket_http_transfer_progress.update({"status": "copying",
            "current_file": item["relative_path"].split("/")[-1],
            "updatedAt": int(time.time() * 1000)})
    return jsonify(result)


@app.route("/api/pocket/inbox/chunk", methods=["POST"])
@pocket_login_required
def api_pocket_inbox_chunk():
    if request.content_length and request.content_length > int(MAX_HTTP_POCKET_CHUNK_BYTES * 1.5) + 4096:
        return jsonify({"error": "手机文件分块超过限制"}), 413
    state = _pocket_http_upload_state()
    raw_data = request.get_json(silent=True)
    data = raw_data if isinstance(raw_data, dict) else {}
    if not state or state.get("active_index") != data.get("index"):
        return jsonify({"error": "当前没有对应的手机文件传输"}), 409
    try:
        result = _pocket_mobile_inbox.append(
            state["inbox_session"], int(data.get("offset", -1)),
            str(data.get("data") or ""), max_chunk_bytes=MAX_HTTP_POCKET_CHUNK_BYTES,
        )
    except (OSError, TypeError, ValueError) as error:
        return jsonify({"error": str(error)}), 400
    with _pocket_http_transfer_progress_lock:
        _pocket_http_transfer_progress.update({
            "bytes_copied": sum(value["size"] for value in state["manifest"]
                                if value["index"] in state.get("completed_indices", set())) + result["received"],
            "updatedAt": int(time.time() * 1000),
        })
    return jsonify(result)


@app.route("/api/pocket/inbox/finish", methods=["POST"])
@pocket_login_required
def api_pocket_inbox_finish():
    state = _pocket_http_upload_state()
    raw_data = request.get_json(silent=True)
    data = raw_data if isinstance(raw_data, dict) else {}
    index = data.get("index")
    if not state or state.get("active_index") != index or not state.get("backup_id"):
        return jsonify({"error": "当前没有对应的手机文件传输"}), 409
    if not isinstance(index, int) or index < 0 or index >= len(state["manifest"]):
        return jsonify({"error": "手机文件清单已失效"}), 409
    item = state["manifest"][index]
    try:
        result = _pocket_mobile_inbox.finish(state["inbox_session"], item["sha256"])
    except OSError:
        message = "电脑目标磁盘写入失败，请检查可用空间和写入权限"
        db.update_file_status(state["backup_id"], item["source_path"], "failed", error=message)
        state["failed_indices"].add(index)
        state["active_index"] = None
        return jsonify({"error": message}), 507
    except ValueError as error:
        db.update_file_status(state["backup_id"], item["source_path"], "failed", error=str(error))
        state["failed_indices"].add(index)
        state["active_index"] = None
        return jsonify({"error": str(error)}), 400
    status = "skipped" if result.get("status") == "skipped" else "completed"
    dest = str(Path(_pocket_mobile_inbox_root()).expanduser().resolve() / "影序手机备份" /
                result.get("relative_path", item["relative_path"]))
    db.update_file_status(state["backup_id"], item["source_path"], status, dest,
                          verified=True, source_hash=result["sha256"])
    state["completed_indices"].add(index)
    state["active_index"] = None
    item["result"] = result.get("status")
    with _pocket_http_transfer_progress_lock:
        _pocket_http_transfer_progress.update({
            "processed_files": len(state["completed_indices"]) + len(state["failed_indices"]),
            "copied_files": sum(1 for value in state["manifest"]
                                if value["index"] in state["completed_indices"] and value.get("result") != "skipped"),
            "skipped_files": sum(1 for value in state["manifest"]
                                 if value["index"] in state["completed_indices"] and value.get("result") == "skipped"),
            "bytes_copied": sum(value["size"] for value in state["manifest"]
                                 if value["index"] in state["completed_indices"]),
            "updatedAt": int(time.time() * 1000),
        })
    return jsonify(result)


@app.route("/api/pocket/inbox/abort", methods=["POST"])
@pocket_login_required
def api_pocket_inbox_abort():
    state = _pocket_http_upload_state()
    if state:
        _pocket_mobile_inbox.abort(state["inbox_session"])
        state["active_index"] = None
    return jsonify({"status": "aborted"})


@app.route("/api/pocket/inbox/progress", methods=["POST"])
@pocket_login_required
def api_pocket_inbox_progress():
    state = _pocket_http_upload_state()
    if not state or not state.get("backup_id"):
        return jsonify({"error": "没有进行中的手机备份"}), 409
    raw_data = request.get_json(silent=True)
    data = raw_data if isinstance(raw_data, dict) else {}
    status = str(data.get("status") or "copying")
    if status not in {"copying", "paused"}:
        return jsonify({"error": "传输状态无效"}), 400
    with _pocket_http_transfer_progress_lock:
        _pocket_http_transfer_progress.update({"status": status, "updatedAt": int(time.time() * 1000)})
    return jsonify({"status": status})


@app.route("/api/pocket/inbox/complete", methods=["POST"])
@pocket_login_required
def api_pocket_inbox_complete():
    state = _pocket_http_upload_state()
    if not state or not state.get("backup_id"):
        return jsonify({"error": "没有可完成的手机备份任务"}), 409
    raw_data = request.get_json(silent=True)
    data = raw_data if isinstance(raw_data, dict) else {}
    requested = str(data.get("status") or "completed")
    if requested not in {"completed", "partial", "cancelled", "failed"}:
        requested = "failed"
    if state.get("active_index") is not None:
        _pocket_mobile_inbox.abort(state["inbox_session"])
        state["active_index"] = None
    finished_indices = state.get("completed_indices", set()) | state.get("failed_indices", set())
    pending_status = "cancelled" if requested == "cancelled" else "failed"
    for item in state["manifest"]:
        if item["index"] not in finished_indices:
            db.update_file_status(state["backup_id"], item["source_path"], pending_status,
                                  error="任务结束前未传输；原始手机文件未修改")
    completed = len(state.get("completed_indices", set()))
    failed = len(state.get("failed_indices", set()))
    if requested == "completed" and completed < len(state["manifest"]):
        requested = "partial" if completed else "failed"
    elif requested == "cancelled" and completed:
        requested = "partial"
    if failed and requested == "completed":
        requested = "partial"
    db.finish_backup(state["backup_id"], requested,
                     str(data.get("message") or "")[:500])
    backup_id = state["backup_id"]
    with _pocket_upload_sessions_lock:
        _pocket_upload_sessions.pop(_pocket_token(), None)
    with _pocket_http_transfer_progress_lock:
        _pocket_http_transfer_progress.update({"status": requested, "current_file": "",
                                               "updatedAt": int(time.time() * 1000)})
    threading.Thread(target=_sync_latest_desktop_history, daemon=True,
                     name=f"yingxu-pocket-history-{backup_id}").start()
    return jsonify({"status": requested, "backup_id": backup_id})


@app.route("/website")
def website():
    """产品官网 landing page"""
    return flask.send_from_directory(
        os.path.join(_base, "website"), "index.html"
    )


@app.route("/download/macos")
def download_macos():
    """下载 macOS .app"""
    zip_path = os.path.join(_base, "desktop", "dist")
    # 如果 dist 下有 zip 就提供，否则提示
    zip_file = os.path.join(zip_path, "SmartMediaBackup-macOS.zip")
    if os.path.exists(zip_file):
        return flask.send_file(zip_file, as_attachment=True,
                               download_name="SmartMediaBackup-macOS.zip")
    return jsonify({"error": "下载文件未就绪"}), 404


@app.route("/api/open_folder", methods=["POST"])
def api_open_folder():
    """打开目标文件夹"""
    data = request.get_json(silent=True) or {}
    path = data.get("path", "").strip()
    if not path:
        return jsonify({"error": "路径不能为空"}), 400
    if not os.path.exists(path):
        return jsonify({"error": "路径不存在"}), 404
    try:
        if sys.platform == "darwin":
            subprocess.Popen(["open", path])
        elif sys.platform.startswith("win"):
            os.startfile(path)  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["xdg-open", path])
        return jsonify({"status": "ok"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/choose_folder", methods=["POST"])
def api_choose_folder():
    """选择备份目标文件夹（macOS 原生 / Windows 原生对话框）"""
    if sys.platform == "darwin":
        try:
            result = subprocess.run(
                ["osascript", "-e", 'POSIX path of (choose folder with prompt "选择影序备份位置")'],
                capture_output=True, text=True, timeout=120,
            )
            if result.returncode != 0:
                return jsonify({"status": "cancelled"})
            folder = result.stdout.strip().rstrip("/")
        except Exception as exc:
            return jsonify({"error": f"打开文件夹选择器失败: {exc}"}), 500
    elif sys.platform.startswith("win"):
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        try:
            folder = filedialog.askdirectory(title="选择影序备份位置")
        finally:
            root.destroy()
        if not folder:
            return jsonify({"status": "cancelled"})
    else:
        return jsonify({"error": "当前平台暂不支持原生文件夹选择器"}), 501
    if not folder or not os.path.isdir(folder):
        return jsonify({"error": "未选择有效文件夹"}), 400
    try:
        usage = shutil.disk_usage(folder)
        size_free = usage.free
    except OSError:
        size_free = 0
    return jsonify({
        "status": "ok",
        "name": os.path.basename(folder) or folder,
        "mount_point": folder,
        "size_free": size_free,
    })


# ====== 百度网盘 API ======

@app.route("/api/baidu/status")
def api_baidu_status():
    """百度网盘配置和授权状态"""
    from .baidu import baidu
    return jsonify({
        "configured": baidu.is_configured(),
        "authorized": baidu.is_authorized(),
        "api_key": baidu.api_key[:6] + "..." if baidu.api_key else "",
    })


@app.route("/api/baidu/settings", methods=["POST"])
def api_baidu_settings():
    """保存百度网盘 API 配置"""
    from .baidu import baidu
    data = request.get_json(silent=True) or {}
    baidu.configure(
        api_key=data.get("api_key", ""),
        secret_key=data.get("secret_key", ""),
        app_id=data.get("app_id", ""),
    )
    return jsonify({"status": "ok"})


@app.route("/api/baidu/auth_url")
def api_baidu_auth_url():
    """获取授权 URL"""
    from .baidu import baidu
    if not baidu.is_configured():
        return jsonify({"error": "请先配置 API Key"}), 400
    return jsonify({"url": baidu.get_auth_url()})


@app.route("/api/baidu/exchange", methods=["POST"])
def api_baidu_exchange():
    """兑换授权码"""
    from .baidu import baidu
    data = request.get_json(silent=True) or {}
    code = data.get("code", "").strip()
    if not code:
        return jsonify({"error": "请输入授权码"}), 400
    ok = baidu.exchange_code(code)
    return jsonify({"ok": ok})


@app.route("/api/baidu/quota")
def api_baidu_quota():
    """网盘容量信息"""
    from .baidu import baidu
    quota = baidu.get_quota()
    if quota.get("error"):
        return jsonify(quota), 502
    return jsonify(quota)


# ====== AI 命名 API ======

@app.route("/api/ai/status")
def api_ai_status():
    """AI 命名配置状态"""
    from .ai_namer import ai_namer
    return jsonify({
        "enabled": ai_namer.is_enabled(),
        "backend": ai_namer.backend,
        "ollama_model": ai_namer.ollama_model,
        "openai_model": ai_namer.openai_model,
        "openai_base": ai_namer.openai_base,
        "cloud_provider": ai_namer.cloud_provider,
        "cloud_key_providers": sorted(ai_namer.cloud_key_providers),
    })


@app.route("/api/ai/settings", methods=["POST"])
def api_ai_settings():
    """保存 AI 命名配置"""
    from .ai_namer import ai_namer, CLOUD_PROVIDER_PRESETS
    if request.remote_addr not in {"127.0.0.1", "::1"}:
        return jsonify({"error": "AI 密钥设置只能在这台电脑本机修改"}), 403
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return jsonify({"error": "AI 设置格式无效"}), 400
    provider = data.get("cloud_provider", ai_namer.cloud_provider)
    if provider not in CLOUD_PROVIDER_PRESETS:
        return jsonify({"error": "不支持的云端模型服务"}), 400
    backend = data.get("backend", ai_namer.backend)
    if backend not in {"disabled", "ollama", "openai"}:
        return jsonify({"error": "不支持的 AI 后端"}), 400
    model = data.get("openai_model", CLOUD_PROVIDER_PRESETS[provider]["model"])
    if not isinstance(model, str):
        return jsonify({"error": "模型名称格式无效"}), 400

    ai_namer.backend = backend
    ai_namer.ollama_model = data.get("ollama_model", ai_namer.ollama_model)
    ai_namer.ollama_url = data.get("ollama_url", ai_namer.ollama_url)
    key = data.get("openai_key", "")
    if isinstance(key, str) and key.strip():
        try:
            ai_namer.set_cloud_key(provider, key.strip())
        except Exception:
            return jsonify({"error": "无法保存 API Key 到系统凭据库，请检查系统钥匙串/凭据管理器"}), 500
    ai_namer.use_cloud_provider(provider)
    ai_namer.openai_model = model.strip() or CLOUD_PROVIDER_PRESETS[provider]["model"]
    # Provider endpoints are fixed so a locally supplied key cannot be sent to an
    # arbitrary URL through a mistyped or manipulated settings form.
    ai_namer.openai_base = CLOUD_PROVIDER_PRESETS[provider]["base_url"]
    try:
        ai_namer.save()
    except Exception:
        return jsonify({"error": "无法保存 AI 设置或写入系统凭据库，请检查本机钥匙串/凭据管理器"}), 500
    return jsonify({"status": "ok"})


@app.route("/api/ai/test", methods=["POST"])
def api_ai_test():
    """Test the configured cloud model with a generated 1x1 image only."""
    from .ai_namer import ai_namer
    if request.remote_addr not in {"127.0.0.1", "::1"}:
        return jsonify({"error": "AI 模型测试只能在这台电脑本机发起"}), 403
    if ai_namer.backend != "openai":
        return jsonify({"error": "请先启用云端模型 API 并保存设置"}), 400
    try:
        ai_namer.test_cloud_vision()
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        status = getattr(exc, "status_code", None)
        if status in (401, 403):
            message = "API Key 无效、模型无权限，或免费额度已停止"
        elif status == 429:
            message = "请求频率或免费额度已达到当前限制"
        elif status and status >= 500:
            message = "模型服务暂时不可用，请稍后再试"
        else:
            message = "连接失败，请检查 API 地址、支持图片输入的模型名称和网络"
        return jsonify({"error": message}), 502
    return jsonify({"ok": True, "message": f"{ai_namer.cloud_provider} 图像模型连接成功；仅发送了内置测试图片"})


# ====== API ======

@app.route("/api/status")
def api_status():
    """返回当前状态"""
    from . import __version__
    return jsonify({
        "status": engine.progress.status,
        "progress": engine.progress.to_dict(),
        "version": __version__,
    })


@app.route("/api/updates/latest")
def api_latest_update():
    """Check the newest public GitHub release and provide this OS download."""
    import re
    import urllib.error
    import ssl
    import urllib.request
    import certifi
    from . import __version__

    release_url = "https://api.github.com/repos/yingxu-app/smart-media-backup/releases/latest"
    try:
        request = urllib.request.Request(
            release_url,
            headers={"Accept": "application/vnd.github+json", "User-Agent": "YINGXU-Update-Checker"},
        )
        tls_context = ssl.create_default_context(cafile=certifi.where())
        with urllib.request.urlopen(request, timeout=12, context=tls_context) as response:
            release = json.loads(response.read().decode("utf-8"))
    except Exception as error:
        if isinstance(error, urllib.error.HTTPError):
            diagnostic_code = f"HTTP_{int(error.code)}"
        elif isinstance(error, urllib.error.URLError):
            reason = error.reason
            if isinstance(reason, ssl.SSLError):
                diagnostic_code = "TLS_ERROR"
            elif isinstance(reason, TimeoutError):
                diagnostic_code = "TIMEOUT"
            else:
                diagnostic_code = "NETWORK_ERROR"
        elif isinstance(error, ssl.SSLError):
            diagnostic_code = "TLS_ERROR"
        elif isinstance(error, TimeoutError):
            diagnostic_code = "TIMEOUT"
        else:
            diagnostic_code = "UPDATE_CHECK_FAILED"
        app.logger.warning("Latest release lookup failed (%s)", diagnostic_code)
        return jsonify({
            "error": f"更新服务暂时无法访问（诊断码 {diagnostic_code}）。请检查网络后重试。",
            "diagnostic_code": diagnostic_code,
        }), 502

    def version_key(value):
        numbers = re.findall(r"\d+", str(value or ""))
        return tuple(int(number) for number in numbers[:4]) or (0,)

    latest = str(release.get("tag_name") or "").strip().lstrip("vV")
    assets = release.get("assets") if isinstance(release.get("assets"), list) else []
    if sys.platform == "darwin":
        platform_name, suffixes = "macOS", (".dmg",)
    elif sys.platform.startswith("win"):
        platform_name, suffixes = "Windows", (".exe", ".msi")
    else:
        platform_name, suffixes = "Linux", (".appimage", ".deb", ".rpm")
    platform_assets = [
        {"name": str(asset.get("name") or ""), "url": str(asset.get("browser_download_url") or ""),
         "size": int(asset.get("size") or 0), "digest": str(asset.get("digest") or "")}
        for asset in assets
        if str(asset.get("name") or "").lower().endswith(suffixes)
        and asset.get("browser_download_url")
    ]
    current = __version__
    return jsonify({
        "current_version": current,
        "latest_version": latest,
        "update_available": bool(latest) and version_key(latest) > version_key(current),
        "platform": platform_name,
        "assets": platform_assets,
        "release_url": str(release.get("html_url") or "https://github.com/yingxu-app/smart-media-backup/releases/latest"),
        "published_at": str(release.get("published_at") or ""),
    })


@app.route("/api/updates/download")
def api_download_latest_update():
    """Download the current platform's latest release after SHA-256 verification."""
    import hashlib
    import re
    import ssl
    import tempfile
    import urllib.error
    import urllib.request
    import certifi

    asset_name = str(request.args.get("asset") or "")
    if not re.fullmatch(r"YINGXU-(macOS|Windows|Linux)-v[0-9A-Za-z.+-]+\.(dmg|exe|msi|appimage|deb|rpm)", asset_name):
        return jsonify({"error": "更新文件名无效"}), 400
    if sys.platform == "darwin":
        platform_name, suffixes = "macOS", (".dmg",)
    elif sys.platform.startswith("win"):
        platform_name, suffixes = "Windows", (".exe", ".msi")
    else:
        platform_name, suffixes = "Linux", (".appimage", ".deb", ".rpm")

    tls_context = ssl.create_default_context(cafile=certifi.where())

    def read_json(url):
        req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json",
                                                  "User-Agent": "YINGXU-Update-Checker"})
        with urllib.request.urlopen(req, timeout=30, context=tls_context) as response:
            return json.loads(response.read().decode("utf-8"))

    try:
        release = read_json("https://api.github.com/repos/yingxu-app/smart-media-backup/releases/latest")
        assets = release.get("assets") if isinstance(release.get("assets"), list) else []
        asset = next((item for item in assets if str(item.get("name") or "") == asset_name), None)
        if not asset or not asset_name.lower().endswith(suffixes):
            return jsonify({"error": f"最新正式发行版没有适用于 {platform_name} 的这个文件"}), 404
        download_url = str(asset.get("browser_download_url") or "")
        if not download_url.startswith("https://github.com/yingxu-app/smart-media-backup/releases/download/"):
            return jsonify({"error": "发行文件来源不受信任，已停止下载"}), 502
        expected_size = int(asset.get("size") or 0)
        if expected_size > 1024 * 1024 * 1024:
            return jsonify({"error": "更新文件超过 1 GB 下载限制"}), 413
        expected_digest = str(asset.get("digest") or "").lower()
        match = re.fullmatch(r"sha256:([0-9a-f]{64})", expected_digest)
        if match:
            expected_digest = match.group(1)
        else:
            checksum_asset = next((item for item in assets if item.get("name") == "SHA256SUMS.txt"), None)
            if not checksum_asset:
                return jsonify({"error": "正式发行版没有提供 SHA-256 校验值"}), 502
            checksum_url = str(checksum_asset.get("browser_download_url") or "")
            if not checksum_url.startswith("https://github.com/yingxu-app/smart-media-backup/releases/download/"):
                return jsonify({"error": "校验文件来源不受信任"}), 502
            checksum_request = urllib.request.Request(checksum_url, headers={"User-Agent": "YINGXU-Update-Checker"})
            with urllib.request.urlopen(checksum_request, timeout=30, context=tls_context) as response:
                manifest = response.read(256 * 1024).decode("utf-8", "replace")
            expected_digest = ""
            for line in manifest.splitlines():
                parts = line.strip().split()
                if len(parts) >= 2 and parts[-1].lstrip("*") == asset_name and re.fullmatch(r"[0-9a-fA-F]{64}", parts[0]):
                    expected_digest = parts[0].lower()
                    break
            if not expected_digest:
                return jsonify({"error": "校验清单中没有该更新文件的 SHA-256"}), 502

        file_request = urllib.request.Request(download_url, headers={"User-Agent": "YINGXU-Update-Checker"})
        temporary_path = ""
        digest = hashlib.sha256()
        actual_size = 0
        try:
            with urllib.request.urlopen(file_request, timeout=60, context=tls_context) as response:
                with tempfile.NamedTemporaryFile(prefix="yingxu-update-", suffix="-" + asset_name,
                                                 delete=False) as output:
                    temporary_path = output.name
                    while True:
                        block = response.read(1024 * 1024)
                        if not block:
                            break
                        actual_size += len(block)
                        if actual_size > 1024 * 1024 * 1024 or (expected_size and actual_size > expected_size):
                            raise ValueError("下载大小超过发行元数据")
                        digest.update(block)
                        output.write(block)
            if expected_size and actual_size != expected_size:
                raise ValueError("下载文件大小与发行元数据不一致")
            if digest.hexdigest() != expected_digest:
                raise ValueError("下载文件 SHA-256 与正式发行清单不一致")
        except Exception:
            if temporary_path:
                Path(temporary_path).unlink(missing_ok=True)
            raise

        response = send_file(temporary_path, as_attachment=True, download_name=asset_name,
                             mimetype="application/octet-stream", conditional=True)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Yingxu-SHA256"] = expected_digest
        response.headers["X-Yingxu-Verified"] = "true"
        response.call_on_close(lambda: Path(temporary_path).unlink(missing_ok=True))
        return response
    except ValueError as error:
        return jsonify({"error": f"更新包校验失败：{error}"}), 502
    except Exception as error:
        if isinstance(error, urllib.error.HTTPError):
            code = f"HTTP_{error.code}"
        elif isinstance(error, urllib.error.URLError) and isinstance(error.reason, ssl.SSLError):
            code = "TLS_ERROR"
        elif isinstance(error, TimeoutError):
            code = "TIMEOUT"
        else:
            code = "UPDATE_DOWNLOAD_FAILED"
        app.logger.warning("Verified update download failed (%s)", code)
        return jsonify({"error": f"更新包下载失败，诊断码 {code}；未提供未校验文件"}), 502


@app.route("/api/volumes")
def api_volumes():
    """列出所有可用卷（用于目标磁盘选择）"""
    all_vols = list_removable_volumes()
    # 过滤系统卷
    system_mounts = {"/System/Volumes/VM", "/System/Volumes/Preboot",
                     "/System/Volumes/Update", "/System/Volumes/xarts", "/System/Volumes/iSCPreboot",
                     "/System/Volumes/Hardware", "/System/Volumes/Data"}
    result = []

    # 常用本地文件夹
    import os as _os
    if sys.platform.startswith("win"):
        local_folders = [
            _os.path.expanduser('~/Desktop'),
            _os.path.expanduser('~/Documents'),
            _os.path.expanduser('~/Downloads'),
            _os.path.expanduser('~/Pictures'),
            _os.path.expanduser('~/Videos'),
        ]
    else:
        local_folders = [
            _os.path.expanduser('~/Desktop'),
            _os.path.expanduser('~/Documents'),
            _os.path.expanduser('~/Downloads'),
            _os.path.expanduser('~/Pictures'),
            _os.path.expanduser('~/Movies'),
        ]
    for p in local_folders:
        if _os.path.isdir(p):
            try:
                usage = shutil.disk_usage(p)
                result.append({
                    'name': '📁 ' + _os.path.basename(p),
                    'mount_point': p,
                    'size_total': usage.total,
                    'size_used': usage.used,
                    'size_free': usage.free,
                    'fstype': 'local',
                })
            except OSError:
                pass

    for v in all_vols:
        mount = v["mount_point"]
        # 排除系统卷
        if mount.rstrip("/") in system_mounts or any(mount.startswith(s) for s in system_mounts):
            continue
        # macOS: 排除 Macintosh HD
        if sys.platform == "darwin" and "macintosh" in mount.lower():
            continue
        # 排除根目录
        if mount == "/":
            continue
        # 排除 >5TB (NAS)
        total = v.get("size_total", 0)
        if total > 5 * 1024**4:
            continue
        item = dict(v)
        item.setdefault('size_free', max(0, item.get('size_total', 0) - item.get('size_used', 0)))
        result.append(item)
    return jsonify(result)


@app.route("/api/scan", methods=["GET", "POST"])
def api_scan():
    """扫描 SD 卡内容 (GET 和 POST 均可)"""
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
    else:
        data = {}
    mount_point = data.get("mount_point", "")

    if not mount_point:
        # macOS 上 /Volumes 同时包含外置 SSD 和 SD 卡，不能按枚举顺序误选。
        # Windows 读卡器可能把卡报成固定盘，因此用候选来源卷（removable + 非系统 fixed）。
        source = find_likely_media_source(list_candidate_source_volumes())
        if source:
            mount_point = source["mount_point"]

    if not mount_point or not _is_existing_mount(mount_point):
        return jsonify({"error": "未检测到 SD 卡", "files": [], "devices": []})

    # WebKit 首次进入、自动恢复和用户点击刷新有可能在很短时间内同时请求
    # 扫描。外置 FAT/exFAT 卡不适合被多个 os.walk 并发遍历：系统调用可能
    # 长时间等待，表现成首页一直停在“等待插卡”。这里只允许一个真实扫描；
    # 并发请求直接复用刚完成的同卡结果，或明确告诉前端稍候重试。
    if not _scan_lock.acquire(blocking=False):
        cached = _scan_response_cache.get(mount_point)
        if cached:
            return jsonify({**cached, "cached": True})
        return jsonify({
            "error": "正在读取存储卡，请稍候",
            "scanning": True,
            "mount_point": mount_point,
            "files": [],
            "devices": [],
        }), 409

    try:
        from .organizer import (
            scan_sd_card,
            batch_extract_metadata,
            build_date_groups,
            date_group_key,
            display_device_name,
        )
        raw = scan_sd_card(mount_point)
        if not raw:
            return jsonify({"error": "未找到照片或视频文件", "files": [], "devices": []})

        files = batch_extract_metadata(raw)

    # 按设备统计
        devices = {}
        for f in files:
            cam = f.get("camera", "Unknown")
            if cam not in devices:
                devices[cam] = {"files": 0, "photos": 0, "videos": 0, "size": 0}
            devices[cam]["files"] += 1
            devices[cam]["size"] += f.get("size", 0)
            mt = f.get("media_type", "other")
            if mt in ("photo", "raw"):
                devices[cam]["photos"] += 1
            elif mt == "video":
                devices[cam]["videos"] += 1

    # 构建返回
        device_list = [{"name": display_device_name(k), "raw_name": k, **v} for k, v in devices.items()]
        file_list = [{
            "filename": f["filename"],
            "camera": f.get("camera", ""),
            "media_type": f.get("media_type", ""),
            "size": f.get("size", 0),
        } for f in files[:500]]  # 前端只展示前 500 个

        preview_items = []
        group_previews = {}
        previewable_extensions = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp"}
        for index, item in enumerate(files):
            suffix = Path(item.get("path", "")).suffix.lower()
            if item.get("media_type") == "photo" and suffix in previewable_extensions:
                preview = {"id": index, "filename": item.get("filename", "照片")}
                if len(preview_items) < 4:
                    preview_items.append(preview)
                group_key = date_group_key(item.get("date"))
                group_previews.setdefault(group_key, [])
                if len(group_previews[group_key]) < 8:
                    group_previews[group_key].append(preview)

        global _scan_cache
        event_groups = build_date_groups(files)
        for group in event_groups:
            group["previews"] = group_previews.get(group["date_key"], [])
        _scan_cache = {"files": files, "devices": device_list, "event_groups": event_groups,
                       "mount_point": str(Path(mount_point).expanduser().resolve())}

    # 扫描必须始终快速、离线且可预测。AI 命名属于可选增强，不得阻塞
    # 存储卡识别；尤其不能在首页轮询请求中等待本地模型超时。
    # 基础建议直接使用 EXIF 日期，事件名仍可由用户在工作台中修改。
        suggested_name = ""
        if files:
            from collections import Counter
            dates = [str(f.get("date", ""))[:10] for f in files if f.get("date", "")]
            if dates:
                d = Counter(dates).most_common(1)[0][0]
                suggested_name = d + "拍摄"

        response = {
            "devices": device_list,
            "files": file_list,
            "total_files": len(files),
            "total_size": sum(f.get("size", 0) for f in files),
            "mount_point": mount_point,
            "source_name": Path(mount_point).name,
            "suggested_name": suggested_name,
            "event_groups": event_groups,
            "previews": preview_items,
        }
        _scan_response_cache[mount_point] = response
        return jsonify(response)
    finally:
        _scan_lock.release()


@app.route("/api/preview/<int:file_index>")
def api_preview(file_index: int):
    """只提供刚刚扫描来源中的少量照片预览，不接受外部任意路径。"""
    files = _scan_cache.get("files", [])
    if file_index < 0 or file_index >= len(files):
        return jsonify({"error": "预览不存在"}), 404
    item = files[file_index]
    path = Path(item.get("path", ""))
    allowed_extensions = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp"}
    if item.get("media_type") != "photo" or path.suffix.lower() not in allowed_extensions or not path.is_file():
        return jsonify({"error": "该文件不能直接预览"}), 404
    return send_file(path, conditional=True, max_age=0)


@app.route("/api/sources")
def api_sources():
    """列出可作为备份来源的已挂载卡/磁盘，供用户明确选择。"""
    from .organizer import scan_sd_card
    sources = []
    for volume in list_candidate_source_volumes():
        mount = volume.get("mount_point", "")
        if not mount or not _is_existing_mount(mount):
            continue
        media = scan_sd_card(mount)
        if not media:
            continue
        photos = sum(1 for item in media if item.get("media_type") in ("photo", "raw"))
        videos = sum(1 for item in media if item.get("media_type") == "video")
        sources.append({
            "name": volume.get("name") or Path(mount).name,
            "mount_point": mount,
            "total_files": len(media),
            "photos": photos,
            "videos": videos,
            "total_size": sum(item.get("size", 0) for item in media),
        })
    return jsonify({"sources": sources})


@app.route("/api/start_backup", methods=["POST"])
def api_start_backup():
    """开始备份"""
    if engine.progress.status in ("copying", "verifying", "paused"):
        return jsonify({"error": "正在备份中，请等待完成"}), 409

    data = request.get_json(silent=True) or {}
    mount_point = data.get("mount_point", "")
    event_name = data.get("event_name", "").strip()
    event_names = data.get("event_names") or []
    event_groups = data.get("event_groups") or []
    if not event_names and event_name:
        event_names = [e.strip() for e in event_name.replace("，", ",").split(",") if e.strip()]
    backup_root = data.get("backup_root", "")
    backup_targets = data.get("backup_targets") or []
    requested_sort_order = data.get("sort_order") or []
    naming_parts = data.get("naming_parts") or []

    if not event_names and event_name:
        event_names = [e.strip() for e in event_name.replace("，", ",").split(",") if e.strip()]
    if not backup_root and not backup_targets:
        return jsonify({"error": "请选择备份目标位置"}), 400
    if not mount_point:
        source = find_likely_media_source(list_candidate_source_volumes())
        if source:
            mount_point = source["mount_point"]
    if not mount_point or not os.path.isdir(mount_point):
        return jsonify({"error": "未检测到 SD 卡"}), 400

    # 保存配置
    config.last_backup_root = backup_root
    allowed_sort_parts = {"date", "event", "location", "device", "type"}
    cleaned_sort_order = [part for part in requested_sort_order if part in allowed_sort_parts]
    if cleaned_sort_order:
        config.sort_order = cleaned_sort_order
    if backup_targets:
        config.backup_targets = list(dict.fromkeys(backup_targets))
    config.save()

    # 在新线程运行备份
    def _run():
        try:
            engine.run(mount_point, event_name, backup_root,
                       enable_verify=config.verify_method == "sha256",
                       backup_targets=backup_targets or None,
                       event_names=event_names or None,
                       event_groups=event_groups or None,
                       naming_parts=naming_parts or None)
        except Exception as e:
            print(f"[SMB] 备份失败: {e}", file=sys.stderr)

    t = threading.Thread(target=_run, daemon=True)
    t.start()

    return jsonify({"status": "started"})


@app.route("/api/cancel_backup", methods=["POST"])
def api_cancel_backup():
    """取消备份"""
    engine.cancel()
    return jsonify({"status": "cancelling"})


@app.route("/api/pause_backup", methods=["POST"])
def api_pause_backup():
    """暂停或继续当前任务；暂停发生在完整文件边界。"""
    if engine.progress.status == "paused":
        engine.resume()
        return jsonify({"status": "resumed"})
    if engine.progress.status not in ("copying", "verifying"):
        return jsonify({"error": "当前没有可暂停的备份任务"}), 409
    engine.pause()
    return jsonify({"status": "paused"})


def _windows_send_to_recycle_bin(paths: list[str]) -> tuple[bool, str]:
    """Windows：把一批文件移入回收站（可恢复），而不是永久删除。

    返回 (成功?, 失败原因)。任何失败都必须由调用方转成错误，绝不能退化成
    os.remove 永久删除——原卡素材一旦被硬删就无法找回。
    """
    import ctypes
    from ctypes import wintypes

    FO_DELETE = 3
    FOF_SILENT = 0x0004
    FOF_NOCONFIRMATION = 0x0010
    FOF_ALLOWUNDO = 0x0040          # 关键：进回收站而非直接抹除
    FOF_NOERRORUI = 0x0400

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [
            ("hwnd", wintypes.HWND),
            ("wFunc", wintypes.UINT),
            ("pFrom", ctypes.c_void_p),
            ("pTo", ctypes.c_void_p),
            ("fFlags", wintypes.WORD),
            ("fAnyOperationsAborted", wintypes.BOOL),
            ("hNameMappings", ctypes.c_void_p),
            ("lpszProgressTitle", ctypes.c_void_p),
        ]

    # SHFileOperationW 需要双 \0 结尾的路径列表。
    joined = "\0".join(os.path.abspath(p) for p in paths) + "\0"
    buffer = ctypes.create_unicode_buffer(joined)

    op = SHFILEOPSTRUCTW()
    op.hwnd = None
    op.wFunc = FO_DELETE
    op.pFrom = ctypes.cast(buffer, ctypes.c_void_p)
    op.pTo = None
    op.fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI
    op.fAnyOperationsAborted = False
    op.hNameMappings = None
    op.lpszProgressTitle = None

    result = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    if result != 0:
        return False, f"系统回收站操作失败（代码 {result}）"
    if op.fAnyOperationsAborted:
        return False, "操作被中止"
    return True, ""


def _remove_empty_dirs(root: str):
    """素材搬走后清掉留下的空目录，交给系统的失败一律忽略。"""
    for current, dirs, files in os.walk(root, topdown=False):
        if os.path.abspath(current) == os.path.abspath(root):
            continue
        try:
            if not os.listdir(current):
                os.rmdir(current)
        except OSError:
            pass


@app.route("/api/cleanup_sd", methods=["POST"])
def api_cleanup_sd():
    """备份完成后把 SD 卡文件移入系统废纸篓/回收站（绝不永久删除）。"""
    mp = engine.progress.mount_point
    if not mp:
        return jsonify({"error": "没有可清理的SD卡"}), 400
    if not engine.progress.can_cleanup:
        return jsonify({"error": "备份未完成，不能清理"}), 400
    try:
        files_to_clear = []
        for root, dirs, files in os.walk(mp):
            for f in files:
                files_to_clear.append(os.path.join(root, f))
        if not files_to_clear:
            engine.progress.can_cleanup = False
            engine.progress.notify()
            return jsonify({"status": "cleaned", "deleted": 0})

        if sys.platform == "darwin":
            failed = 0
            for fp in files_to_clear:
                done = subprocess.run(
                    ["osascript", "-e",
                     f'tell app "Finder" to delete (POSIX file "{fp}" as alias)'],
                    capture_output=True, timeout=30,
                )
                if done.returncode != 0:
                    failed += 1
            if failed:
                return jsonify({
                    "error": f"{failed} 个文件未能移入废纸篓，已停止清理；原卡素材未被永久删除。"
                }), 500
        elif sys.platform.startswith("win"):
            ok, reason = _windows_send_to_recycle_bin(files_to_clear)
            if not ok:
                return jsonify({
                    "error": f"无法移入回收站，已停止清理（原卡素材保持原样）: {reason}"
                }), 500
        else:
            # 没有统一、可恢复的回收站时宁可拒绝，也不做不可逆的永久删除。
            return jsonify({
                "error": "当前平台没有可恢复的回收站，已拒绝清理以避免素材无法找回。"
            }), 501

        _remove_empty_dirs(mp)
        engine.progress.can_cleanup = False
        engine.progress.notify()
        return jsonify({"status": "cleaned", "deleted": len(files_to_clear)})
    except Exception as e:
        return jsonify({"error": f"清理失败: {e}"}), 500


@app.route("/api/sync/export")
def api_sync_export():
    """导出备份历史（供其他实例同步）"""
    history = db.get_all_history()
    db_path = str(db.DB_PATH)
    return jsonify({
        "version": "1.0",
        "exported_at": datetime.now().isoformat(),
        "history": history,
    })


@app.route("/api/sync/import", methods=["POST"])
def api_sync_import():
    """导入外部备份历史记录"""
    data = request.get_json() or {}
    records = data.get("history", [])
    imported = db.import_history(records)
    return jsonify({"imported": imported})


@app.route("/api/sync/pull", methods=["POST"])
def api_sync_pull():
    """从远程实例拉取备份历史"""
    data = request.get_json() or {}
    remote_url = data.get("url", "").strip().rstrip("/")
    if not remote_url:
        return jsonify({"error": "请输入远程地址"})
    try:
        import urllib.request
        resp = urllib.request.urlopen(f"{remote_url}/api/sync/export", timeout=15)
        remote_data = json.loads(resp.read().decode())
        imported = db.import_history(remote_data.get("history", []))
        return jsonify({"imported": imported, "source": remote_url})
    except Exception as e:
        return jsonify({"error": f"同步失败: {e}"})


@app.route("/api/history")
def api_history():
    """获取历史记录"""
    limit = request.args.get("limit", 20, type=int)
    offset = request.args.get("offset", 0, type=int)
    query = request.args.get("query", "", type=str).strip()
    status = request.args.get("status", "", type=str).strip()
    target = request.args.get("target", "", type=str).strip()
    scope = request.args.get("scope", "", type=str).strip()
    date_from = request.args.get("date_from", "", type=str).strip()
    date_to = request.args.get("date_to", "", type=str).strip()
    records = db.get_backups(limit, offset, query, status, target, date_from, date_to, scope)
    total = db.count_backups(query, status, target, date_from, date_to, scope)
    return jsonify({
        "items": records,
        "total": total,
        "limit": limit,
        "offset": offset,
        "filters": {
            "query": query,
            "status": status,
            "target": target,
            "scope": scope,
            "date_from": date_from,
            "date_to": date_to,
        }
    })


@app.route("/api/history_targets")
def api_history_targets():
    """按位置类别返回历史目标，避免把本机、外置盘和测试目录混在同一列表。"""
    home = Path.home().resolve()
    local_aliases = {
        "Desktop": "桌面", "Documents": "文稿", "Downloads": "下载",
        "Pictures": "图片", "Movies": "影片",
    }
    groups = {
        "local": {"id": "local", "label": "本机位置", "items": []},
        "external": {"id": "external", "label": "外接存储", "items": []},
        "other": {"id": "other", "label": "其他位置", "items": []},
        "test": {"id": "test", "label": "历史测试目录", "items": []},
    }

    for raw_path in db.get_backup_targets():
        normalized = os.path.normpath(raw_path)
        path_obj = Path(normalized)
        path_parts = {part.lower() for part in path_obj.parts}
        is_test_path = any(marker in part for part in path_parts for marker in (
            "acceptance", "validation", "fixture", "test", "tmp",
        ))

        category = "other"
        label = normalized
        try:
            relative = path_obj.resolve().relative_to(home)
            parts = relative.parts
            if parts:
                root_label = local_aliases.get(parts[0], parts[0])
                label = " / ".join((root_label, *parts[1:]))
            else:
                label = "个人目录"
            category = "local"
        except (ValueError, OSError):
            if len(path_obj.parts) >= 3 and path_obj.parts[1] == "Volumes":
                volume_name = path_obj.parts[2]
                label = " / ".join((volume_name, *path_obj.parts[3:]))
                category = "external"
            else:
                label = path_obj.name or normalized

        # 验收和临时目录仍可查询，但不干扰摄影师日常的目标盘选择。
        if is_test_path:
            category = "test"
        groups[category]["items"].append({"value": raw_path, "label": label})

    for group in groups.values():
        group["items"].sort(key=lambda item: item["label"].casefold())
    return jsonify({"groups": [group for group in groups.values() if group["items"]]})


@app.route("/api/history/<int:backup_id>")
def api_history_detail(backup_id):
    """获取单条历史详情"""
    record = db.get_backup(backup_id)
    files = db.get_backup_files(backup_id, 500)
    return jsonify({"record": record, "files": files, "targets": db.get_target_results(backup_id)})


@app.route("/api/history/<int:backup_id>/report/<report_format>")
def api_history_report(backup_id, report_format):
    """下载本机生成的 JSON/CSV/Markdown 报告。"""
    suffixes = {"json": ".json", "csv": ".csv", "markdown": ".md"}
    if report_format not in suffixes:
        return jsonify({"error": "不支持的报告格式"}), 400
    record = db.get_backup(backup_id)
    if not record or not record.get("report_path"):
        return jsonify({"error": "这条记录还没有报告"}), 404
    report_path = Path(record["report_path"]).with_suffix(suffixes[report_format]).resolve()
    from .config import CONFIG_DIR
    reports_root = (CONFIG_DIR / "reports").resolve()
    if reports_root not in report_path.parents or not report_path.is_file():
        return jsonify({"error": "报告文件不存在或路径无效"}), 404
    return send_file(report_path, as_attachment=True, download_name=report_path.name)


@app.route("/api/history/<int:backup_id>/rename", methods=["POST"])
def api_history_rename(backup_id):
    data = request.get_json(silent=True) or {}
    name = str(data.get("event_name", "")).strip()
    if not name:
        return jsonify({"error": "请输入新的项目名称"}), 400
    if not db.rename_backup(backup_id, name):
        return jsonify({"error": "未找到该历史记录"}), 404
    return jsonify({"status": "ok", "event_name": name})


@app.route("/api/history/clear", methods=["POST"])
def api_history_clear():
    """清空历史记录；真实备份目录、报告和原卡素材不会被删除。"""
    data = request.get_json(silent=True) or {}
    if data.get("confirm") != "CLEAR_HISTORY":
        return jsonify({"error": "请确认清空历史记录"}), 400
    return jsonify({"status": "ok", "cleared": db.clear_history()})


# ====== SocketIO ======

@socketio.on("connect")
def on_connect():
    emit("connected", {"status": "ok"})


@socketio.on("request_status")
def on_request_status():
    emit("backup_progress", engine.progress.to_dict())


@app.route("/api/settings", methods=["GET", "POST"])
def api_settings():
    """读写高级设置"""
    from .config import config
    if request.method == "POST":
        data = request.get_json() or {}
        if "webhook_url" in data:
            config.webhook_url = data["webhook_url"]
        if "max_speed_mbps" in data:
            config.max_speed_mbps = int(data["max_speed_mbps"])
        if "phash_threshold" in data:
            config.phash_threshold = int(data["phash_threshold"])
        if "sort_order" in data:
            config.sort_order = data["sort_order"]
        config.save()
        return jsonify({"status": "saved"})
    return jsonify({
        "webhook_url": config.webhook_url,
        "max_speed_mbps": config.max_speed_mbps,
        "phash_threshold": config.phash_threshold,
        "sort_order": config.sort_order,
    })


@app.route("/api/firebase/settings", methods=["GET", "POST"])
def api_firebase_settings():
    global _firebase_sync_client
    if request.method == "GET":
        signed_in = bool(_firebase_sync_client and _firebase_sync_client.uid)
        if not signed_in and config.firebase_uid:
            signed_in = _restore_firebase_session()
        return jsonify({
            "email": config.firebase_email,
            "display_name": config.firebase_display_name,
            "configured": bool(config.firebase_api_key and config.firebase_database_url),
            "signed_in": signed_in,
        })
    data = request.get_json(silent=True) or {}
    api_key = str(data.get("api_key", config.firebase_api_key)).strip()
    database_url = str(data.get("database_url", config.firebase_database_url)).strip().rstrip("/")
    email = str(data.get("email", config.firebase_email)).strip()
    if database_url and not database_url.startswith("https://"):
        return jsonify({"error": "Realtime Database 地址必须使用 HTTPS。"}), 400
    if _firebase_sync_client:
        _firebase_sync_client.stop_live_watch()
    config.firebase_api_key = api_key
    config.firebase_database_url = database_url
    config.firebase_email = email
    config.save()
    _firebase_sync_client = None
    return jsonify({"status": "saved", "configured": bool(api_key and database_url)})


def _firebase_authenticate(create_account: bool):
    data = request.get_json(silent=True) or {}
    email = str(data.get("email", config.firebase_email)).strip()
    password = str(data.get("password", ""))
    display_name = str(data.get("display_name", "")).strip()
    if not email or not password:
        return jsonify({"error": "请输入 Firebase 邮箱和密码。"}), 400
    if create_account and not 1 <= len(display_name) <= 40:
        return jsonify({"error": "注册时请填写 1 到 40 个字符的用户名。"}), 400
    if data.get("api_key") is not None or data.get("database_url") is not None:
        saved = api_firebase_settings()
        if isinstance(saved, tuple):
            return saved
    try:
        client = _get_firebase_sync_client()
        if create_account:
            account = client.sign_in(
                email, password, create_account=True, display_name=display_name,
            )
        else:
            account = client.sign_in(email, password, create_account=False, display_name=display_name)
    except Exception as error:
        if isinstance(error, RuntimeError) and str(error).startswith("账号已"):
            return jsonify({"error": str(error)}), 502
        if isinstance(error, FirebaseSyncError):
            return jsonify({"error": str(error)}), 502
        if isinstance(error, ValueError):
            messages = {
                "EMAIL_EXISTS": "这个邮箱已注册，请直接登录。",
                "EMAIL_NOT_FOUND": "这个邮箱尚未注册，请先注册账号。",
                "INVALID_EMAIL": "邮箱格式无效。",
                "INVALID_PASSWORD": "邮箱或密码不正确。",
                "INVALID_LOGIN_CREDENTIALS": "邮箱或密码不正确。",
                "USER_DISABLED": "此账号已停用，请联系 Firebase 项目所有者。",
                "TOO_MANY_ATTEMPTS_TRY_LATER": "尝试次数过多，请稍后再试。",
                "WEAK_PASSWORD": "密码未达到 Firebase 要求，请更换更强的密码。",
                "OPERATION_NOT_ALLOWED": "Firebase 项目尚未启用邮箱/密码登录。",
                "API_KEY_INVALID": "Firebase Web API Key 无效，请检查应用配置。",
                "CONFIGURATION_NOT_FOUND": "Firebase Authentication 尚未完成项目配置。",
            }
            return jsonify({"error": messages.get(str(error), "Firebase 账号验证失败。")}), 400
        # Surface a safe exception class so the user can distinguish an unexpected
        # local failure without exposing exception text, tokens, or request URLs.
        error_type = type(error).__name__
        return jsonify({
            "error": f"Firebase 登录未完成（本机错误类型：{error_type}）。请将错误类型提供给支持人员；不要发送密码。"
        }), 400
    config.firebase_email = account["email"]
    config.firebase_uid = account["uid"]
    config.firebase_display_name = account.get("display_name") or display_name or email.split("@", 1)[0]
    config.save()
    client.start_live_watch()
    threading.Thread(target=_sync_latest_desktop_history, daemon=True, name="yingxu-firebase-history").start()
    return jsonify({
        "status": "signed_in",
        "email": account["email"],
        "display_name": config.firebase_display_name,
    })


@app.route("/api/firebase/login", methods=["POST"])
def api_firebase_login():
    return _firebase_authenticate(create_account=False)


@app.route("/api/firebase/register", methods=["POST"])
def api_firebase_register():
    return _firebase_authenticate(create_account=True)


@app.route("/api/firebase/logout", methods=["POST"])
def api_firebase_logout():
    global _firebase_sync_client
    try:
        if _firebase_sync_client:
            _firebase_sync_client.stop_live_watch()
            _firebase_sync_client.sign_out()
        elif config.firebase_uid:
            KeyringTokenStore().remove(config.firebase_uid)
    except Exception:
        return jsonify({"error": "无法清除系统凭据库中的 Firebase 会话。"}), 500
    config.firebase_uid = ""
    config.firebase_display_name = ""
    config.save()
    _firebase_sync_client = None
    return jsonify({"status": "signed_out"})


@app.route("/api/firebase/history")
def api_firebase_history():
    try:
        client = _get_firebase_sync_client()
        if not client.uid and not client.restore_session(
            config.firebase_uid, config.firebase_email, config.firebase_display_name
        ):
            return jsonify({"error": "请先登录影序云同步账号。"}), 401
        client.start_live_watch()
        values = client.get("history") or {}
        items = sorted(values.values(), key=lambda item: str(item.get("startedAt") or ""), reverse=True)
        return jsonify({"items": items[:100]})
    except Exception:
        return jsonify({"error": "读取 Firebase 历史失败，请检查账号、项目配置、数据库规则或网络。"}), 502


@app.route("/api/firebase/live")
def api_firebase_live():
    try:
        client = _get_firebase_sync_client()
        if not client.uid and not client.restore_session(
            config.firebase_uid, config.firebase_email, config.firebase_display_name
        ):
            return jsonify({"error": "请先登录影序云同步账号。"}), 401
        client.start_live_watch()
        return jsonify(client.live_snapshot())
    except Exception:
        return jsonify({"error": "读取 Firebase 实时进度失败，请检查账号、项目配置、数据库规则或网络。"}), 502


@app.route("/api/setup/status")
def api_setup_status():
    """返回当前安装状态（用于首次启动引导）"""
    import shutil
    ollama_installed = shutil.which("ollama") is not None
    from .ai_namer import ai_namer
    return jsonify({
        "ollama_installed": ollama_installed,
        "ai_enabled": ai_namer.is_enabled(),
        "show_setup": not ollama_installed and not ai_namer.is_enabled(),
    })


@app.route("/api/setup/install_ollama", methods=["POST"])
def api_setup_install_ollama():
    """静默安装 Ollama（命令行版，无图标）"""
    import subprocess, shutil
    if shutil.which("ollama"):
        return jsonify({"status": "already_installed"})
    try:
        # brew install ollama = CLI only, no GUI icon
        result = subprocess.run(
            ["brew", "install", "ollama"],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode != 0:
            return jsonify({"error": result.stderr[:200]})
        # 启动 ollama 后台服务
        subprocess.run(["ollama", "serve"], capture_output=True, timeout=5)
        return jsonify({"status": "installed"})
    except Exception as e:
        return jsonify({"error": str(e)})


@app.route("/api/setup/pull_ai_model", methods=["POST"])
def api_setup_pull_model():
    """下载 AI 视觉模型（llava）"""
    import subprocess
    try:
        result = subprocess.run(
            ["ollama", "pull", "llava"],
            capture_output=True, text=True, timeout=300,
        )
        if result.returncode != 0:
            return jsonify({"error": result.stderr[:200]})
        # 自动启用 AI 命名
        from .ai_namer import ai_namer
        ai_namer.backend = "ollama"
        ai_namer.ollama_model = "llava"
        ai_namer.save()
        return jsonify({"status": "ready"})
    except Exception as e:
        return jsonify({"error": str(e)})


# ====== 启动 ======

def main(open_browser: bool = True, host_override: str | None = None):
    """启动 Web 服务"""
    import socket

    # 找可用端口
    port = config.web_port
    host = host_override or config.web_host

    # 数据库初始化
    db.init_db()

    # 确保配置目录存在
    from .config import CONFIG_DIR
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    if config.pocket_tcp_enabled and config.pocket_enabled:
        try:
            _configure_pocket_tcp(True)
        except OSError as exc:
            print(f"[Pocket TCP] 无法监听端口 {POCKET_TCP_PORT}: {exc}")

    global _open_browser_on_start
    _open_browser_on_start = open_browser

    # 启动 SD 卡监听
    def _on_sd_insert(volume: dict):
        # 自动打开浏览器（macOS）
        if _open_browser_on_start and config.auto_open_browser:
            import webbrowser
            webbrowser.open(f'http://localhost:{port}')
        print(f"[SMB] 📸 SD 卡已插入: {volume['name']} ({volume['mount_point']})")

    watcher = SDCardWatcher(on_insert=_on_sd_insert)
    watcher.start()

    print(f"""
╔══════════════════════════════════════════╗
║          影序 YINGXU  v1.0.40           ║
║                                          ║
║  打开浏览器访问:                         ║
║    http://localhost:{port}                ║
║                                          ║
║  按 Ctrl+C 停止服务                      ║
╚══════════════════════════════════════════╝
""")

    # 先启动服务，就绪后再弹浏览器（消除启动等待感）
    import threading, time, urllib.request

    def _open_when_ready():
        for _ in range(30):
            try:
                urllib.request.urlopen(f'http://{host}:{port}/api/status', timeout=0.5)
                break
            except Exception:
                time.sleep(0.5)
        if _open_browser_on_start and config.auto_open_browser:
            import webbrowser
            webbrowser.open(f'http://localhost:{port}')

    threading.Thread(target=_open_when_ready, daemon=True).start()

    socketio.run(app, host=host, port=port, debug=False, allow_unsafe_werkzeug=True)


if __name__ == "__main__":
    main()
