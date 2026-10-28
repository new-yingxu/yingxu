import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from build_sidecar import APP_NAME, executable_name, stage_bundle


class BuildSidecarTests(unittest.TestCase):
    def test_platform_entrypoint_suffix(self):
        self.assertEqual(executable_name("darwin"), APP_NAME)
        self.assertEqual(executable_name("linux"), APP_NAME)
        self.assertEqual(executable_name("win32"), APP_NAME + ".exe")

    def test_stage_copies_complete_bundle_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "build" / APP_NAME
            (source / "_internal").mkdir(parents=True)
            (source / APP_NAME).write_bytes(b"sidecar executable fixture")
            (source / "_internal" / "runtime.fixture").write_bytes(b"runtime fixture")
            files = []
            for relative in (APP_NAME, "_internal/runtime.fixture"):
                data = (source / relative).read_bytes()
                files.append({"path": relative, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()})
            (source / "sidecar-manifest.json").write_text(json.dumps({
                "name": APP_NAME,
                "entrypoint": APP_NAME,
                "files": files,
            }))
            target = root / "tauri" / "agent-sidecar" / APP_NAME

            staged = stage_bundle(source, target)

            self.assertEqual(staged, target.resolve())
            self.assertEqual((target / APP_NAME).read_bytes(), b"sidecar executable fixture")
            self.assertEqual((target / "_internal" / "runtime.fixture").read_bytes(), b"runtime fixture")
            self.assertTrue((target / "sidecar-manifest.json").is_file())
            with self.assertRaisesRegex(SystemExit, "refusing to overwrite"):
                stage_bundle(source, target)

    def test_stage_rejects_tampered_source_before_copy(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "build" / APP_NAME
            source.mkdir(parents=True)
            executable = source / APP_NAME
            executable.write_bytes(b"original")
            record = {"path": APP_NAME, "size": 8, "sha256": hashlib.sha256(b"original").hexdigest()}
            (source / "sidecar-manifest.json").write_text(json.dumps({
                "name": APP_NAME,
                "entrypoint": APP_NAME,
                "files": [record],
            }))
            executable.write_bytes(b"tampered")
            target = root / "tauri" / APP_NAME

            with self.assertRaisesRegex(SystemExit, "refusing to stage invalid"):
                stage_bundle(source, target)
            self.assertFalse(target.exists())

    def test_stage_rejects_destination_nested_in_source(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / APP_NAME
            source.mkdir()
            with self.assertRaisesRegex(SystemExit, "inside its own source"):
                stage_bundle(source, source / "nested")


if __name__ == "__main__":
    unittest.main()
