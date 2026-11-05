"""Local control endpoint served by `orchestra agents run`.

`orchestra agents chat` attaches here to talk to the Supervisor session that the host keeps
resident, so there is exactly one session per project agent. Bound to 127.0.0.1 only, and the
chat asks for the control token (``~/.orchestrator/<key>/agents/control.token``, owner-only, made
the first time it is asked for): a page of any site the owner visits could otherwise post to a port
on 127.0.0.1 and talk to the Supervisor, around Orchestra's web login. The web plugin and
``orchestra agents chat`` read the file and send ``Authorization: Bearer <token>``.

POST /supervisor  body {"message": "..."}  -> NDJSON stream of the session's normalized events (harness/base.py;
                                              only tool_start and assistant_end are forwarded, host.py
                                              prompt_supervisor), then {"type": "done"}
                  401 without the token
POST /delegate    body {"job", "brief"}    -> NDJSON: started, tool, wait lines, then {"type": "done", "result"|"error"}:
                  a session's ``delegate`` run in the host, from its environment (delegate_host.py). Its
                  bearer is the calling session's own token, never the control token: the host checks
                  the token's grant, so a delegated job runs only for a live Supervisor session
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
DelegateFn = Callable[[str, dict, Callable[[dict], None]], dict]     # (the session's token, the body, emit) -> the answer


def token_file(project) -> Path:
    return project.state_dir / "control.token"


def bearer(project) -> dict[str, str]:
    """The header the chat's clients send: the control token, made the first time it is asked for."""
    return {"Authorization": f"Bearer {owner_only(token_file(project))}"}


class ControlServer:
    def __init__(self, port: int, prompt_supervisor: PromptFn, token: str, delegate: DelegateFn | None = None):
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
                if self.path == "/delegate" and delegate is not None:
                    return self._delegate()
                if self.path != "/supervisor":
                    return self._json(404, {"ok": False})
                given = (self.headers.get("Authorization") or "").encode()
                if not hmac.compare_digest(given, f"Bearer {token}".encode()):
                    return self._json(401, {"ok": False, "error": "send the control token (agents/control.token)"})
                body = self._body()
                if body is None:
                    return self._json(400, {"ok": False, "error": "bad json"})
                self._stream(lambda emit: outer.prompt_supervisor(body.get("message", ""), emit))

            def _body(self) -> dict | None:
                n = int(self.headers.get("Content-Length") or 0)
                try:
                    body = json.loads(self.rfile.read(n) or b"{}")
                except json.JSONDecodeError:
                    return None
                return body if isinstance(body, dict) else None

            def _delegate(self):
                given = self.headers.get("Authorization") or ""
                session = given.removeprefix("Bearer ").strip() if given.startswith("Bearer ") else ""
                body = self._body()
                if body is None:
                    return self._json(400, {"ok": False, "error": "bad json"})
                self._stream(lambda emit: {"result": delegate(session, body, emit)})

            def _stream(self, run):
                self.send_response(200)
                self.send_header("Content-Type", "application/x-ndjson")
                self.end_headers()
                lock = threading.Lock()

                def emit(ev: dict) -> None:
                    with lock:
                        try:
                            self.wfile.write((json.dumps(ev, default=str) + "\n").encode())
                            self.wfile.flush()
                        except (BrokenPipeError, ConnectionResetError):
                            pass

                try:
                    result = run(emit)
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
