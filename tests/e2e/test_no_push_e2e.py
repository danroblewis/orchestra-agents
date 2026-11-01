"""End-to-end: a worker's session cannot push or write pull requests (push_guard.py).

The real task system and the host, with the fake Claude Code: the worker, after committing its
ticket, runs from its Bash tool what a model told to "open a PR" might: ``git push``, ``git push
origin HEAD``, the real git by its path (past the ``git`` on PATH, so git's own configuration
refuses it), ``gh pr merge 1``, ``gh pr create``, ``gh pr edit --add-label``; and the reads,
``git fetch`` and ``gh pr view``. The refusals, with what to do instead, are in its transcript;
the local bare remote got nothing; the reads worked; the ticket lands as usual."""

import json
import os
import shutil
import subprocess

from test_agents_e2e import agent_store, git, make_env

from orchestra_agents.harness import read_transcript
from orchestra_agents.push_guard import ADVICE


def test_worker_cannot_push_or_merge(tmp_path, monkeypatch):
    real_git = shutil.which("git")
    stub = tmp_path / "stub-bin"            # the gh the reads pass through to: no network, says what it was asked
    stub.mkdir()
    (stub / "gh").write_text('#!/bin/sh\necho "gh read: $*"\n')
    (stub / "gh").chmod(0o755)
    monkeypatch.setenv("PATH", f"{stub}{os.pathsep}{os.environ['PATH']}")
    commands = ["git push", "git push origin HEAD", f"{real_git} push origin HEAD:refs/heads/sneaky",
                "gh pr merge 1", "gh pr create --fill", "gh pr edit 1 --add-label ready",
                "git fetch origin && echo fetched", "gh pr view 1"]
    script = tmp_path / "shell.json"
    script.write_text(json.dumps(commands))
    monkeypatch.setenv("FAKE_CLAUDE_WORKER_SHELL", str(script))
    with make_env(tmp_path, monkeypatch, "claude_code") as env:
        bare = tmp_path / "remote.git"
        subprocess.run([real_git, "init", "-q", "--bare", str(bare)], check=True)
        git("remote", "add", "origin", str(bare), cwd=env.repo)
        git("push", "-q", "origin", "main", cwd=env.repo)            # the test's own push, outside any session
        t = env.create(title="Do the thing", description="x", acceptance="y")
        env.settle(t)
        assert env.state(t) == "DONE"
        msgs = read_transcript(agent_store(env).runtime()["archived_sessions"][f"worker-{t}"])
    results = [m["text"] for m in msgs if m["role"] == "toolResult"]
    out = dict(zip(commands, results[:len(commands)], strict=True))
    for c in commands[:3]:
        assert "Orchestra refused this push" in out[c] and ADVICE in out[c] and "(exit 0)" not in out[c], (c, out[c])
    for c, what in zip(commands[3:6], ("pr merge", "pr create", "pr edit"), strict=True):
        assert f"Orchestra refused 'gh {what}'" in out[c] and ADVICE in out[c] and "(exit 1)" in out[c], (c, out[c])
    assert "fetched" in out["git fetch origin && echo fetched"]
    assert "gh read: pr view 1" in out["gh pr view 1"] and "(exit 0)" in out["gh pr view 1"]
    heads = subprocess.run([real_git, "--git-dir", str(bare), "for-each-ref", "--format=%(refname)"],
                           capture_output=True, text=True, check=True).stdout.split()
    assert heads == ["refs/heads/main"]                              # nothing reached the remote
