"""Opt-in real Ollama test for the desktop local-agent HTTP sidecar."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import Settings
from app.local_agent_app import create_local_agent_app
from app import model_provider


@unittest.skipUnless(os.getenv("YINGXU_RUN_OLLAMA_TESTS") == "1", "real local Ollama test is opt-in")
class LocalAgentOllama(unittest.TestCase):
    def test_real_model_selects_local_tool_consumes_result_and_replay_does_not_rebill(self):
        with tempfile.TemporaryDirectory() as temp:
            token = "local-ollama-test-token-" + "x" * 40
            settings = Settings(
                app_env="development", database_url="sqlite:///" + temp + "/agent.sqlite",
                auth_mode="disabled", dev_auth_token=None, jwt_issuer=None, jwt_audience=None,
                jwt_jwks_url=None, cors_origins=(), app_version="ollama-test",
            )
            request = {
                "query": "请只使用 search_media 一次，在本机照片索引里搜索文件名包含 IMG_2048 的照片。收到工具结果后直接总结，不再调用其他工具，也不要请求打开照片。",
                "sessionId": "local-ollama-session",
                "turnId": "local-ollama-turn",
                "useModel": True,
                "capabilities": ["search_media", "get_metadata", "get_backup_job", "open_item"],
                "destinationGrantIds": [],
            }
            result = {
                "continuationToken": "placeholder", "useModel": True,
                "results": [{"callId": "placeholder", "tool": "search_media", "status": "succeeded",
                             "data": {"matches": [{"mediaId": "fixture-media-2048", "name": "IMG_2048.jpg",
                                                    "kind": "photo", "bytes": 2048, "qualityState": "unanalyzed"}],
                                      "truncated": False}}],
            }
            with patch.dict(os.environ, {"YINGXU_LOCAL_AGENT_MODE": "1"}):
                client = TestClient(create_local_agent_app(settings=settings, token=token))
                with client:
                    with patch("app.model_provider._tool_provider_request", wraps=model_provider._tool_provider_request) as provider:
                        first = client.post("/v1/agent/local-turns", json=request,
                                            headers={"Authorization": "Bearer " + token})
                        self.assertEqual(first.status_code, 200, first.text)
                        self.assertEqual(first.json()["status"], "awaiting_local_tools")
                        call = first.json()["localToolRequests"][0]
                        self.assertEqual(call["tool"], "search_media")
                        self.assertLessEqual(len(first.json()["localToolRequests"]), 2)
                        path = "/v1/agent/local-turns/local-ollama-session/local-ollama-turn/results"
                        current = first.json()
                        last_result = None
                        completed = None
                        for _ in range(2):
                            results = []
                            for request_tool in current["localToolRequests"]:
                                if request_tool["tool"] == "search_media":
                                    typed = {"status": "succeeded", "data": {"matches": [{
                                        "mediaId": "fixture-media-2048", "name": "IMG_2048.jpg",
                                        "kind": "photo", "bytes": 2048, "qualityState": "unanalyzed",
                                    }], "truncated": False}}
                                else:
                                    # This real-model test never grants local open confirmation.
                                    typed = {"status": "declined" if request_tool["tool"] == "open_item" else "failed", "data": {}}
                                results.append({"callId": request_tool["callId"], "tool": request_tool["tool"], **typed})
                            last_result = {"continuationToken": current["continuationToken"], "useModel": True, "results": results}
                            completed = client.post(path, json=last_result, headers={"Authorization": "Bearer " + token})
                            self.assertEqual(completed.status_code, 200, completed.text)
                            current = completed.json()
                            if current["status"] == "completed":
                                break
                            self.assertEqual(current["status"], "awaiting_local_tools")
                        self.assertIsNotNone(completed)
                        self.assertEqual(current["status"], "completed")
                        self.assertLessEqual(provider.call_count, 3)
                        self.assertFalse(current["executionAuthorized"])
                        self.assertIn("fixture-media-2048", str(current["response"]["matches"]))
                        replay = client.post(path, json=last_result, headers={"Authorization": "Bearer " + token})
                        self.assertEqual(replay.status_code, 200, replay.text)
                        self.assertTrue(replay.json()["duplicate"])
                        self.assertLessEqual(provider.call_count, 3)


if __name__ == "__main__":
    unittest.main()
