"""The seam between ``integrate_system`` (``design_integrate.py``) and what Orchestra's
``docs/INTEGRATING.md`` builds an integration from: the bridges plugin's kits
(``orchestra_bridges.kits``: its scaffold, templates and fakes) and a scaffolded poller's own files.

- **Which strategy** (INTEGRATING.md §2, in its order of preference): a Prometheus-API store is a
  ``[[sources]]`` entry (``source``); a system with item events is a poller built from a kit
  (``poller``, which also measures); one measurement alone is the kit's ``measure`` as a
  ``[[collect]]`` (``collect``). The Supervisor chooses; ``choose`` is the default when it does
  not. The bridges are a library (the owner, 2026-11-01: "a library or framework so that AI agents
  within my repo ... could write scripts in the target repo"): every collector is a script built
  from a kit, never a reader inside the bridges plugin.
- **The kits are another plugin's**, imported here only, lazily: without them ``installed`` is
  False and nothing is scaffolded (the tool proposes the no-kit route instead).
- **A scaffolded poller's files** live in the owner's repository, ``.orchestra/machines/<name>/``
  (the kit's ``--root repo``; the agents' machine specs are read from there too, and one whose
  name is no agent is ignored), live there: a coding tool may edit them, and their files are one commit on the
  Supervisor's branch for its pull request (``integrate_branch``), or the
  Coordinator's ticket lands them (``integrate_handoff``); in Orchestra's state directory, ``~/.orchestrator/<key>/machines/<name>/``
  (``--root state``), only when the owner says the repository must stay clean (``ROOTS``). Its
  ``poller.toml`` is the program's own file, never Orchestra's configuration, so it is edited here
  line by line (``set_keys``, comments kept): the owner's settings (``fill``) and, on the owner's
  word, the clearance and the credentials BY REFERENCE (``clear``: a variable's name or a file's
  path, for the token and for an HTTP Basic account). Never a token: ``token`` keys are refused.
- **Its test** runs against the kit's fake in a child Python (Orchestra's, with the kits), with the
  environment's credential-looking variables left out, so a real token can never reach it."""

from __future__ import annotations

import importlib.util
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path
from urllib.parse import urlsplit

from . import credential_paths
from .config import Project
from .integrate_settings import CREDENTIAL_KEYS, check_settings, drop_keys, set_keys
from .secret_shapes import secret_in
from .store import StoreError

__all__ = ["CREDENTIAL_KEYS", "check_settings", "drop_keys", "set_keys"]   # the settings, checked and written: integrate_settings

STRATEGIES = ("source", "poller", "collect")
ROOTS = {"repository": "repo", "state": "state"}      # integrate_system's word -> the kit scaffold's --root
# Orchestra's tool with the kits (the bridges extra) and pytest, which runs a scaffolded poller's test (the dev
# extra); installing the tool again replaces it, so every plugin it had is named again (README, "Use it")
REINSTALL = ("uv tool install 'orchestra[sd,agents,bridges,dev] @ git+https://github.com/danroblewis/orchestra@main' "
             "(with tasking or workflows added when the project has them)")
STORES = ("chronosphere", "prometheus", "mimir", "thanos", "victoriametrics", "cortex")
KIT_OF = {"jira": "jira", "slack": "slack", "discord": "discord", "pagerduty": "pagerduty", "github": "github", **{s: "promql" for s in STORES}}
SCOPE = {"jql": "which Jira projects to follow (or a JQL filter)", "channels": "which Slack channels to follow (their ids)"}
SCOPE_OF = {"jira": ("jql",), "slack": ("channels",), "discord": ("channels",)}      # what a kit's template only guesses: the owner's to name
TITLES = {"jira": "Jira", "slack": "Slack", "discord": "Discord", "pagerduty": "PagerDuty", "github": "GitHub", "chronosphere": "Chronosphere"}
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


def templated(kit: str) -> bool:
    """Whether the kit is the system's own template (``http`` is the generic one: its code is a
    guess at an API no kit knows, to be adapted)."""
    return kit != "http"


def scaffold(project: Project, kit: str, name: str, root: str = "repository") -> Path:
    """The kit's poller written into the repository (``--root repo``) or, when the owner keeps it
    clean, the state directory (``--root state``); its directory."""
    from orchestra_bridges.kits import scaffold as s
    try:
        return s.scaffold(kit, name, s.root_dir(project.orchestra, ROOTS[root]))
    except (s.ScaffoldError, OSError) as e:
        raise StoreError(f"scaffold: {e}") from None


LISTED_ROOTS = {"repo": "repository", "state": "state", "overlay": "overlay"}   # the bridges' root -> the record's word


def shipped(project: Project) -> list[dict]:
    """The collectors already in the project that a kit scaffolded, as the bridges list them
    (``orchestra_bridges.kits.integrations.listing``: under each of their roots, a ``poller.toml``,
    or a legacy ``machine.toml`` with a ``kit``, read by their own reader): each one's name (its
    directory's, which names its program and its test), kit, directory and root. Read from the files
    on disk, never from what a machine says of itself; one the bridges' reader refuses is left out."""
    from orchestra_bridges.kits.integrations import listing
    try:
        found = listing(project.orchestra)
    except (OSError, ValueError):
        return []
    return [{"name": Path(i.path).name, "kit": i.kit, "dir": i.path, "root": LISTED_ROOTS.get(i.root, i.root),
             "schedule": i.schedule, "clearance": i.clearance}
            for i in found if not i.error and i.kit != "?" and (Path(i.path) / f"{Path(i.path).name}.py").is_file()]


def has_poller(kit: str) -> bool:
    """Whether the kit relays events (a poller), as the bridges' kinds say; ``promql`` only measures."""
    from orchestra_bridges.kits.scaffold import KINDS
    return KINDS[kit].poller if kit in KINDS else True


def bounded(selector: str) -> bool:
    """The promql kit's own test of a selector that names something (``promql.bounded``)."""
    try:
        from orchestra_bridges.kits.promql import bounded
    except ImportError:
        return bool(selector.strip()) and selector.replace(" ", "") not in ("{}", '{__name__=~".+"}', '{__name__=~".*"}')
    return bounded(selector)


def settings_file(target: Path) -> Path:
    """The poller's settings file in ``target``, as the bridges' own reader finds it
    (``machinefile.locate``): its ``poller.toml``, else a legacy ``machine.toml`` that is a poller's."""
    from orchestra_bridges.kits.machinefile import locate
    return locate(target)


def read(target: Path) -> dict:
    return tomllib.loads(settings_file(target).read_text(encoding="utf-8"))


def publishes(target: Path) -> list[str]:
    """The event types the poller's ``poller.toml`` says it publishes (a template's placeholder left out)."""
    types = [str(p.get("type") or "") for p in read(target).get("publishes") or []]
    return [t for t in types if re.fullmatch(r"[a-z][a-z0-9_]*(\.[a-z0-9_]+)+", t)]


def placeholder(url: str | None) -> bool:
    host = (urlsplit(url or "").hostname or "").lower()
    return not host or any(host == h or host.endswith("." + h) for h in PLACEHOLDER_HOSTS)


def _edit(target: Path, top: dict, settings: dict, drop: list[str] = ()) -> None:
    """The change, written, then read back by the program's own reader (the bridges'
    ``machinefile.read``): what it refuses is put back as it was."""
    from orchestra_bridges.kits.machinefile import read as read_machine_file
    path = settings_file(target)
    old = path.read_text(encoding="utf-8")
    text = set_keys(set_keys(old, None, top), "settings", settings)
    text = drop_keys(text, "settings", [k for k in drop if k not in settings])
    try:
        got = tomllib.loads(text)                   # still TOML, and says what was set: refused before it is written otherwise
    except tomllib.TOMLDecodeError as e:
        raise StoreError(f"{path}: the change would not be TOML ({e}); nothing was changed") from None
    if any(k in got.get("settings", {}) for k in drop if k not in settings):
        raise StoreError(f"{path}: the change did not read back as written; nothing was changed")
    if any(got.get(k) != v for k, v in top.items()) or any(got.get("settings", {}).get(k) != v for k, v in settings.items()):
        raise StoreError(f"{path}: the change did not read back as written; nothing was changed")
    path.write_text(text, encoding="utf-8")
    try:
        read_machine_file(path)
    except ValueError as e:                         # MachineFileError: the program would refuse to start on it
        path.write_text(old, encoding="utf-8")
        raise StoreError(f"the poller's own reader refuses the change ({e}); {path.name} was put back as it was") from None


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
    """Settings into the poller's ``poller.toml``; never its clearance (the owner's words are the
    integration record's, ``wants``)."""
    _edit(target, {}, {**settings, **({"base_url": base_url} if base_url else {})})


def clear(target: Path, credentials: dict, data_class: str, base_url: str | None) -> None:
    """The owner's approval into ``poller.toml``: ``clearance = "approved"``, the data class, and
    the credentials by reference (``credentials``: some of ``CREDENTIAL_KEYS`` but ``token``, a
    variable's name or a file's path each). A credential the owner did not name is dropped, so the
    template's guess (its ``token_env``) never shadows the file they named."""
    _edit(target, {"clearance": "approved", "data_class": data_class},
          {**credentials, **({"base_url": base_url} if base_url else {})},
          drop=[k for k in CREDENTIAL_KEYS[1:] if k not in credentials])


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


def credentials(a: dict, repo_root: Path | None = None, *, required: bool = True) -> dict:
    """The owner's credentials for the program, by reference only: the token's variable
    (``token_env``) or file (``token_file``), and for HTTP Basic (Jira Cloud: the account's email)
    the account's variable (``user_env``) or file (``user_file``), the kits' own names. Never a
    value: a name that is not a variable's, a file a commit could carry (inside the project's
    repository, or tracked by the repository that encloses it: ``credential_paths``), or anything
    that looks like a secret is refused. A file allowed with a caution (untracked but not ignored
    where it is) carries it under ``cautions``, never written to the poller."""
    got = {k: (a.get(k) or "").strip() for k in ("token_env", "token_file", "user_env", "user_file")}
    out, cautions = {}, []
    for k, v in got.items():
        if not v:
            continue
        if why := secret_in(f"{k} = {v!r}"):
            raise StoreError(f"{k} looks like a credential itself ({why}): name its variable or its file, never the "
                             "value; nothing was changed")
        if k.endswith("_env") and not env_name_ok(v):
            raise StoreError(f"{k} is the NAME of an environment variable (JIRA_TOKEN), never a token: ask the owner for it")
        if k.endswith("_env"):
            out[k] = v
            continue
        out[k], caution = credential_paths.check(v, k, repo_root)
        if caution:
            cautions.append(caution)
    if required and not ({"token_env", "token_file"} & set(out)):
        raise StoreError("name where the token is: token_env, the NAME of an environment variable (JIRA_TOKEN), or "
                         f"token_file, {credential_paths.hint(repo_root, 'token')} (never in the project's repository); "
                         "never the token: ask the owner for it")
    if cautions:
        out["cautions"] = cautions
    return out


def file_state(path: str) -> str:
    """Whether a credential's file is there and private, never its content."""
    p = Path(path).expanduser()
    if not p.is_file():
        return "not there yet"
    return "there, chmod 600" if (p.stat().st_mode & 0o077) == 0 else "there, but readable by others (chmod 600 it)"
