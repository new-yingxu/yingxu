#!/usr/bin/env python3
"""本地网盘 mock 服务器（仅联调/验收用，不是真实厂商实现）。

实现与 `backend/core/src/netdisk.rs` 约定的最小协议：
  POST /v1/precreate                {"name","bytes","sha256","chunkSize"} -> {"uploadId"}
  POST /v1/part?uploadId=&index=     分片原文                        -> {"index"}
  POST /v1/complete                  {"uploadId","sha256","bytes"}    -> {"sha256","verified"}

环境变量：
  MOCK_FAIL_ONCE=<index>   该分片第一次返回 503，用于验证断点续传
  MOCK_PORT=<port>         0 表示随机端口，启动后打印 `PORT=<n>`
"""
import hashlib
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LOCK = threading.Lock()
UPLOADS = {}
FAIL_ONCE = {int(x) for x in os.environ.get("MOCK_FAIL_ONCE", "").split(",") if x.strip()}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args):
        pass

    def _send(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        path, _, query = self.path.partition("?")
        params = dict(
            (k, v) for k, v in (p.split("=", 1) for p in query.split("&") if "=" in p)
        )
        if path == "/v1/precreate":
            upload_id = "up-%d" % (len(UPLOADS) + 1)
            with LOCK:
                UPLOADS[upload_id] = {"parts": {}, "chunkSize": json.loads(body).get("chunkSize")}
            return self._send(200, {"uploadId": upload_id})
        if path == "/v1/part":
            upload_id = params.get("uploadId", "")
            index = int(params.get("index", "0"))
            with LOCK:
                if index in FAIL_ONCE:
                    FAIL_ONCE.discard(index)
                    return self._send(503, {"retry": True})
                upload = UPLOADS.setdefault(upload_id, {"parts": {}})
                upload["parts"][index] = body
            return self._send(200, {"index": index})
        if path == "/v1/complete":
            info = json.loads(body or b"{}")
            with LOCK:
                upload = UPLOADS.get(info.get("uploadId"), {"parts": {}})
                joined = b"".join(upload["parts"][k] for k in sorted(upload["parts"]))
            digest = hashlib.sha256(joined).hexdigest()
            ok = digest == info.get("sha256")
            return self._send(
                200 if ok else 409,
                {"sha256": digest, "verified": ok, "receivedChunks": len(upload["parts"])},
            )
        return self._send(404, {"error": "unknown endpoint"})


if __name__ == "__main__":
    port = int(os.environ.get("MOCK_PORT", "0"))
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print("PORT=%d" % server.server_address[1], flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        sys.exit(0)
