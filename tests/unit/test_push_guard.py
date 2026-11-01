"""The block on pushing and pull-request writes (push_guard.py): who gets it, what it sets, and
that the commands it writes refuse the writes and pass the reads, against a real git and a local
bare remote."""

import os
import shutil
import subprocess
import tomllib

import pytest

from orchestra_agents import push_guard
from orchestra_agents.config import Config, ConfigError


def test_allow_push_setting():
    assert Config.from_raw(tomllib.loads("[agents]\nharness = 'dummy'\n")).allow_push == []
    assert Config.from_raw(tomllib.loads("[agents]\nharness = 'dummy'\nallow_push = ['reviewer']\n")).allow_push == ["reviewer"]
    for bad in ("'worker'", "[1]", "['']"):
        with pytest.raises(ConfigError, match="allow_push"):
            Config.from_raw(tomllib.loads(f"[agents]\nharness = 'dummy'\nallow_push = {bad}\n"))


def test_who_is_blocked(tmp_path):
    assert push_guard.blocked("worker", []) and push_guard.blocked("reviewer", []) and push_guard.blocked("coordinator", [])
    assert push_guard.blocked("po", ["worker"])                       # a custom agent too, unless named
    assert not push_guard.blocked("worker", ["worker"])
    assert not push_guard.blocked("supervisor", [])                   # it keeps what it had (SPEC §6)
    assert push_guard.env("worker", ["worker"], tmp_path / "bin") == {}
    assert not (tmp_path / "bin").exists()


def test_env_appends_to_the_hosts_git_config(tmp_path):
    e = push_guard.env("worker", [], tmp_path / "bin", {"PATH": "/usr/bin", "GIT_CONFIG_COUNT": "2"})
    assert e["GIT_CONFIG_COUNT"] == "3" and e["GIT_CONFIG_KEY_2"] == "url.orchestra-no-push::.pushInsteadOf"
    assert e["GIT_CONFIG_VALUE_2"] == "" and e["PATH"] == f"{tmp_path / 'bin'}{os.pathsep}/usr/bin"
    assert {p.name for p in (tmp_path / "bin").iterdir()} == {"git", "gh", "git-remote-orchestra-no-push"}


@pytest.fixture
def repo(tmp_path):
    git = shutil.which("git")
    run = lambda *a, cwd=tmp_path: subprocess.run([git, *a], cwd=cwd, check=True, capture_output=True)
    run("init", "-q", "--bare", "bare.git")
    run("init", "-q", "-b", "main", "w")
    w = tmp_path / "w"
    run("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "x", cwd=w)
    run("remote", "add", "origin", str(tmp_path / "bare.git"), cwd=w)
    run("push", "-q", "origin", "main", cwd=w)
    stub = tmp_path / "stub"
    stub.mkdir()
    (stub / "gh").write_text('#!/bin/sh\necho "gh read: $*"\n')
    (stub / "gh").chmod(0o755)
    path = os.pathsep.join([str(stub), os.path.dirname(git), "/usr/bin", "/bin"])
    env = {**os.environ, "PATH": path}
    env |= push_guard.env("worker", [], tmp_path / "bin", env)
    sh = lambda c: subprocess.run(["/bin/sh", "-c", c], check=False, cwd=w, env=env, capture_output=True, text=True)
    return sh, git, tmp_path / "bare.git"


@pytest.mark.parametrize("command", ["git push", "git push origin HEAD", "git -C . push origin main:other",
                                     "git push ../bare.git HEAD:refs/heads/other", "REAL push origin HEAD:refs/heads/other"])
def test_git_push_refused(repo, command):
    sh, git, bare = repo
    p = sh(command.replace("REAL", git))
    assert p.returncode != 0 and push_guard.PUSH_REFUSED in p.stderr
    refs = subprocess.run([git, "--git-dir", str(bare), "for-each-ref"], capture_output=True, text=True, check=True).stdout
    assert "other" not in refs


@pytest.mark.parametrize("command", ["gh pr merge 1", "gh pr create --fill", "gh pr edit 1 --add-label x", "gh pr comment 1 -b hi",
                                     "gh -R o/r pr merge 1", "gh api -X POST repos/o/r/pulls", "gh api repos/o/r/labels -f name=x",
                                     "gh repo delete o/r"])
def test_gh_writes_refused(repo, command):
    p = repo[0](command)
    assert p.returncode == 1 and "Orchestra refused 'gh " in p.stderr and push_guard.ADVICE in p.stderr and "gh read" not in p.stdout


@pytest.mark.parametrize("command", ["gh pr view 1", "gh pr list", "gh pr diff 1", "gh -R o/r issue view 2", "gh api repos/o/r/pulls/1",
                                     "gh --version"])
def test_reads_pass(repo, command):
    p = repo[0](command)
    assert p.returncode == 0 and p.stdout.startswith("gh read: ")


def test_git_reads_and_commits_pass(repo):
    sh = repo[0]
    assert sh("git fetch origin").returncode == 0
    assert sh("git -c user.email=t@t -c user.name=t commit -q --allow-empty -m y && git log --oneline | wc -l").stdout.strip() == "2"
