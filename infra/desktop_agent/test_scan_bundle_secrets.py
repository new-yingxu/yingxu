from pathlib import Path
import tempfile
import unittest

from scan_bundle_secrets import scan_bundle


class ScanBundleSecrets(unittest.TestCase):
    def test_clean_bundle_has_no_findings(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "app.bin").write_bytes(b"normal app payload")
            report = scan_bundle(root)
        self.assertEqual(report["status"], "passed")
        self.assertEqual(report["findingCount"], 0)
        self.assertEqual(report["filesScanned"], 1)

    def test_reports_known_key_signatures_without_values(self):
        secret = b"sk-proj-" + b"A" * 28
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "nested").mkdir()
            (root / "nested" / "config.bin").write_bytes(b"OPENAI_API_KEY=" + secret)
            report = scan_bundle(root)
        self.assertEqual(report["status"], "findings")
        self.assertEqual(report["findings"], [
            {"rule": "openai_api_key", "files": ["nested/config.bin"]},
            {"rule": "named_secret_assignment", "files": ["nested/config.bin"]},
        ])
        self.assertNotIn(secret.decode(), str(report))

    def test_rejects_symlink_escape(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "bundle"
            root.mkdir()
            outside = Path(temp) / "outside.bin"
            outside.write_bytes(b"not in bundle")
            try:
                (root / "outside.bin").symlink_to(outside)
            except OSError as error:
                if getattr(error, 'winerror', None) == 1314:
                    self.skipTest('Windows symlink privilege is unavailable')
                raise
            with self.assertRaisesRegex(ValueError, "escaping"):
                scan_bundle(root)


if __name__ == "__main__":
    unittest.main()
