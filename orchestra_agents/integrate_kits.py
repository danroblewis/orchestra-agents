"""The seam between ``integrate_system`` (``design_integrate.py``) and what Orchestra's
``docs/INTEGRATING.md`` builds an integration from: the bridges plugin's kits
(``orchestra_bridges.kits``: its scaffold, templates and fakes) and a scaffolded poller's own files.

- **Which strategy** (INTEGRATING.md §2, in its order of preference): a Prometheus-API store is a
  ``[[sources]]`` entry (``source``); a system with item events is a poller built from a kit
  (``poller``, which also measures); one measurement alone is the kit's ``measure`` as a
  ``[[collect]]`` (``collect``); the bridges' own reader where one exists (``bridge``). The
  Supervisor chooses; ``choose`` is the default when it does not.
- **The kits are another plugin's**, imported here only, lazily: without them ``installed`` is
  False and nothing is scaffolded (the tool proposes the no-kit route instead).
- **A scaffolded poller's files** live in Orchestra's state directory,
  ``~/.orchestrator/<key>/machines/<name>/`` (the kit's ``--root state``): its ``machine.toml`` is
  the program's own file, never Orchestra's configuration, so it is edited here line by line
  (``set_keys``, comments kept): the owner's settings (``fill``) and, on the owner's word, the clearance and the
  NAME of the token's variable (``clear``). Never a token: ``token`` keys are refused.
- **Its test** runs against the kit's fake in a child Python (Orchestra's, with the kits), with the
  environment's credential-looking variables left out, so a real token can never reach it."""

from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path
from urllib.parse import urlsplit

from .config import Project
from .secret_shapes import secret_in
from .store import StoreError

STRATEGIES = ("source", "poller", "collect", "bridge")
# Orchestra's tool with the kits (the bridges extra) and pytest, which runs a scaffolded poller's test (the dev
# extra); installing the tool again replaces it, so every plugin it had is named again (README, "Use it")
REINSTALL = ("uv tool install 'orchestra[sd,agents,bridges,dev] @ git+https://github.com/danroblewis/orchestra@main' "
             "(with tasking or workflows added when the project has them)")
STORES = ("chronosphere", "prometheus", "mimir", "thanos", "victoriametrics", "cortex")
KIT_OF = {"jira": "jira", "slack": "slack", "pagerduty": "pagerduty", **{s: "promql" for s in STORES}}
BRIDGES = {"jira": "[bridges.jira] (Data Center or Cloud: issues, changes and comments, as who did them, and counts by status)",
           "slack": "[bridges.slack] (pings to the owner: mentions and DMs, and whether they were answered)",
           "github": "[bridges.github] and [bridges.github_prs] (issues and pull requests, as who acted)"}
SCOPE = {"jql": "which Jira projects to follow (or a JQL filter)", "channels": "which Slack channels to follow (their ids)"}
SCOPE_OF = {"jira": ("jql",), "slack": ("channels",)}      # what a kit's template only guesses: the owner's to name
# what a bridges' reader needs to know what to read (its section's scope keys, any one of them), and
# the readers that have a default API (the others need the site's address)
BRIDGE_SCOPE = {"github": ("repos",), "jira": ("projects", "jql")}
BRIDGE_DEFAULT_API = ("github", "slack")
BRIDGE_TOKEN = ("jira", "slack")                           # the readers that read a token's variable (GitHub's is gh's login)
TITLES = {"jira": "Jira", "slack": "Slack", "pagerduty": "PagerDuty", "github": "GitHub", "chronosphere": "Chronosphere"}
KEY = re.compile(r"^[a-z_][a-z0-9_]{0,40}$")
PLACEHOLDER_HOSTS = ("example.com", "example.org", "acme.chronosphere.io")
CREDENTIAL_VARS = re.compile(r"(TOKEN|SECRET|PASSWORD|PASSWD|API_KEY|_KEY|BEARER|CREDENTIAL)", re.IGNORECASE)
TEST_TIMEOUT_S = 300
INTEGRATING = "Orchestra's docs/INTEGRATING.md (https://github.com/danroblewis/orchestra/blob/main/docs/INTEGRATING.md)"


def installed() -> bool:
    """Whether the bridges plugin's kits are importable in this Python (Orchestra's)."""
    try:
        return importlib.util.find_spec("orchestra_bridges.kits") is not None
    except ModuleNotFoundError:
        return False


def title(system: str) -> str:
    return TITLES.get(system, system.replace("_", " ").title())


def choose(system: str) -> str:
    """INTEGRATING.md §2's first fit by the system's name: a metrics store reads in place
    (``source``); anything else with items is a poller (which measures too)."""
    return "source" if system in STORES else "poller"


def kit_for(system: str, strategy: str) -> str:
    return "promql" if strategy == "collect" and system in STORES else KIT_OF.get(system, "http")


def kits() -> tuple[str, ...]:
    from orchestra_bridges.kits import scaffold as s
    return tuple(sorted(s.KINDS))


def scaffold(project: Project, kit: str, name: str) -> Path:
    """The kit's poller written into the state directory (``--root state``); its directory."""
    from orchestra_bridges.kits import scaffold as s
    try:
        return s.scaffold(kit, name, s.root_dir(project.orchestra, "state"))
    except (s.ScaffoldError, OSError) as e:
        raise StoreError(f"scaffold: {e}") from None


def read(target: Path) -> dict:
    return tomllib.loads((target / "machine.toml").read_text(encoding="utf-8"))


def publishes(target: Path) -> list[str]:
    """The event types the poller's ``machine.toml`` says it publishes (a template's placeholder left out)."""
    types = [str(p.get("type") or "") for p in read(target).get("publishes") or []]
    return [t for t in types if re.fullmatch(r"[a-z][a-z0-9_]*(\.[a-z0-9_]+)+", t)]


def placeholder(url: str | None) -> bool:
    host = (urlsplit(url or "").hostname or "").lower()
    return not host or any(host == h or host.endswith("." + h) for h in PLACEHOLDER_HOSTS)


def check_settings(settings: dict) -> dict:
    """The owner's settings for the program, checked: plain keys, plain values, no credential."""
    if not isinstance(settings, dict):
        raise StoreError("settings is an object of the program's own settings (jql, channels, every, per_minute, ttl)")
    out = {}
    for k, v in settings.items():
        if not KEY.match(str(k)):
            raise StoreError(f"settings: {k!r} is not a setting's name (lower case, digits, _)")
        if k in ("token", "token_env", "token_file", "clearance", "data_class") or "token" in k or "secret" in k:
            raise StoreError(f"settings: {k} is not set here: the token's variable and the clearance are the owner's, at "
                             "approve_integration; a token is never written anywhere")
        ok = isinstance(v, str | int | float | bool) or (isinstance(v, list) and all(isinstance(x, str | int | float) for x in v))
        if not ok:
            raise StoreError(f"settings: {k} is a string, a number, true/false or a list of them")
        if why := secret_in(f"{k} = {v!r}"):
            raise StoreError(f"settings: {k} looks like it holds a secret ({why}); nothing was written")
        out[k] = v
    return out


def _value(v) -> str:
    """A TOML value from a string, number, boolean or list of them (JSON's string escapes are TOML's)."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int | float):
        return repr(v)
    if isinstance(v, list):
        return "[" + ", ".join(_value(x) for x in v) + "]"
    return json.dumps(str(v))


def set_keys(text: str, table: str | None, values: dict) -> str:
    """``text`` with each key of ``values`` set in ``table`` (None: the top level), line by line:
    a key's line replaced (its end-of-line comment, which described the old value, goes), a new key
    added at the end of the table; every other line, comments included, kept as it was. The
    program's own ``machine.toml`` is edited this way; Orchestra's configuration never is (rule 12)."""
    lines = text.splitlines()
    head = re.compile(r"^\s*\[")
    start = 0 if table is None else next(
        (i + 1 for i, ln in enumerate(lines) if re.match(rf"^\s*\[{re.escape(table)}\]\s*(#.*)?$", ln)), None)
    if start is None:
        lines += ["", f"[{table}]"]
        start = len(lines)
    end = next((i for i in range(start, len(lines)) if head.match(lines[i])), len(lines))
    for k, v in values.items():
        line = f"{k} = {_value(v)}"
        at = next((i for i in range(start, end) if re.match(rf"^\s*{re.escape(k)}\s*=", lines[i])), None)
        if at is not None:
            lines[at] = line
            continue
        last = max((i for i in range(start, end) if lines[i].strip()), default=start - 1)
        lines.insert(last + 1, line)
        end += 1
    return "\n".join(lines) + "\n"


def _edit(target: Path, top: dict, settings: dict) -> None:
    """The change, written, then read back by the program's own reader (the bridges'
    ``machinefile.read``): what it refuses is put back as it was."""
    from orchestra_bridges.kits.machinefile import read as read_machine_file
    path = target / "machine.toml"
    old = path.read_text(encoding="utf-8")
    text = set_keys(set_keys(old, None, top), "settings", settings)
    got = tomllib.loads(text)                       # still TOML, and says what was set: refused before it is written otherwise
    if any(got.get(k) != v for k, v in top.items()) or any(got.get("settings", {}).get(k) != v for k, v in settings.items()):
        raise StoreError(f"{path}: the change did not read back as written; nothing was changed")
    path.write_text(text, encoding="utf-8")
    try:
        read_machine_file(path)
    except ValueError as e:                         # MachineFileError: the program would refuse to start on it
        path.write_text(old, encoding="utf-8")
        raise StoreError(f"the poller's own reader refuses the change ({e}); machine.toml was put back as it was") from None


def env_name_ok(name: str) -> bool:
    """Whether ``name`` is a token's variable name the reader will take: the bridges' kits' rule
    (``orchestra_bridges.kits.settings.ENV_NAME``), else, with no kits, Orchestra's own
    (``[[sources]]``'s ``bearer_env``, ``orchestra.sd.intended.ops.ENV_NAME``)."""
    try:
        from orchestra_bridges.kits.settings import ENV_NAME
    except ImportError:
        from orchestra.sd.intended.ops import ENV_NAME
    return bool(ENV_NAME.match(name))


def fill(target: Path, settings: dict, base_url: str | None) -> None:
    """Settings into the poller's ``machine.toml``; never its clearance (the owner's words are the
    integration record's, ``wants``)."""
    _edit(target, {}, {**settings, **({"base_url": base_url} if base_url else {})})


def clear(target: Path, token_env: str, data_class: str, base_url: str | None) -> None:
    """The owner's approval into ``machine.toml``: ``clearance = "approved"``, the data class, and
    the NAME of the variable that holds the token."""
    _edit(target, {"clearance": "approved", "data_class": data_class},
          {"token_env": token_env, **({"base_url": base_url} if base_url else {})})


def test_env() -> dict[str, str]:
    """This environment without anything that may hold a credential: the test sets its own."""
    return {k: v for k, v in os.environ.items()
            if not CREDENTIAL_VARS.search(k) and not k.startswith(("ORCHESTRA_EVENTS_", "PYTEST_"))}


def run_test(target: Path, name: str, timeout_s: float = TEST_TIMEOUT_S) -> dict:
    """The scaffolded test against the kit's fake: ``{ran, passed, summary, command}`` (and
    ``tail``, its last lines, when it failed)."""
    test = target / f"test_{name}.py"
    command = f"python -m pytest {test} -q"
    if importlib.util.find_spec("pytest") is None:
        return {"ran": False, "passed": False, "command": command,
                "summary": "pytest is not installed in Orchestra's Python, so the test was not run"}
    try:
        p = subprocess.run([sys.executable, "-m", "pytest", str(test), "-q", "-p", "no:cacheprovider", "--rootdir", str(target)],
                           cwd=target, env=test_env(), capture_output=True, text=True, timeout=timeout_s, check=False)
    except subprocess.TimeoutExpired:
        return {"ran": True, "passed": False, "command": command, "summary": f"the test did not finish in {timeout_s:g} s"}
    lines = [ln for ln in (p.stdout + p.stderr).splitlines() if ln.strip()]
    out = {"ran": True, "passed": p.returncode == 0, "command": command, "summary": lines[-1] if lines else f"exit {p.returncode}"}
    if p.returncode != 0:
        out["tail"] = lines[-15:]
    return out
