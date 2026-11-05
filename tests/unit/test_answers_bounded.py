"""No tool answer a model reads is over answers.BOUND characters (T26; bootstrap run 1 spooled a
34 KB answer of guide sections to a file the Supervisor then parsed). A guide comes back as its
contents and one section at a time; a list comes back as its count and its first ones; whatever is
still over the bound is cut with a line saying how to read the rest (a read tool's ``page``)."""

import json
import subprocess
import time

import pytest
from conftest import agents_project
from orchestra.metrics import Point, for_project

from orchestra_agents import access, guides, installed, tooling
from orchestra_agents.answers import BOUND, cut, first
from orchestra_agents.builtins import ROLES
from orchestra_agents.events import Events
from orchestra_agents.harness import caller as caller_mod
from orchestra_agents.harness.tools import PAGE, PAGED, TOOLS
from orchestra_agents.ledger import CHANGES
from orchestra_agents.store import AgentStore

SERIES, COLLECTORS, TYPES = 3000, 300, 400


def git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True).stdout


def big_guide() -> str:
    """A guide of about 43 KB: an opening, a section of about 27 KB with subsections, and short ones."""
    para = ("Each line of this paragraph says something an operator needs, at some length, so the section grows. " * 4 + "\n") * 6
    out = ["# The big guide\n\nWhat this guide is about, in one paragraph.\n", "## Opening\n\n" + para]
    out.append("## Instruments and machines\n\n" + para + "".join(f"### Part {i}\n\n{para * 2}" for i in range(5)))
    out += [f"## Short {i}\n\n{para}" for i in range(6)]
    return "\n".join(out)


@pytest.fixture(autouse=True)
def _the_bound_itself(monkeypatch):
    """These are the bound's tests: a session that trims its answers further (it holds read_answer,
    answer_store.py) is test_answer_trimming's."""
    monkeypatch.setattr(caller_mod.Caller, "trims", property(lambda self: False))


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(installed, "tasking", lambda: False)
    repo = tmp_path / "repo"
    repo.mkdir()
    for args in (["init", "-q", "-b", "main"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        git(repo, *args)
    p = agents_project(repo)
    toml = repo / "orchestra.toml"
    toml.write_text(toml.read_text() + "".join(
        f'\n[[collect]]\nname = "probe_{i}"\ncommand = "machines/probe_{i}.sh --a-long-flag {"x" * 40}"\nevery = "30s"\n'
        for i in range(COLLECTORS)))
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "a fat project")
    p.reload_config()
    now = time.time()
    for_project(p.orchestra).write([Point(f"queue_{i % 600}_items", float(i), now - 5,
                                          {"project": p.key, "machine": f"m{i % 50}", "shard": f"s{i}"})
                                    for i in range(SERIES)])
    logs = p.orchestra.logs_dir / "machines"
    logs.mkdir(parents=True, exist_ok=True)
    (logs / "feed.log").write_text("".join(f"line {i} " + "y" * 2000 + "\n" for i in range(500)))
    guides._CACHE.clear()
    guides._CACHE[p.key] = [{"name": "big", "text": big_guide()}, *guides._own()]
    return p


@pytest.fixture
def store(project):
    s = AgentStore(project, Events.for_project(project, source="supervisor"))
    for i in range(TYPES):
        s.events.publish(f"thing.happened_{i}", {"n": i}, source=f"machine_{i % 30}")
    return s


@pytest.fixture
def caller(project, store, monkeypatch):
    monkeypatch.setattr(access, "identify", lambda st, env: ("supervisor", ""))
    c = caller_mod.Caller({}, list(ROLES["supervisor"].tools))
    c._project, c._store = project, store
    return c


def test_a_guide_larger_than_the_bound_comes_back_as_its_contents_and_opening(caller):
    assert len(big_guide()) > BOUND
    out = json.loads(caller.answer("read_guide", {"name": "big"}))
    assert out["chars"] == len(big_guide()) and "Short 5" not in out["text"]
    assert out["text"].startswith("# The big guide") and "## Opening" in out["text"] and "## Instruments" not in out["text"]
    assert any(c.startswith("Instruments and machines (") for c in out["contents"])
    assert any(c.startswith("  Part 0 (") for c in out["contents"])                  # a ### indented, with its size
    listed = json.loads(caller.answer("list_guides", {}))["guides"]
    big = next(g for g in listed if g["name"] == "big")
    assert big["chars"] == len(big_guide()) and big["sections"] == 13


def test_a_section_comes_back_whole_under_the_bound_and_in_pieces_over_it(caller):
    short = json.loads(caller.answer("read_guide", {"name": "big", "section": "Short 2"}))
    assert short["text"].startswith("## Short 2") and "more" not in short and "## Short 3" not in short["text"]
    raw = caller.answer("read_guide", {"name": "big", "section": "Instruments and machines"})
    assert len(raw) <= BOUND and "answer cut" not in raw
    long = json.loads(raw)
    assert "read_guide with name 'big', section 'Instruments and machines' and offset" in long["more"]
    assert "Part 3 (" in long["more"]                                                 # its subsections, to ask for
    offset = int(long["more"].split("offset ")[1].split(",")[0])
    rest = json.loads(caller.answer("read_guide", {"name": "big", "section": "Instruments and machines", "offset": offset}))
    whole = guides._text({"name": "big", "text": big_guide()})
    start = whole.index("## Instruments and machines")
    assert rest["text"] and (long["text"] + rest["text"]) in whole[start:]


def test_check_setup_on_a_project_with_thousands_of_series_answers_under_the_bound(caller):
    raw = caller.answer("check_setup", {})
    assert len(raw) <= BOUND and "answer cut" not in raw
    out = json.loads(raw)
    assert out["collectors_count"] == COLLECTORS and out["collectors"][-1].startswith(f"… and {COLLECTORS - 40} more")
    metrics = json.loads(caller.answer("list_metrics", {}))
    assert metrics["names"] == 600 and metrics["series"] == SERIES and metrics["metrics"][-1].startswith("… and 550 more")


def test_every_read_tool_on_a_fat_project_answers_under_the_bound(caller):
    """Each tool that only reads, called for real on the fat project with what it needs at least."""
    need = {"read_guide": {"name": "big"}, "read_machine_log": {"name": "feed", "lines": 400},
            "read_instrument": {"name": "probe_1"}, "explain_item": {"item": "x"}, "get_role": {"name": "supervisor"},
            "get_agent": {"name": "supervisor"}, "get_team": {"name": "x"}, "read_transcript": {"agent": "supervisor"}}
    sizes = {}
    for t in caller.tools:
        if t.name not in PAGED:
            continue
        try:
            got = caller.answer(t.name, {k: v for k, v in need.get(t.name, {}).items() if k in t.properties})
        except Exception as e:  # noqa: BLE001 - a refusal is an answer too, bounded the same way
            got = caller_mod.error(e)
        sizes[t.name] = len(got)
    assert sizes and max(sizes.values()) <= BOUND, {k: v for k, v in sizes.items() if v > BOUND}
    assert {"check_setup", "list_metrics", "read_events", "list_processes", "read_machine_log", "read_guide"} <= set(sizes)


def test_no_tool_in_the_registry_can_return_more_than_the_bound(monkeypatch, tmp_path):
    """Every tool, its handler made to answer 200,000 characters: the caller cuts each; a read tool's
    pages give the rest, in order; a tool that changes something says its answer is not paged."""
    huge = {"ok": True, "rows": [f"row {i}: " + "z" * 90 for i in range(2000)]}
    monkeypatch.setattr(tooling, "handle", lambda *a, **kw: huge)
    monkeypatch.setattr(tooling, "check_args", lambda name, args: None)
    monkeypatch.setattr(access, "identify", lambda st, env: ("supervisor", ""))
    c = caller_mod.Caller({}, None)
    c._project = c._store = object()
    full = json.dumps(huge, indent=1)
    for t in TOOLS:
        got = c.answer(t.name, {})
        assert len(got) <= BOUND, t.name
        if t.name in PAGED:
            assert f"call {t.name} again with the same arguments and page: 2" in got
        else:
            assert "not paged" in got and PAGE not in t.properties
    pieces, page = [], 1
    while True:
        got = c.answer("list_metrics", {"page": page})
        body, note = got.rsplit("\n[answer cut", 1)
        pieces.append(body)
        if "the last" in note:
            break
        page += 1
    assert "".join(pieces) == full and page == len(full) // BOUND + 1
    with pytest.raises(ValueError, match="no page 99"):
        c.answer("list_metrics", {"page": 99})


def test_the_paged_tools_only_read():
    names = {t.name for t in TOOLS}
    assert PAGED <= names and not PAGED & CHANGES
    assert all((PAGE in t.properties) == (t.name in PAGED) for t in TOOLS)


def test_cut_and_first():
    assert cut("short", "x") == "short"
    assert first(range(5), 3, "ask") == [0, 1, 2, "… and 2 more (ask)"] and first([1], 3) == [1]
