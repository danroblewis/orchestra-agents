"""The Supervisor's tools for adding a measurement: the guides it reads first, a trial round of a
collector run as Orchestra would run it, a one-liner declared as configuration (sh -c), the
instrument read back with the points it stored, and another store looked at before it is declared."""

import json
import subprocess
import threading
import time
import tomllib
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from conftest import agents_project
from orchestra.metrics import Point
from orchestra.metrics import for_project as metrics_for

from orchestra_agents import guides, tooling
from orchestra_agents.events import Events
from orchestra_agents.store import AgentStore, StoreError

WHY = "the owner wants to see how many TODO comments the code has"


def git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True).stdout


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path / "home"))
    repo = tmp_path / "repo"
    repo.mkdir()
    for args in (["init", "-q", "-b", "main"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        git(repo, *args)
    (repo / "app.py").write_text("# TODO: one\nx = 1  # TODO two\n")
    (repo / "lib.py").write_text("# TODO three\n")
    p = agents_project(repo)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "init")
    p.reload_config()
    guides._CACHE.clear()
    return p


@pytest.fixture
def store(project):
    return AgentStore(project, Events.for_project(project, source="supervisor"))


def call(project, store, tool, role="supervisor", **args):
    tooling.check_args(tool, args)
    return tooling.handle(project, store, tool, args, role=role)


TODOS = "echo \"todo_comments $(git grep -I -c TODO -- . | awk -F: '{s+=$2} END {print s+0}')\""


def test_the_guides_are_listed_with_their_sections_and_read_whole_or_by_section(project, store):
    out = call(project, store, "list_guides")
    by = {g["name"]: g for g in out["guides"]}
    assert {"orchestra", "agents"} <= set(by)
    assert by["orchestra"]["about"] and "Naming series" in by["orchestra"]["sections"]
    assert "What the human decides" in by["agents"]["sections"] and by["agents"]["chars"] > 2000
    whole = call(project, store, "read_guide", name="agents")["text"]
    assert whole.startswith("# Configuring the agents")
    part = call(project, store, "read_guide", name="orchestra", section="naming series")
    assert part["section"] == "Naming series" and part["text"].startswith("## Naming series") and "## Pitfalls" not in part["text"]
    with pytest.raises(StoreError, match="no section"):
        call(project, store, "read_guide", name="orchestra", section="no such heading")
    with pytest.raises(StoreError, match="no guide"):
        call(project, store, "read_guide", name="nope")
    assert call(project, store, "list_guides", role="coordinator")["guides"]             # the Coordinator reads them too
    with pytest.raises(StoreError, match="'guides' tool group"):
        call(project, store, "list_guides", role="worker")


def test_a_trial_round_says_what_would_be_stored_and_what_is_wrong(project, store):
    out = call(project, store, "try_collector", command=TODOS, shell=True)
    assert out["works"] and out["series"] == [{"name": "todo_comments", "labels": {}, "value": 3.0}]
    assert out["would_run"].startswith("sh -c ")
    bad = call(project, store, "try_collector", command="echo 'orchestra_todos 3'; echo 'not a line'", shell=True)
    assert not bad["works"] and bad["refused"] and bad["bad_lines"]
    failed = call(project, store, "try_collector", command="exit 3", shell=True)
    assert not failed["works"] and "code 3" in failed["error"]
    slow = call(project, store, "try_collector", command="sleep 3", shell=True, timeout="1s")
    assert not slow["works"] and "timeout" in slow["error"]
    with pytest.raises(StoreError, match="a script, not a one-liner"):
        call(project, store, "try_collector", command="echo a\necho b", shell=True)
    with pytest.raises(StoreError, match="needs the 'setup' tool group"):
        call(project, store, "try_collector", role="coordinator", command="true")


def test_a_one_liner_is_declared_as_configuration_and_read_back_with_its_points(project, store):
    out = call(project, store, "set_collector", name="todos", command=TODOS, shell=True, every="60s", timeout="20s", reason=WHY)
    assert out["commit"] and "read_instrument todos" in out["applies"]
    [entry] = tomllib.loads((project.repo_root / "orchestra.toml").read_text())["collect"]
    assert entry["command"][:2] == ["sh", "-c"] and "git grep" in entry["command"][2] and entry["every"] == "60s"

    seen = call(project, store, "read_instrument", name="todos")
    assert seen["found"] and seen["series"] == [] and "no `orchestra run`" in seen["note"]
    store_ = metrics_for(project.orchestra)
    store_.write([Point("todo_comments", 3, time.time(), {"project": project.key, "instrument": "todos"})])
    store_.close()
    seen = call(project, store, "read_instrument", name="todos")
    assert [(s["name"], s["latest"]) for s in seen["series"]] == [("todo_comments", [3.0])]
    assert not call(project, store, "read_instrument", name="nope")["found"]


def test_set_collector_refuses_a_script_as_a_one_liner(project, store):
    with pytest.raises(StoreError, match="hand_to_coordinator"):
        call(project, store, "set_collector", name="big", command="x" * 400, shell=True, reason=WHY)


class FakeStore:
    """A store answering the Prometheus API's series, metadata, build info and range queries."""

    def __init__(self, dump):
        from orchestra.metrics import promql
        fake = self
        self.max_series = 0          # a listing of more series answers 422 (a per-query limit)
        self.busy = 0                # the next N listings answer 429

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _go(self, form):
                path = urllib.parse.urlsplit(self.path).path.rsplit("/", 1)[-1]
                one = lambda k, d=None: (form.get(k) or [d])[0]
                if path in ("series", "values"):
                    seen = {}
                    for m in form.get("match[]") or []:
                        for lab, _ in promql.select(dump, m, float(one("start", 0)), float(one("end", time.time()))):
                            seen.setdefault(tuple(sorted(lab.items())), lab)
                    data = list(seen.values())
                    if path == "values":
                        data = sorted({lab["__name__"] for lab in data})
                    elif fake.max_series and len(data) > fake.max_series:
                        return self._send(422, {"status": "error", "error": f"query matched {len(data)} series, over the limit"})
                    if fake.busy:
                        fake.busy -= 1
                        return self._send(429, {"status": "error", "error": "slow down"})
                elif path == "metadata":
                    data = {"http_requests_total": [{"type": "counter", "help": "Requests served.", "unit": ""}]}
                elif path == "buildinfo":
                    data = {"version": "fake-1"}
                elif path == "query_range":
                    res = promql.query_range(dump, one("query"), float(one("start")), float(one("end")), float(one("step")))
                    data = {"resultType": "matrix", "result": [{"metric": lab, "values": [[t, repr(v)] for t, v in pts]} for lab, pts in res]}
                elif path in ("query", "labels"):
                    data = {"resultType": "vector", "result": []} if path == "query" else ["__name__"]
                else:
                    return self._send(404, {"status": "error", "error": "not found"})
                self._send(200, {"status": "success", "data": data})

            def _send(self, code, body):
                raw = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self):
                self._go(urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query))

            def do_POST(self):
                self._go(urllib.parse.parse_qs(self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode()))

        fake.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        fake.url = f"http://127.0.0.1:{fake.server.server_address[1]}"
        threading.Thread(target=fake.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def remote():
    now = time.time()
    pts = lambda f: [(now - 600 + 15 * i, f(i)) for i in range(40)]
    dump = [("http_requests_total", {"service": "api", "pod": f"api-{k}"}, pts(lambda i, k=k: 10 * i + k)) for k in range(3)]
    dump += [("queue_depth", {"queue": "orders"}, pts(lambda i: 5 + i % 4))]
    s = FakeStore(dump)
    yield s
    s.close()


def test_another_store_is_looked_at_before_it_is_declared(project, store, remote):
    checked = call(project, store, "check_sources", url=remote.url)
    assert any("answers" in line for line in checked["lines"])
    whole = call(project, store, "source_schema", url=remote.url)
    assert "http_requests_total [counter] 3 series; pod=3, service=1" in whole["names"]          # one line a metric
    assert "queue_depth [guessed: gauge] 1 series; queue=1" in whole["names"]
    schema = call(project, store, "source_schema", url=remote.url, match=['{__name__=~".+"}'])
    by = {m["name"]: m for m in schema["names"]}
    assert by["http_requests_total"]["type"] == "counter" and by["http_requests_total"]["series"] == 3
    assert by["http_requests_total"]["labels"]["pod"]["values"] == 3
    assert by["queue_depth"]["type"].startswith("guessed: gauge")
    preview = call(project, store, "preview_query", url=remote.url, query="sum(rate(http_requests_total[1m]))", minutes=10, step_s=15)
    assert preview["lines"][0].startswith("1 series")
    inferred = call(project, store, "infer_source", url=remote.url)
    assert any("families" in line for line in inferred["lines"])
    assert "not declared yet" in checked["note"]
    entry = json.dumps([{"name": "shop", "url": remote.url, "machine": "service", "match": ['{__name__=~"http_.*|queue_.*"}']}])
    out = call(project, store, "set_setting", key="sources", value=entry, target="repo", reason="The shop's request and queue metrics.")
    assert out["commit"]
    assert call(project, store, "infer_source", source="shop")["lines"][0].startswith("shop:")
    seen = call(project, store, "read_instrument", name="shop")
    assert seen["instrument"]["kind"] == "source" and "no discovery has read it yet" in seen["note"]
    with pytest.raises(StoreError, match="no source 'work'"):
        call(project, store, "source_schema", source="work")
    with pytest.raises(StoreError, match="literal secret|user:pass"):
        call(project, store, "check_sources", url="http://me:pw@127.0.0.1:1")


def test_a_store_that_refuses_a_whole_listing_is_listed_name_by_name(project, store, remote, monkeypatch):
    from orchestra_agents import source_tools
    monkeypatch.setattr(source_tools.time, "sleep", lambda s: None)
    remote.max_series, remote.busy = 2, 1        # three request series: too many at once; one 429 on the way
    whole = call(project, store, "source_schema", url=remote.url)
    assert "queue_depth [guessed: gauge] 1 series; queue=1" in whole["names"]
    assert any(x.startswith("http_requests_total [counter]: the store refused") for x in whole["too_big_to_list"])
    narrow = call(project, store, "source_schema", url=remote.url, match=['{__name__=~".+",queue="orders"}'])
    assert [m["name"] for m in narrow["names"]] == ["queue_depth"]
    remote.max_series = 3
    whole = call(project, store, "source_schema", url=remote.url, match=['{service="api"}'])
    assert [m["name"] for m in whole["names"]] == ["http_requests_total"] and "too_big_to_list" not in whole


def test_a_token_in_a_file_is_declared_by_its_path_and_never_shown(project, store, remote, tmp_path):
    token = tmp_path / "secrets" / "token"
    token.parent.mkdir()
    token.write_text("tok-9f8e7d6c5b4a\n")
    auth = {"bearer_file": str(token)}
    seen = json.dumps(call(project, store, "check_sources", url=remote.url, auth=auth))
    assert "present" in seen and "tok-9f8e7d6c5b4a" not in seen
    entry = json.dumps([{"name": "shop", "url": remote.url, "auth": auth, "match": ['{__name__="queue_depth"}']}])
    assert call(project, store, "set_setting", key="sources", value=entry, target="repo", reason="The shop's queue.")["commit"]
    toml = (project.repo_root / "orchestra.toml").read_text()
    assert str(token) in toml and "tok-9f8e7d6c5b4a" not in toml
    assert "tok-9f8e7d6c5b4a" not in json.dumps(call(project, store, "read_config"))


def test_the_supervisor_connects_a_store_it_is_asked_to_without_waiting_for_a_go_ahead(project):
    from orchestra_agents.prompting import PROMPTS_DIR
    text = (PROMPTS_DIR / "operator.md").read_text()
    store_part = text[text.index("## Another time-series store"):text.index("## When the system cannot do what is asked")]
    assert "observation, not a change to the system" in store_part and "do not propose" in store_part
    assert "bearer_file" in store_part and "never for its value" in store_part
    assert "or size-limited store is no reason to stop or to ask" in store_part
    assert "rate-limited store)" not in store_part           # the old reason to ask first is gone


def test_an_orchestra_toml_git_does_not_track_yet_is_committed_with_the_first_change(project, store):
    """`orchestra init` writes orchestra.toml and commits nothing: the Supervisor's first change adds it."""
    git(project.repo_root, "rm", "-q", "--cached", "orchestra.toml")
    git(project.repo_root, "commit", "-qm", "orchestra.toml untracked, as orchestra init leaves it")
    out = call(project, store, "set_collector", name="todos", command=TODOS, shell=True, reason=WHY)
    assert out["commit"] and git(project.repo_root, "ls-files", "orchestra.toml").strip() == "orchestra.toml"
    assert not git(project.repo_root, "status", "--porcelain", "--", "orchestra.toml")
