"""Tiny mock OpenAI-compatible upstream for smoke-testing transmymodels.

Serves on http://127.0.0.1:9099/v1:
  GET  /models                 -> {"data":[{"id":"mock-1"},{"id":"mock-2"}]}
  POST /chat/completions       -> echoes the upstream model it received
  POST /chat/completions (stream=true) -> 3 SSE chunks
"""
import json
from http.server import BaseHTTPRequestHandler, HTTPServer


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, obj, ctype="application/json"):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/v1/models":
            self._send(200, {
                "object": "list",
                "data": [
                    {"id": "mock-1", "object": "model"},
                    {"id": "mock-2", "object": "model"},
                ],
            })
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        length = int(self.headers.get("content-length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw or b"{}")
        except Exception:
            body = {}

        auth = self.headers.get("authorization", "")
        upstream_model = body.get("model")

        if self.path == "/v1/chat/completions":
            if body.get("stream"):
                # SSE: 3 chunks then [DONE]
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                for i, tok in enumerate(["hello", " from", " mock"]):
                    chunk = {
                        "id": "chatcmpl-mock",
                        "object": "chat.completion.chunk",
                        "choices": [{"delta": {"content": tok}, "index": 0}],
                        "model": upstream_model,
                    }
                    self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
                self.wfile.write(b"data: [DONE]\n\n")
                return
            self._send(200, {
                "id": "chatcmpl-mock",
                "object": "chat.completion",
                "model": upstream_model,
                "auth_ok": auth == "Bearer sk-mock-upstream",
                "choices": [{"message": {"role": "assistant", "content": f"echo:{upstream_model}"}}],
            })
        else:
            self._send(404, {"error": "not found"})

    def log_message(self, *a):  # silence
        pass


if __name__ == "__main__":
    HTTPServer(("127.0.0.1", 9099), Handler).serve_forever()
