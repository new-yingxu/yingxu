import unittest

from app.config import production_cors_origin_allowed


class ProductionCorsOriginTests(unittest.TestCase):
    def test_allows_https_sites_and_exact_packaged_tauri_origins(self):
        for origin in (
            "https://app.example.com",
            "https://app.example.com:8443",
            "tauri://localhost",
            "http://tauri.localhost",
        ):
            with self.subTest(origin=origin):
                self.assertTrue(production_cors_origin_allowed(origin))

    def test_rejects_unlisted_http_and_lookalike_tauri_origins(self):
        for origin in (
            "http://app.example.com",
            "http://localhost:5173",
            "tauri://evil.localhost",
            "http://evil.tauri.localhost",
            "https://app.example.com/path",
            "https://user:pass@app.example.com",
            "https://*.example.com",
        ):
            with self.subTest(origin=origin):
                self.assertFalse(production_cors_origin_allowed(origin))


if __name__ == "__main__":
    unittest.main()
