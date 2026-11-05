"""A fake model server speaking the valve's three API shapes (``orchestra_agents/valve/apis.py``):
OpenAI's chat completions (``POST /v1/chat/completions``, a JSON-schema answer in the message's
content), Anthropic's messages (``POST /v1/messages``, a forced ``decide`` tool use) and a generic
classifier (``POST /classify``, ``{label, score}``). Each request takes the next scripted reply
(``script``): an answer ``{"label", "score", "reason"}``, ``{"status": 503}``, ``{"sleep": 2}`` (a
timeout) or ``{"raw": ...}`` (a body as given); when the script runs out, ``default``, which may be
a function of the request's inputs. It checks the token and records every request (path, headers,
body). Also a plain forward proxy: a request for an absolute URL is answered the same way."""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit


class FakeModel:
    def __init__(self, token: str = "sk-test-0123456789abcdef", default=None):
        self.token, self.default, self.script, self.requests = token, default, [], []
        self.lock = threading.Lock()
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                fake._serve(self)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def start(self) -> FakeModel:
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return self

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    def __enter__(self):
        return self.start()

    def __exit__(self, *a):
        self.stop()

    def _authorized(self, path: str, headers) -> bool:
        if path == "/v1/messages":
            return headers.get("x-api-key") == self.token
        return headers.get("Authorization") == f"Bearer {self.token}"

    def _next(self, inputs):
        with self.lock:
            if self.script:
                return self.script.pop(0)
        d = self.default
        return d(inputs) if callable(d) else (d or {"label": "queue", "score": 0.9, "reason": "a default answer"})

    def _serve(self, h: BaseHTTPRequestHandler) -> None:
        path = urlsplit(h.path).path
        body = json.loads(h.rfile.read(int(h.headers.get("Content-Length") or 0)) or b"{}")
        with self.lock:
            self.requests.append({"path": path, "headers": {k.lower(): v for k, v in h.headers.items()}, "body": body, "raw_path": h.path})
        if not self._authorized(path, h.headers):
            return self._send(h, 401, {"error": {"message": "invalid token"}})
        reply = self._next(_inputs(path, body))
        if "sleep" in reply:
            time.sleep(reply["sleep"])
            return self._send(h, 200, _answer(path, {"label": "queue", "score": 0.9, "reason": "late"}))
        if "status" in reply:
            return self._send(h, reply["status"], {"error": {"message": "overloaded"}})
        return self._send(h, 200, reply["raw"] if "raw" in reply else _answer(path, reply))

    @staticmethod
    def _send(h, status: int, body: dict) -> None:
        data = json.dumps(body).encode()
        try:
            h.send_response(status)
            h.send_header("Content-Type", "application/json")
            h.send_header("Content-Length", str(len(data)))
            h.end_headers()
            h.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass


def _inputs(path: str, body: dict):
    """The inputs the valve sent, read back from the request as each shape carries them."""
    if path == "/classify":
        return body.get("inputs")
    text = body["messages"][-1]["content"]
    return json.loads(text.split("Inputs (JSON):\n", 1)[1])


def _answer(path: str, a: dict) -> dict:
    if path == "/v1/chat/completions":
        return {"choices": [{"message": {"role": "assistant", "content": json.dumps(a)}}],
                "usage": {"prompt_tokens": 120, "completion_tokens": 20}}
    if path == "/v1/messages":
        return {"content": [{"type": "tool_use", "id": "t1", "name": "decide", "input": a}],
                "usage": {"input_tokens": 120, "output_tokens": 20}}
    return {"label": a.get("label"), "score": a.get("score"), "reason": a.get("reason"), "cost": 0.0001}
