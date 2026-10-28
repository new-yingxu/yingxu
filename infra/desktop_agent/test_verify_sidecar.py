import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from verify_sidecar import read_ready_line, verify_manifest


class ReadyLineTest(unittest.TestCase):
    def test_reads_ready_line_from_pipe(self):
        import subprocess
        import sys
        child = subprocess.Popen([sys.executable, "-c", "print('ready', flush=True)"], stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(read_ready_line(child.stdout, timeout=5).strip(), "ready")
        finally:
            child.wait(timeout=5)
            child.stdout.close()

    def test_eof_is_an_explicit_failure(self):
        with self.assertRaisesRegex(RuntimeError, "exited"):
            read_ready_line(io.StringIO(""), timeout=1)


class VerifySidecarManifest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="yingxu-sidecar-manifest-")
        self.bundle = Path(self.temp.name) / "yingxu-local-agent"
        self.bundle.mkdir()
        (self.bundle / "yingxu-local-agent").write_bytes(b"executable")
        internal = self.bundle / "_internal"
        internal.mkdir()
        (internal / "runtime.bin").write_bytes(b"runtime")
        self.records = [
            self._record("yingxu-local-agent"),
            self._record("_internal/runtime.bin"),
        ]
        self._write_manifest()

    def tearDown(self):
        self.temp.cleanup()

    def _record(self, relative):
        path = self.bundle / relative
        data = path.read_bytes()
        return {"path": relative, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}

    def _write_manifest(self, *, entrypoint="yingxu-local-agent", files=None):
        (self.bundle / "sidecar-manifest.json").write_text(json.dumps({
            "name": "yingxu-local-agent",
            "platform": "darwin",
            "architecture": "arm64",
            "entrypoint": entrypoint,
            "files": self.records if files is None else files,
        }), encoding="utf-8")

    def test_accepts_complete_bundle_with_matching_hashes(self):
        manifest = verify_manifest(self.bundle)
        self.assertEqual(manifest["entrypoint"], "yingxu-local-agent")
        self.assertEqual(len(manifest["files"]), 2)

    def test_rejects_tampered_file(self):
        (self.bundle / "_internal/runtime.bin").write_bytes(b"changed")
        with self.assertRaisesRegex(RuntimeError, "hash does not match"):
            verify_manifest(self.bundle)

    def test_rejects_unmanifested_file(self):
        (self.bundle / "unexpected.txt").write_text("not packaged", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "file set does not match"):
            verify_manifest(self.bundle)

    def test_rejects_path_escape_record(self):
        self.records.append({"path": "../outside", "size": 0, "sha256": "0" * 64})
        self._write_manifest()
        with self.assertRaisesRegex(RuntimeError, "unsafe or duplicate"):
            verify_manifest(self.bundle)

    def test_rejects_unlisted_entrypoint(self):
        self._write_manifest(entrypoint="missing-agent")
        with self.assertRaisesRegex(RuntimeError, "entrypoint is not a listed"):
            verify_manifest(self.bundle)

    def test_rejects_duplicate_json_keys(self):
        (self.bundle / "sidecar-manifest.json").write_text(
            '{"name":"yingxu-local-agent","name":"other","files":[]}', encoding="utf-8"
        )
        with self.assertRaisesRegex(RuntimeError, "manifest is missing or invalid JSON"):
            verify_manifest(self.bundle)

    def test_rejects_manifest_symlink_outside_bundle(self):
        outside = Path(self.temp.name) / "outside.json"
        outside.write_text("{}", encoding="utf-8")
        manifest = self.bundle / "sidecar-manifest.json"
        manifest.unlink()
        try:
            manifest.symlink_to(outside)
        except OSError as error:
            if getattr(error, 'winerror', None) == 1314:
                self.skipTest('Windows symlink privilege is unavailable')
            raise
        with self.assertRaisesRegex(RuntimeError, "manifest is missing or escaping"):
            verify_manifest(self.bundle)


if __name__ == "__main__":
    unittest.main()
