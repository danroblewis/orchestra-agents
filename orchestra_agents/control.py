"""Local control endpoint served by `orchestra-agents run`.

`orchestra-agents chat` attaches here to talk to the Supervisor session that the host keeps
resident, so there is exactly one session per project agent. Bound to 127.0.0.1 only, and the
chat asks for the control token (``~/.orchestrator/<key>/agents/control.token``, owner-only, made
the first time it is asked for): a page of any site the owner visits could otherwise post to a port
on 127.0.0.1 and talk to the Supervisor, around Orchestra's web login. The web plugin and
``orchestra-agents chat`` read the file and send ``Authorization: Bearer <token>``.

POST /supervisor  body {"message": "..."}  -> NDJSON stream of the session's normalized events (harness/base.py;
                                              only tool_start and assistant_end are forwarded, host.py
                                              prompt_supervisor), then {"type": "done"}
                  401 without the token
GET  /health                                -> {"ok": true}
"""

from __future__ import annotations

import hmac
import json
import threading
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from orchestra.tokens import owner_only

PromptFn = Callable[[str, Callable[[dict], None]], dict]


def token_file(project) -> Path:
    return project.state_dir / "control.token"


def bearer(project) -> dict[str, str]:
    """The header the chat's clients send: the control token, made the first time it is asked for."""
    return {"Authorization": f"Bearer {owner_only(token_file(project))}"}


class ControlServer:
    def __init__(self, port: int, prompt_supervisor: PromptFn, token: str):
        self.port = port
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"

            def log_message(self, *a):  # quiet
                pass

            def _json(self, code: int, obj: dict) -> None:
                body = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.path == "/health":
                    return self._json(200, {"ok": True})
                self._json(404, {"ok": False})

            def do_POST(self):
                if self.path != "/supervisor":
                    return self._json(404, {"ok": False})
                given = (self.headers.get("Authorization") or "").encode()
                if not hmac.compare_digest(given, f"Bearer {token}".encode()):
                    return self._json(401, {"ok": False, "error": "send the control token (agents/control.token)"})
                n = int(self.headers.get("Content-Length") or 0)
                try:
                    body = json.loads(self.rfile.read(n) or b"{}")
                except json.JSONDecodeError:
                    return self._json(400, {"ok": False, "error": "bad json"})
                self.send_response(200)
                self.send_header("Content-Type", "application/x-ndjson")
                self.end_headers()
                lock = threading.Lock()

                def emit(ev: dict) -> None:
                    with lock:
                        try:
                            self.wfile.write((json.dumps(ev) + "\n").encode())
                            self.wfile.flush()
                        except (BrokenPipeError, ConnectionResetError):
                            pass

                try:
                    result = outer.prompt_supervisor(body.get("message", ""), emit)
                    emit({"type": "done", **result})
                except Exception as e:  # noqa: BLE001 - reported to the caller instead of dropping the connection
                    emit({"type": "done", "error": str(e)})

        self._server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self.port = self._server.server_address[1]          # port 0: the one the system chose
        self.prompt_supervisor = prompt_supervisor
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True, name="control")

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._server.shutdown()
