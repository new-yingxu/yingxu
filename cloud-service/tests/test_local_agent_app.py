"""HTTP contract tests for the desktop-only LangGraph sidecar; provider is mocked."""
from pathlib import Path
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import Settings
from app.local_agent_app import LOCAL_OWNER, _local_settings, create_local_agent_app
from app.model_provider import credential_for_owner


class LocalAgentApp(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.token = "local-sidecar-test-token-" + "x" * 40
        self.settings = Settings(
            app_env="development", database_url="sqlite:///" + self.temp.name + "/agent.sqlite",
            auth_mode="disabled", dev_auth_token=None, jwt_issuer=None, jwt_audience=None,
            jwt_jwks_url=None, cors_origins=(), app_version="test",
        )
        self.client_env = patch.dict(os.environ, {"YINGXU_LOCAL_AGENT_MODE": "1"})
        self.client_env.start()
        self.app = create_local_agent_app(settings=self.settings, token=self.token)
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.headers = {"Authorization": "Bearer " + self.token}
        self.turn = {
            "query": "查找最近的照片",
            "sessionId": "session-local-1",
            "turnId": "turn-local-1",
            "useModel": True,
            "capabilities": ["search_media", "get_metadata", "get_backup_job", "open_item"],
            "destinationGrantIds": [],
        }

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.client_env.stop()
        self.temp.cleanup()

    def test_bearer_is_required_and_only_local_identity_is_accepted(self):
        response = self.client.post("/v1/agent/local-turns", json=self.turn)
        self.assertEqual(response.status_code, 401, response.text)

    def test_host_shutdown_requires_runtime_bearer_and_requests_graceful_stop(self):
        from unittest.mock import Mock
        request_shutdown = Mock()
        self.app.state.request_shutdown = request_shutdown
        unauthorized = self.client.post("/_host/shutdown")
        self.assertEqual(unauthorized.status_code, 401)
        stopped = self.client.post("/_host/shutdown", headers=self.headers)
        self.assertEqual(stopped.status_code, 200, stopped.text)
        self.assertEqual(stopped.json(), {"stopping": True})
        request_shutdown.assert_called_once_with()

    def test_local_status_is_authenticated_and_never_offers_cloud_fallback(self):
        unauthorized = self.client.get("/v1/agent/local-status")
        self.assertEqual(unauthorized.status_code, 401)
        with patch("app.model_provider.local_ollama_model_state", return_value="ready") as probe:
            response = self.client.get("/v1/agent/local-status", headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {
            "configured": True, "provider": "ollama", "model": "qwen2.5:7b", "region": "local",
            "availability": "ready", "requiresExplicitConsent": True,
            "executionAuthorized": False, "cloudFallback": False,
        })
        probe.assert_called_once()
        with patch("app.model_provider.local_ollama_model_state", return_value="model_not_installed"):
            missing = self.client.get("/v1/agent/local-status", headers=self.headers)
        self.assertFalse(missing.json()["configured"])
        self.assertEqual(missing.json()["availability"], "model_not_installed")

    def test_local_model_catalog_is_authenticated_bounded_and_pathless(self):
        unauthorized = self.client.get("/v1/agent/local-models")
        self.assertEqual(unauthorized.status_code, 401)
        with patch("app.model_provider.local_ollama_models", return_value=["qwen2.5:7b", "llama3.1:8b"]) as probe:
            response = self.client.get("/v1/agent/local-models", headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {"available": True, "models": ["qwen2.5:7b", "llama3.1:8b"]})
        probe.assert_called_once_with()
        with patch("app.model_provider.local_ollama_models", return_value=None):
            unavailable = self.client.get("/v1/agent/local-models", headers=self.headers)
        self.assertEqual(unavailable.json(), {"available": False, "models": []})

    def test_desktop_byok_status_is_ready_without_ollama_or_cloud_account(self):
        from app.local_agent_app import _local_settings
        env = {
            "YINGXU_LOCAL_AGENT_MODE": "1",
            "YINGXU_LOCAL_AGENT_PROVIDER": "deepseek",
            "YINGXU_LOCAL_AGENT_MODEL": "deepseek-flash",
            "YINGXU_LOCAL_AGENT_PROVIDER_KEY": "synthetic-provider-key",
        }
        with patch.dict(os.environ, env):
            resolved, _ = _local_settings(self.settings, self.token)
            credential = credential_for_owner(resolved.model_owner_credentials, LOCAL_OWNER, allow_local=True)
            self.assertEqual((credential.provider, credential.model, credential.region), ("deepseek", "deepseek-flash", "global"))
            self.assertIsNone(resolved.cloudbase_env_id)
            self.assertIsNone(resolved.cloudbase_api_key)
            self.assertNotIn("synthetic-provider-key", repr(credential))
            app = create_local_agent_app(settings=self.settings, token=self.token)
            with patch("app.model_provider.local_ollama_model_state", side_effect=AssertionError("must not probe Ollama")):
                with TestClient(app) as client:
                    response = client.get("/v1/agent/local-status", headers={"Authorization": "Bearer " + self.token})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {
            "configured": True, "provider": "deepseek", "model": "deepseek-flash", "region": "global",
            "availability": "configured", "requiresExplicitConsent": True,
            "executionAuthorized": False, "cloudFallback": False,
        })

    def test_local_model_turn_replay_does_not_call_provider_twice(self):
        model = {"choices": [{"message": {"content": "我会先在本机影像索引中查找。"}}],
                 "usage": {"prompt_tokens": 4, "completion_tokens": 5, "total_tokens": 9}}
        with patch("app.model_provider._tool_provider_request", return_value=model) as provider:
            first = self.client.post("/v1/agent/local-turns", json=self.turn, headers=self.headers)
            self.assertEqual(first.status_code, 200, first.text)
            self.assertEqual(first.json()["status"], "completed")
            self.assertEqual(first.json()["response"]["answer"], "我会先在本机影像索引中查找。")
            self.assertFalse(first.json()["executionAuthorized"])
            duplicate = self.client.post("/v1/agent/local-turns", json=self.turn, headers=self.headers)
            self.assertEqual(duplicate.status_code, 200, duplicate.text)
            self.assertTrue(duplicate.json()["duplicate"])
            self.assertEqual(provider.call_count, 1)

    def test_unsupported_write_tool_is_refused_without_creating_a_proposal(self):
        response = {"choices": [{"message": {"tool_calls": [{
            "id": "rename-call-1", "type": "function",
            "function": {"name": "rename_item", "arguments": '{"fileIds":["media-1"],"requestedName":"renamed.jpg"}'},
        }]}}], "usage": {"total_tokens": 2}}
        with patch("app.model_provider._tool_provider_request", return_value=response):
            result = self.client.post("/v1/agent/local-turns", json=self.turn, headers=self.headers)
        self.assertEqual(result.status_code, 200, result.text)
        payload = result.json()
        self.assertEqual(payload["status"], "completed")
        self.assertEqual(payload["response"]["code"], "LOCAL_TOOL_NOT_ALLOWED")
        self.assertFalse(payload["executionAuthorized"])
        self.assertFalse(payload["filesChanged"])
        self.assertNotIn("proposal", payload)

    def test_typed_local_tool_result_is_consumed_and_duplicate_resume_is_free(self):
        seen = []
        def model(_credential, messages, **_kwargs):
            seen.append(messages)
            if len(seen) == 1:
                return {"choices": [{"message": {"tool_calls": [{
                    "id": "search-call-1", "type": "function",
                    "function": {"name": "search_media", "arguments": json.dumps({"query": "IMG_", "limit": 10})},
                }]}}], "usage": {"total_tokens": 2}}
            tool_message = messages[-1]
            self.assertEqual(tool_message["tool_call_id"], "search-call-1")
            self.assertNotIn("/private/photos", str(messages))
            return {"choices": [{"message": {"content": "本机索引返回了一张照片。"}}],
                    "usage": {"total_tokens": 3}}

        result = {
            "continuationToken": "placeholder",
            "useModel": True,
            "results": [{"callId": "search-call-1", "tool": "search_media", "status": "succeeded",
                         "data": {"matches": [{"mediaId": "media-1", "name": "IMG_0001.jpg", "kind": "photo",
                                                "bytes": 5, "qualityState": "unanalyzed"}], "truncated": False}}],
        }
        with patch("app.model_provider._tool_provider_request", side_effect=model) as provider:
            first = self.client.post("/v1/agent/local-turns", json=self.turn, headers=self.headers)
            self.assertEqual(first.status_code, 200, first.text)
            self.assertEqual(first.json()["status"], "awaiting_local_tools")
            call = first.json()["localToolRequests"][0]
            self.assertEqual(call["tool"], "search_media")
            self.assertFalse(call["executionAuthorized"])
            result["continuationToken"] = first.json()["continuationToken"]
            route = "/v1/agent/local-turns/session-local-1/turn-local-1/results"
            completed = self.client.post(route, json=result, headers=self.headers)
            self.assertEqual(completed.status_code, 200, completed.text)
            self.assertEqual(completed.json()["status"], "completed")
            self.assertEqual(completed.json()["response"]["answer"], "本机索引返回了一张照片。")
            self.assertEqual(provider.call_count, 2)
            duplicate = self.client.post(route, json=result, headers=self.headers)
            self.assertEqual(duplicate.status_code, 200, duplicate.text)
            self.assertTrue(duplicate.json()["duplicate"])
            self.assertEqual(provider.call_count, 2)

    def test_pending_tool_continuation_survives_sidecar_restart(self):
        private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pem = private.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode("ascii")
        env = {
            "YINGXU_LOCAL_AGENT_MODE": "1",
            "YINGXU_LOCAL_AGENT_DATABASE_URL": self.settings.database_url,
            "YINGXU_LOCAL_AGENT_SIGNING_KEY": pem,
            "YINGXU_LOCAL_AGENT_KEY_ID": "stable-restart-test-key",
        }
        model = {"choices": [{"message": {"tool_calls": [{
            "id": "restart-search-1", "type": "function",
            "function": {"name": "search_media", "arguments": '{"query":"IMG_0001","limit":10}'},
        }]}}], "usage": {"total_tokens": 2}}
        final = {"choices": [{"message": {"content": "重启后已读取本机工具结果。"}}], "usage": {"total_tokens": 3}}
        with patch.dict(os.environ, env):
            first_app = create_local_agent_app(settings=self.settings, token=self.token)
            with TestClient(first_app) as first_client, patch("app.model_provider._tool_provider_request", return_value=model) as provider:
                pending = first_client.post("/v1/agent/local-turns", json=self.turn, headers=self.headers)
                self.assertEqual(pending.status_code, 200, pending.text)
                self.assertEqual(pending.json()["status"], "awaiting_local_tools")
                continuation = {
                    "continuationToken": pending.json()["continuationToken"],
                    "useModel": True,
                    "results": [{"callId": "restart-search-1", "tool": "search_media", "status": "succeeded",
                        "data": {"matches": [{"mediaId": "media-restart-1", "name": "IMG_0001.jpg", "kind": "photo",
                            "bytes": 5, "qualityState": "unanalyzed"}], "truncated": False}}],
                }
                self.assertEqual(provider.call_count, 1)

            restarted_app = create_local_agent_app(settings=self.settings, token=self.token)
            with TestClient(restarted_app) as restarted_client, patch("app.model_provider._tool_provider_request", return_value=final) as provider:
                route = "/v1/agent/local-turns/session-local-1/turn-local-1/results"
                completed = restarted_client.post(route, json=continuation, headers=self.headers)
                self.assertEqual(completed.status_code, 200, completed.text)
                self.assertEqual(completed.json()["status"], "completed")
                self.assertEqual(completed.json()["response"]["answer"], "重启后已读取本机工具结果。")
                self.assertEqual(provider.call_count, 1)
                duplicate = restarted_client.post(route, json=continuation, headers=self.headers)
                self.assertEqual(duplicate.status_code, 200, duplicate.text)
                self.assertTrue(duplicate.json()["duplicate"])
                self.assertEqual(provider.call_count, 1)

    def test_requires_explicit_development_mode_and_long_host_token(self):
        with patch.dict(os.environ, {"YINGXU_LOCAL_AGENT_MODE": "0"}):
            with self.assertRaises(RuntimeError):
                from app.local_agent_app import _local_settings
                _local_settings(self.settings, self.token)
        with patch.dict(os.environ, {"YINGXU_LOCAL_AGENT_MODE": "1"}):
            with self.assertRaises(RuntimeError):
                from app.local_agent_app import _local_settings
                _local_settings(self.settings, "short")

    def test_packaged_sidecar_uses_only_local_sqlite_ollama_and_host_key(self):
        private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pem = private.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode("ascii")
        production = Settings(
            app_env="production", database_url="", auth_mode="cloudbase",
            dev_auth_token=None, jwt_issuer="https://cloud.invalid", jwt_audience="cloud",
            jwt_jwks_url="https://cloud.invalid/jwks", cors_origins=("https://app.invalid",),
            app_version="desktop", cloudbase_env_id="cloud-env", cloudbase_api_key="cloud-secret",
            model_owner_credentials=json.dumps({LOCAL_OWNER: {"provider": "dashscope", "apiKey": "cloud-model-secret"}}),
            session_private_key="cloud-signing-key", session_key_id="cloud-kid",
        )
        env = {
            "YINGXU_LOCAL_AGENT_MODE": "1",
            "YINGXU_LOCAL_AGENT_DATABASE_URL": "sqlite:///" + self.temp.name + "/packaged.sqlite",
            "YINGXU_LOCAL_AGENT_MODEL": "qwen2.5:7b",
            "YINGXU_LOCAL_AGENT_SIGNING_KEY": pem,
            "YINGXU_LOCAL_AGENT_KEY_ID": "desktop-key-1",
        }
        with patch.dict(os.environ, env):
            resolved, token = _local_settings(production, self.token)
            app = create_local_agent_app(settings=production, token=self.token)
            with TestClient(app) as packaged_client:
                health = packaged_client.get("/healthz")
        credential = credential_for_owner(resolved.model_owner_credentials, LOCAL_OWNER, allow_local=True)
        self.assertEqual(resolved.database_url, env["YINGXU_LOCAL_AGENT_DATABASE_URL"])
        self.assertEqual(resolved.app_env, "development")
        self.assertEqual(resolved.session_private_key, pem)
        self.assertEqual(resolved.session_key_id, "desktop-key-1")
        self.assertIsNone(resolved.cloudbase_env_id)
        self.assertIsNone(resolved.cloudbase_api_key)
        self.assertNotIn("cloud-model-secret", resolved.model_owner_credentials)
        self.assertEqual((credential.provider, credential.model), ("ollama", "qwen2.5:7b"))
        self.assertEqual(token, self.token)
        self.assertEqual(resolved.configuration_errors(), [])
        self.assertEqual(health.status_code, 200)
        self.assertEqual(health.json()["service"], "yingxu-local-agent")

    def test_packaged_sidecar_requires_stable_host_signing_key(self):
        production = Settings(
            app_env="production", database_url="sqlite:///" + self.temp.name + "/packaged.sqlite",
            auth_mode="disabled", dev_auth_token=None, jwt_issuer=None, jwt_audience=None,
            jwt_jwks_url=None, cors_origins=(), app_version="desktop",
        )
        with patch.dict(os.environ, {"YINGXU_LOCAL_AGENT_MODE": "1", "YINGXU_LOCAL_AGENT_SIGNING_KEY": ""}):
            with self.assertRaisesRegex(RuntimeError, "OS credential store"):
                _local_settings(production, self.token)


if __name__ == "__main__":
    unittest.main()
