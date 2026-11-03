"""Asked where a token is, the Supervisor never names a path as an example (bootstrap run 6: it
proposed ``~/.config/orchestra-bridges/jira-token``, a file that existed on that machine, from the
hint its own tools and guide gave). The prompt, the agents' guide, the tool descriptions and the
answers that ask for a token's place show a placeholder, never a file path under a home directory."""

import re
from pathlib import Path

import pytest

from orchestra_agents import credential_paths, integrate_kits
from orchestra_agents.design_integrate import _ask
from orchestra_agents.harness.tools import TOOLS
from orchestra_agents.prompting import PROMPTS_DIR
from orchestra_agents.store import StoreError

GUIDE = Path(credential_paths.__file__).with_name("guide.md")
# a file under a home directory: two segments or more after ~, $HOME or /Users/<name>, /home/<name>
HOME_FILE = re.compile(r"(?:~|\$HOME|/Users/[\w.-]+|/home/[\w.-]+)/[\w.-]+/[\w.-]+(?:/[\w.-]+)*")


def home_files(text: str) -> list[str]:
    """The home-directory file paths ``text`` names; a path ending in a placeholder (``/<key>``) is none."""
    return [m.group(0) for m in HOME_FILE.finditer(text) if not text[m.end():m.end() + 2].startswith("/<")]


def _offered(text: str) -> list[str]:
    found = home_files(text)
    existing = [p for p in found if Path(p.replace("$HOME", "~")).expanduser().exists()]
    assert not existing, f"an existing file under the home directory is offered: {existing}"
    return found


def test_the_check_sees_a_home_path_and_passes_a_placeholder():
    assert home_files("in `~/.config/orchestra-bridges/jira-token`") == ["~/.config/orchestra-bridges/jira-token"]
    assert home_files('token_file = "/Users/me/.config/x/token"') == ["/Users/me/.config/x/token"]
    assert home_files("`<path to the token file>`, not `~/.config` or `~/.orchestrator/<key>/agents/`") == []


def test_no_prompt_and_not_the_guide_names_a_file_under_the_home_directory():
    texts = {p.name: p.read_text() for p in sorted(PROMPTS_DIR.rglob("*.md"))}
    texts["guide.md"] = GUIDE.read_text()
    offered = {name: _offered(text) for name, text in texts.items()}
    assert {k: v for k, v in offered.items() if v} == {}
    sup = texts["supervisor.md"]
    assert "never\n  name a path as an example" in sup and "never a path that exists on this machine" in sup
    assert "<path to the token file>" in sup


def test_no_tool_description_names_a_file_under_the_home_directory():
    for t in TOOLS:
        assert _offered(f"{t.description} {t.properties}") == [], t.name


@pytest.mark.parametrize("system", ["jira", "github", "slack", "discord", "pagerduty"])
def test_the_question_for_a_token_names_no_path(system):
    ask = _ask(system, None, "jql" if system == "jira" else None)
    assert "where the token is" in ask and _offered(ask) == [] and "orchestra-bridges" not in ask


def test_a_refused_token_file_is_answered_with_a_placeholder(tmp_path):
    repo = tmp_path / "project"
    (repo / ".git").mkdir(parents=True)
    hint = credential_paths.hint()
    assert hint.startswith(credential_paths.PLACEHOLDER) and _offered(hint) == []
    for value in ("jira-token", str(repo / "jira-token")):
        with pytest.raises(StoreError) as e:
            credential_paths.check(value, "token_file", repo)
        assert "<path to the token file>" in str(e.value) and "orchestra-bridges" not in str(e.value)
    with pytest.raises(StoreError) as e:
        integrate_kits.credentials({}, repo, required=True)
    assert "orchestra-bridges" not in str(e.value) and _offered(str(e.value)) == []
