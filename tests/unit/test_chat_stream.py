"""The Supervisor chat stream stays alive through proxies: pings go out while nothing
arrives, and the client's lines are passed through unchanged."""

import json
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from conftest import agents_project
from fastapi import FastAPI
from fastapi.testclient import TestClient

from orchestra_agents import web
from orchestra_agents.store import AgentStore


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path / "home"))
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    return agents_project(repo, provider="p", model="m")


class SlowControl(BaseHTTPRequestHandler):
    """A control endpoint whose Supervisor thinks for a while before saying anything."""
    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.end_headers()
        time.sleep(0.7)
        self.wfile.write(b'{"type": "assistant_end", "text": "done thinking"}\n')
        self.wfile.write(b'{"type": "done", "error": null, "timed_out": false}\n')
        self.wfile.flush()
    def log_message(self, *a):
        pass


def test_chat_stream_pings_while_the_supervisor_is_silent(project, monkeypatch):
    srv = HTTPServer(("127.0.0.1", 0), SlowControl)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    store = AgentStore(project)
    store.set_control(srv.server_port, 1)
    monkeypatch.setattr(web, "CHAT_PING_SECONDS", 0.2)
    app = FastAPI()
    app.include_router(web.router(project), prefix="/api/plugins/agents")
    client = TestClient(app)
    with client.stream("POST", "/api/plugins/agents/supervisor", json={"message": "hello"}) as r:
        assert r.status_code == 200
        lines = [json.loads(ln) for ln in r.iter_lines() if ln.strip()]
    kinds = [ln["type"] for ln in lines]
    assert kinds.count("ping") >= 2                                   # silence was bridged
    assert kinds[-2:] == ["assistant_end", "done"]                    # and the real lines came through in order
    srv.shutdown()


def test_the_control_endpoint_talks_to_the_supervisor_only_with_the_control_token(project):
    """A page of any site could post to a port on 127.0.0.1 (a text/plain POST needs no preflight):
    without the control token, the Supervisor never hears it. The web plugin sends the token."""
    import urllib.error
    import urllib.request

    from orchestra_agents.control import ControlServer, bearer, token_file

    heard = []

    def prompt(message, emit):
        heard.append(message)
        emit({"type": "assistant_end", "text": "heard"})
        return {}

    tok = bearer(project)["Authorization"].removeprefix("Bearer ")
    assert (token_file(project).stat().st_mode & 0o777) == 0o600
    srv = ControlServer(0, prompt, tok)
    srv.start()
    try:
        url = f"http://127.0.0.1:{srv.port}/supervisor"
        for headers in ({"Content-Type": "text/plain"}, {"Content-Type": "application/json", "Authorization": "Bearer wrong"}):
            req = urllib.request.Request(url, data=b'{"message": "rotate every token"}', headers=headers, method="POST")
            with pytest.raises(urllib.error.HTTPError) as no:
                urllib.request.urlopen(req, timeout=5)
            assert no.value.code == 401
        assert heard == []

        AgentStore(project).set_control(srv.port, 1)
        app = FastAPI()
        app.include_router(web.router(project), prefix="/api/plugins/agents")
        with TestClient(app).stream("POST", "/api/plugins/agents/supervisor", json={"message": "hello"}) as r:
            lines = [json.loads(ln) for ln in r.iter_lines() if ln.strip() and json.loads(ln)["type"] != "ping"]
        assert heard == ["hello"] and [ln["type"] for ln in lines] == ["assistant_end", "done"]
    finally:
        srv.stop()
