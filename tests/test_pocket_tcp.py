import json
import base64
import hashlib
import socket
import tempfile
import time
import unittest
from pathlib import Path
from urllib.parse import quote, unquote

from smb.pocket import PocketNotesStore
from smb.pocket_tcp import DesktopMediaSource, MobileInbox, PocketTcpBridge


class FakePairingManager:
    def __init__(self):
        self.authorized = set()
        self.revoked = []

    def connect(self, code):
        if code != "123456":
            return None
        token = "session-token"
        self.authorized.add(token)
        return token

    def is_authorized(self, token):
        return token in self.authorized

    def revoke(self, token):
        self.authorized.discard(token)
        self.revoked.append(token)


class PocketTcpBridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.pairing = FakePairingManager()
        self.notes = PocketNotesStore(Path(self.temp_dir.name) / "notes.json")
        self.backup_root = Path(self.temp_dir.name) / "backup"
        self.backup_root.mkdir()
        self.source_root = Path(self.temp_dir.name) / "source"
        self.source_root.mkdir()
        self.source_file = self.source_root / "IMG_0001.JPG"
        self.source_file.write_bytes(b"desktop photo bytes")
        desktop_source = DesktopMediaSource(lambda: {
            "mount_point": str(self.source_root),
            "files": [{"path": str(self.source_file), "filename": self.source_file.name,
                       "media_type": "photo", "camera": "Camera", "date": "2026-10-18"}],
        })
        self.bridge = PocketTcpBridge(
            pairing=self.pairing,
            status_provider=lambda: {"status": "copying", "phase_progress": 25},
            history_provider=lambda limit: [{"id": i} for i in range(limit)],
            notes_store=self.notes,
            host="127.0.0.1",
            port=0,
            mobile_inbox=MobileInbox(lambda: self.backup_root),
            desktop_media_source=desktop_source,
        )
        self.port = self.bridge.start()

    def tearDown(self):
        self.bridge.stop()
        self.temp_dir.cleanup()

    def test_pair_then_read_status_and_disconnect(self):
        paired = self.bridge.handle_message(
            {"id": 1, "command": "pair", "code": "123456"}, "192.168.1.20"
        )
        self.assertTrue(paired["ok"])
        self.assertEqual(paired["token"], "session-token")

        status = self.bridge.handle_message(
            {"id": 2, "command": "status", "token": paired["token"]}
        )
        self.assertEqual(status["data"]["status"], "copying")
        self.assertEqual(status["id"], 2)

        disconnected = self.bridge.handle_message(
            {"id": 3, "command": "disconnect", "token": paired["token"]}
        )
        self.assertTrue(disconnected["disconnected"])
        self.assertFalse(self.pairing.is_authorized(paired["token"]))

    def test_rejects_unauthorized_and_unknown_commands(self):
        unauthorized = self.bridge.handle_message(
            {"id": "a", "command": "history", "token": "bad"}
        )
        self.assertFalse(unauthorized["ok"])
        self.assertEqual(unauthorized["error"], "未配对或会话已过期")
        unknown = self.bridge.handle_message({"id": "b", "command": "backup"})
        self.assertFalse(unknown["ok"])
        self.assertEqual(unknown["error"], "不支持的操作")

    def test_limits_history_and_saves_only_note_fields(self):
        self.pairing.authorized.add("session-token")
        history = self.bridge.handle_message(
            {"command": "history", "token": "session-token", "limit": 500}
        )
        self.assertEqual(len(history["items"]), 50)
        saved = self.bridge.handle_message({
            "command": "notes.save",
            "token": "session-token",
            "data": {"event": "山景", "location": "山顶", "secret": "discard"},
        })
        self.assertEqual(saved["notes"]["event"], "山景")
        self.assertNotIn("secret", saved["notes"])

    def test_mobile_cannot_control_desktop_backup_actions(self):
        self.pairing.authorized.add("session-token")
        for command in ("desktop.start", "desktop.pause", "desktop.cancel"):
            response = self.bridge.handle_message({
                "id": command, "command": command, "token": "session-token",
            })
            self.assertFalse(response["ok"])
            self.assertEqual(response["error"], "不支持的操作")

    def test_authenticated_mobile_upload_is_confined_and_sha256_verified(self):
        self.pairing.authorized.add("session-token")
        session = {}
        connection = {"session-token"}
        payload = b"verified photo bytes\x00\xff"
        digest = hashlib.sha256(payload).hexdigest()
        begin = self.bridge.handle_message({
            "id": 1, "command": "file.begin", "token": "session-token",
            "relativePath": "2026-10-18/event/Photo/image.jpg", "size": len(payload),
        }, connection_tokens=connection, session=session)
        self.assertTrue(begin["ok"])
        chunk = self.bridge.handle_message({
            "id": 2, "command": "file.chunk", "token": "session-token",
            "offset": 0, "data": base64.b64encode(payload).decode("ascii"),
        }, connection_tokens=connection, session=session)
        self.assertEqual(chunk["received"], len(payload))
        finished = self.bridge.handle_message({
            "id": 3, "command": "file.finish", "token": "session-token", "sha256": digest,
        }, connection_tokens=connection, session=session)
        self.assertEqual(finished["status"], "copied")
        saved = self.backup_root / "影序手机备份/2026-10-18/event/Photo/image.jpg"
        self.assertEqual(saved.read_bytes(), payload)

        rejected = self.bridge.handle_message({
            "id": 4, "command": "file.begin", "token": "session-token",
            "relativePath": "../../outside.txt", "size": 1,
        }, connection_tokens=connection, session=session)
        self.assertFalse(rejected["ok"])
        self.assertIn("路径无效", rejected["error"])

    def test_mobile_upload_hash_failure_removes_partial_file(self):
        inbox = MobileInbox(lambda: self.backup_root)
        session = {}
        inbox.begin(session, "event/failed.jpg", 4)
        inbox.append(session, 0, base64.b64encode(b"data").decode("ascii"))
        with self.assertRaisesRegex(ValueError, "SHA-256 校验失败"):
            inbox.finish(session, "0" * 64)
        self.assertFalse((self.backup_root / "影序手机备份/event/failed.jpg").exists())

    def test_paired_mobile_reads_scanned_desktop_files_without_absolute_path_leak(self):
        self.pairing.authorized.add("session-token")
        session = {}
        token = "session-token"
        listed = self.bridge.handle_message({"command": "desktop.files.list", "token": token}, session=session)
        self.assertTrue(listed["ok"])
        self.assertEqual(listed["total"], 1)
        item = listed["items"][0]
        self.assertEqual(item["name"], "IMG_0001.JPG")
        self.assertNotIn("path", item)
        self.assertNotIn(str(self.source_root), json.dumps(listed))

        started = self.bridge.handle_message({"command": "desktop.file.begin", "token": token,
                                               "sourceId": item["source_id"]}, session=session)
        self.assertTrue(started["ok"])
        self.assertEqual(started["size"], len(b"desktop photo bytes"))
        self.assertEqual(started["sha256"], hashlib.sha256(b"desktop photo bytes").hexdigest())
        chunk = self.bridge.handle_message({"command": "desktop.file.chunk", "token": token,
                                             "sourceId": item["source_id"], "offset": 0, "length": 4096}, session=session)
        self.assertTrue(chunk["ok"])
        self.assertEqual(base64.b64decode(chunk["data"]), b"desktop photo bytes")
        finished = self.bridge.handle_message({"command": "desktop.file.finish", "token": token,
                                                "sourceId": item["source_id"]}, session=session)
        self.assertTrue(finished["ok"])

    def test_mobile_import_progress_is_paired_sanitized_and_visible_to_desktop(self):
        denied = self.bridge.handle_message({"command": "desktop.import.progress", "token": "bad",
                                               "data": {"status": "copying"}})
        self.assertFalse(denied["ok"])
        self.pairing.authorized.add("session-token")
        result = self.bridge.handle_message({
            "command": "desktop.import.progress", "token": "session-token",
            "data": {"status": "copying", "currentFile": "private/folder/IMG_2.JPG",
                     "processedFiles": 2, "totalFiles": 5, "bytesCopied": 123,
                     "computerPath": "/Users/private", "token": "secret"},
        })
        self.assertTrue(result["ok"])
        self.assertEqual(result["progress"]["current_file"], "IMG_2.JPG")
        self.assertEqual(result["progress"]["processed_files"], 2)
        self.assertNotIn("computerPath", result["progress"])
        self.assertNotIn("token", result["progress"])
        self.assertEqual(self.bridge.mobile_transfer_progress, result["progress"])

    def test_tcp_wire_protocol_transfers_a_large_desktop_chunk_within_frame_limit(self):
        from smb.pocket_tcp import MAX_DESKTOP_READ_CHUNK_BYTES
        payload = bytes(range(256)) * (MAX_DESKTOP_READ_CHUNK_BYTES // 256)
        self.source_file.write_bytes(payload)
        with socket.create_connection(("127.0.0.1", self.port), timeout=10) as client:
            stream = client.makefile("rwb", buffering=0)

            def request(value):
                stream.write((quote(json.dumps(value), safe="") + "\n").encode("ascii"))
                return json.loads(unquote(stream.readline().decode("ascii")))

            paired = request({"id": 1, "command": "pair", "code": "123456"})
            token = paired["token"]
            listed = request({"id": 2, "command": "desktop.files.list", "token": token})
            source_id = listed["items"][0]["source_id"]
            started = request({"id": 3, "command": "desktop.file.begin", "token": token, "sourceId": source_id})
            self.assertTrue(started["ok"])
            chunk = request({"id": 4, "command": "desktop.file.chunk", "token": token,
                             "sourceId": source_id, "offset": 0, "length": MAX_DESKTOP_READ_CHUNK_BYTES})
            self.assertTrue(chunk["ok"])
            self.assertEqual(base64.b64decode(chunk["data"]), payload)

    def test_desktop_source_rejects_unpaired_changed_and_outside_files(self):
        denied = self.bridge.handle_message({"command": "desktop.files.list", "token": "bad"}, session={})
        self.assertFalse(denied["ok"])
        self.pairing.authorized.add("session-token")
        session = {}
        listed = self.bridge.handle_message({"command": "desktop.files.list", "token": "session-token"}, session=session)
        source_id = listed["items"][0]["source_id"]
        self.bridge.handle_message({"command": "desktop.file.begin", "token": "session-token",
                                    "sourceId": source_id}, session=session)
        self.source_file.write_bytes(b"changed after hash")
        changed = self.bridge.handle_message({"command": "desktop.file.chunk", "token": "session-token",
                                               "sourceId": source_id, "offset": 0, "length": 64}, session=session)
        self.assertFalse(changed["ok"])
        self.assertIn("变化", changed["error"])

        outside = Path(self.temp_dir.name) / "outside.jpg"
        outside.write_bytes(b"outside")
        source = DesktopMediaSource(lambda: {"mount_point": str(self.source_root), "files": [
            {"path": str(outside), "filename": outside.name, "media_type": "photo"},
        ]})
        self.assertEqual(source.list_page({}, 0, 40)["items"], [])

    def test_limits_failed_pairing_attempts_per_peer(self):
        for attempt in range(5):
            result = self.bridge.handle_message(
                {"command": "pair", "code": "000000"}, "192.168.1.20"
            )
            self.assertEqual(result["error"], "配对码无效或已过期")
        blocked = self.bridge.handle_message(
            {"command": "pair", "code": "123456"}, "192.168.1.20"
        )
        self.assertEqual(blocked["error"], "尝试次数过多，请稍后重试")
        other_peer = self.bridge.handle_message(
            {"command": "pair", "code": "123456"}, "192.168.1.21"
        )
        self.assertTrue(other_peer["ok"])

    def test_tcp_wire_protocol_handles_multiple_requests_and_utf8(self):
        with socket.create_connection(("127.0.0.1", self.port), timeout=2) as client:
            stream = client.makefile("rwb", buffering=0)
            request = {"id": 1, "command": "pair", "code": "123456"}
            stream.write((quote(json.dumps(request), safe="") + "\n").encode("ascii"))
            first = json.loads(unquote(stream.readline().decode("ascii")))
            self.assertEqual(first["token"], "session-token")

            notes = {"event": "星空拍摄", "location": "湖边", "device": "相机", "notes": "夜景"}
            request = {
                "id": 2,
                "command": "notes.save",
                "token": first["token"],
                "data": notes,
            }
            stream.write((quote(json.dumps(request, ensure_ascii=False), safe="") + "\n").encode("ascii"))
            second = json.loads(unquote(stream.readline().decode("ascii")))
            self.assertEqual(second["notes"]["event"], "星空拍摄")
            self.assertEqual(second["id"], 2)

    def test_session_is_bound_to_socket_and_revoked_when_client_disconnects(self):
        client = socket.create_connection(("127.0.0.1", self.port), timeout=2)
        stream = client.makefile("rwb", buffering=0)
        try:
            pair = {"id": 1, "command": "pair", "code": "123456"}
            stream.write((quote(json.dumps(pair), safe="") + "\n").encode("ascii"))
            paired = json.loads(unquote(stream.readline().decode("ascii")))
            token = paired["token"]
            self.assertTrue(self.pairing.is_authorized(token))

            other = socket.create_connection(("127.0.0.1", self.port), timeout=2)
            other_stream = other.makefile("rwb", buffering=0)
            try:
                status = {"id": 2, "command": "status", "token": token}
                other_stream.write(
                    (quote(json.dumps(status), safe="") + "\n").encode("ascii")
                )
                rejected = json.loads(unquote(other_stream.readline().decode("ascii")))
                self.assertFalse(rejected["ok"])
                self.assertEqual(rejected["error"], "未配对或会话已过期")
            finally:
                other_stream.close()
                other.close()
        finally:
            stream.close()
            client.shutdown(socket.SHUT_RDWR)
            client.close()

        deadline = time.monotonic() + 2
        while self.pairing.is_authorized(token) and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertFalse(self.pairing.is_authorized(token))


if __name__ == "__main__":
    unittest.main()
