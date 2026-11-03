"""Where a credential's file may live (credential_paths.py; bootstrap run 2, 2026-11-02): refused
only inside the project's own repository or when the repository enclosing it tracks it; allowed
under a parent repository that ignores it, or untracked there (with a caution), including a home
directory that is itself a repository."""

import subprocess

import pytest

from orchestra_agents import credential_paths, integrate_kits
from orchestra_agents.store import StoreError


def git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True).stdout


def repo_at(path):
    path.mkdir(parents=True, exist_ok=True)
    for args in (["init", "-q", "-b", "main"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        git(path, *args)
    return path


@pytest.fixture
def project_repo(tmp_path):
    return repo_at(tmp_path / "project")


def token(path, text="placeholder"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(0o600)
    return path


def test_a_file_inside_the_projects_own_repository_is_refused_even_when_ignored(project_repo):
    (project_repo / ".gitignore").write_text("secrets/\n")
    f = token(project_repo / "secrets" / "jira-token")
    with pytest.raises(StoreError, match=r"jira-token is refused: it is inside the project's own repository"):
        credential_paths.check(str(f), "token_file", project_repo)
    with pytest.raises(StoreError, match="inside the project's own repository"):      # not there yet: the same
        credential_paths.check(str(project_repo / "later" / "t"), "token_file", project_repo)


def test_a_file_outside_every_repository_is_allowed_without_a_caution(tmp_path, project_repo):
    f = token(tmp_path / "creds" / "jira-token")
    assert credential_paths.check(str(f), "token_file", project_repo) == (str(f), None)


def test_a_file_a_parent_repository_ignores_is_allowed(tmp_path, project_repo):
    run = repo_at(tmp_path / "harness")                       # a harness's run directory: a repository that ignores creds/
    (run / ".gitignore").write_text("creds/\n")
    f = token(run / "creds" / "jira-token")
    assert credential_paths.check(str(f), "token_file", project_repo) == (str(f), None)


def test_a_file_a_parent_repository_tracks_is_refused_naming_it(tmp_path, project_repo):
    other = repo_at(tmp_path / "dotfiles")
    f = token(other / "tokens" / "jira-token")
    git(other, "add", "-A")
    git(other, "commit", "-qm", "oops")
    with pytest.raises(StoreError, match=r"jira-token is refused: the repository at .*dotfiles tracks it"):
        credential_paths.check(str(f), "token_file", project_repo)


def test_a_home_directory_that_is_a_repository_allows_an_untracked_file_with_a_caution(tmp_path, monkeypatch, project_repo):
    home = repo_at(tmp_path / "home")                         # the owner's home directory is itself a repository
    token(home / ".zshrc", "# dotfiles\n")
    git(home, "add", ".zshrc")
    git(home, "commit", "-qm", "dotfiles")
    monkeypatch.setenv("HOME", str(home))
    f = token(home / ".config" / "orchestra-bridges" / "jira-token")
    path, caution = credential_paths.check("~/.config/orchestra-bridges/jira-token", "token_file", project_repo)
    assert path == "~/.config/orchestra-bridges/jira-token"
    assert caution and "not tracked" in caution and "nor ignored" in caution and str(f) in caution
    (home / ".gitignore").write_text(".config/orchestra-bridges/\n")    # ignored there: no caution
    assert credential_paths.check(str(f), "token_file", project_repo) == (str(f), None)
    # the hint a refusal gives names no place: a placeholder (run 6)
    assert credential_paths.hint().startswith("<path to the token file>")
    assert "~" not in credential_paths.hint() and "/" not in credential_paths.hint().replace("<path to the token file>", "")


def test_the_hint_never_names_a_place_the_rule_refuses(tmp_path, monkeypatch):
    home = repo_at(tmp_path / "home")
    f = token(home / ".config" / "orchestra-bridges" / "jira-token")
    git(home, "add", "-A")
    git(home, "commit", "-qm", "tracked")
    monkeypatch.setenv("HOME", str(home))
    assert "orchestra-bridges" not in credential_paths.hint()
    with pytest.raises(StoreError, match="tracks it") as e:
        credential_paths.check(str(f), "token_file", tmp_path / "project")
    assert "~/.config/orchestra-bridges" not in str(e.value)


def test_credentials_carry_the_caution_and_check_every_file(tmp_path, monkeypatch, project_repo):
    home = repo_at(tmp_path / "home")
    monkeypatch.setenv("HOME", str(home))
    token(home / ".config" / "orchestra-bridges" / "jira-token")
    got = integrate_kits.credentials({"token_file": "~/.config/orchestra-bridges/jira-token"}, project_repo)
    assert got["token_file"] == "~/.config/orchestra-bridges/jira-token" and len(got["cautions"]) == 1
    with pytest.raises(StoreError, match="user_file: .* inside the project's own repository"):
        integrate_kits.credentials({"token_env": "JIRA_TOKEN", "user_file": str(project_repo / "email")}, project_repo)
    assert integrate_kits.credentials({}, project_repo, required=False) == {}
