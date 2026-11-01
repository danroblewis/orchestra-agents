"""No push and no pull-request writes from an agent's session, unless the owner allows its role.

The agents run with their harnesses' permission checks off and with the owner's git and gh
credentials, so a prompt's "do not push" is all that stands between a worker and the owner's
remote. This puts a block in the session's environment, which every harness passes on to the
commands the agent runs (``SessionSpec.env``):

- **git**: a per-session git configuration (``GIT_CONFIG_COUNT``/``GIT_CONFIG_KEY_n``/
  ``GIT_CONFIG_VALUE_n``) with ``url."orchestra-no-push::".pushInsteadOf`` set to the empty
  prefix, which rewrites every push URL of every remote, and a URL given on the command line, to
  the transport ``orchestra-no-push``; git runs ``git-remote-orchestra-no-push`` for it, which this
  module puts first on the session's PATH and which only refuses. Fetching is untouched
  (``pushInsteadOf`` applies to pushes alone). A remote with an explicit ``pushurl`` is not
  rewritten (git's rule), so a ``git`` on the same PATH refuses ``push`` and ``send-pack`` before
  git runs, and passes every other command to the real git.
- **gh**: a ``gh`` on the same PATH that passes the reads through (``pr view``, ``pr list``,
  ``pr diff``, ``issue view``, ``api`` with GET...) and refuses everything else: ``pr merge``,
  ``pr create``, ``pr edit``, ``pr comment``, ``api`` with a method or fields, and any command it
  does not know.

Each refusal tells the agent what to do instead: commit on its branch and report; the merge gate
or the owner pushes. ``[agents] allow_push = ["<role>", ...]`` lists the roles whose sessions get
none of it. The Supervisor's session keeps what it had (no block) unless the owner decides
otherwise: its own git writes are its tools' (local commits, never a push), and anything it sends
as the owner goes through the outbox, which runs in the outbox's process, not in this session.

The limit: this stops an agent's mistake, not an agent that sets out to push (it can name the
real git by its path and clear the variables). That needs another OS user or a container.
"""

from __future__ import annotations

import os
import shlex
import shutil
import threading
from pathlib import Path

from .protocol import SUPERVISOR

TRANSPORT = "orchestra-no-push"
EXEMPT = (SUPERVISOR,)        # the owner's decision on the Supervisor is pending: it keeps what it had (SPEC §6)

ADVICE = ("Commit your work on your branch and report it (your exit tool, e.g. report_ticket); "
          "the merge gate or the owner pushes, opens and merges pull requests.")
PUSH_REFUSED = f"Orchestra refused this push: an agent's session may not push to any remote. {ADVICE}"
GH_REFUSED = ("Orchestra refused 'gh {what}': an agent's session may read pull requests and issues (gh pr view, gh pr list, "
              f"gh pr diff, gh pr checks, gh issue view, gh api GET) but not create, edit, comment on or merge them. {ADVICE}")

# gh commands that only read; a command not here is refused. A tuple of subcommands; () means any.
GH_READS: dict[str, tuple[str, ...]] = {
    "pr": ("view", "list", "diff", "checks", "status"),
    "issue": ("view", "list", "status"),
    "repo": ("view", "list"),
    "run": ("view", "list", "watch"),
    "workflow": ("view", "list"),
    "release": ("view", "list"),
    "label": ("list",),
    "search": (),
    "status": (),
    "auth": ("status",),
    "help": (),
    "version": (),
    "--version": (),
    "--help": (),
}


def blocked(role: str, allow: list[str] | tuple[str, ...]) -> bool:
    """Whether a session of ``role`` gets the block: every role but the ones the owner allows and the exempt."""
    return role not in allow and role not in EXEMPT


def env(role: str, allow: list[str] | tuple[str, ...], bin_dir: Path, base: dict[str, str] | None = None) -> dict[str, str]:
    """The variables that put the block in a session of ``role`` (nothing when it is allowed): the git
    configuration, appended after any the host's environment (``base``, default ``os.environ``) already
    carries, and ``bin_dir``, where the commands are written, first on its PATH (``base["PATH"]``)."""
    if not blocked(role, allow):
        return {}
    base = os.environ if base is None else base
    try:
        n = int(base.get("GIT_CONFIG_COUNT") or 0)
    except ValueError:
        n = 0
    path = base.get("PATH", "")
    install(bin_dir, path)
    return {"GIT_CONFIG_COUNT": str(n + 1), f"GIT_CONFIG_KEY_{n}": f"url.{TRANSPORT}::.pushInsteadOf",
            f"GIT_CONFIG_VALUE_{n}": "", "PATH": os.pathsep.join([str(bin_dir), path])}


def _which(name: str, path: str, skip: Path) -> str | None:
    """The real ``name`` on ``path``, never the one in ``skip``."""
    rest = os.pathsep.join(d for d in path.split(os.pathsep) if d and Path(d).resolve() != skip.resolve())
    return shutil.which(name, path=rest)


def _write(p: Path, text: str) -> None:
    if not p.exists() or p.read_text(encoding="utf-8") != text:
        tmp = p.with_name(f".{p.name}.{os.getpid()}.{threading.get_ident()}")    # two sessions opening at once never share one
        tmp.write_text(text, encoding="utf-8")
        tmp.chmod(0o755)
        tmp.replace(p)


def install(bin_dir: Path, path: str) -> None:
    """Write the three commands into ``bin_dir``, naming the real git and gh found on ``path``."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    _write(bin_dir / f"git-remote-{TRANSPORT}", _helper())
    _write(bin_dir / "git", _git(_which("git", path, bin_dir) or "git"))
    _write(bin_dir / "gh", _gh(_which("gh", path, bin_dir)))


def _helper() -> str:
    return ("#!/bin/sh\n# Orchestra: the transport every push URL of an agent's session is rewritten to "
            "(orchestra_agents/push_guard.py). It only refuses.\n"
            f"echo {shlex.quote(PUSH_REFUSED)} >&2\nexit 1\n")


def _git(real: str) -> str:
    """git, refusing push and send-pack; the global options before the command are skipped."""
    return f"""#!/bin/sh
# Orchestra: git for an agent's session, which may not push (orchestra_agents/push_guard.py).
skip=
for a in "$@"; do
  if [ -n "$skip" ]; then skip=; continue; fi
  case "$a" in
    -C|-c|--git-dir|--work-tree|--namespace|--config-env|--super-prefix) skip=1 ;;
    -*) ;;
    push|send-pack) echo {shlex.quote(PUSH_REFUSED)} >&2; exit 1 ;;
    *) break ;;
  esac
done
exec {shlex.quote(real)} "$@"
"""


def _gh(real: str | None) -> str:
    """gh, passing the reads in GH_READS (and ``api`` without a method or fields) and refusing the rest."""
    cases = []
    for cmd, subs in GH_READS.items():
        cond = "ok=1" if not subs else f'case "$sub" in {"|".join(subs)}) ok=1 ;; esac'
        cases.append(f"  {cmd}) {cond} ;;")
    run = f'exec {shlex.quote(real)} "$@"' if real else 'echo "gh is not installed on this machine." >&2; exit 127'
    refused = GH_REFUSED.replace("{what}", "$cmd $sub")
    return f"""#!/bin/sh
# Orchestra: gh for an agent's session, which may read but not write (orchestra_agents/push_guard.py).
cmd=; sub=; skip=
for a in "$@"; do
  if [ -n "$skip" ]; then skip=; continue; fi
  case "$a" in
    -R|--repo) skip=1 ;;
    -*) [ -z "$cmd" ] && cmd="$a" && break ;;
    *) if [ -z "$cmd" ]; then cmd="$a"; elif [ -z "$sub" ]; then sub="$a"; break; fi ;;
  esac
done
ok=
case "$cmd" in
  "") ok=1 ;;
{chr(10).join(cases)}
  api) ok=1
    for a in "$@"; do
      case "$a" in
        -X|--method|-f|-F|--field|--raw-field|--input|-X*|--method=*|--field=*|--raw-field=*|--input=*) ok= ;;
      esac
    done ;;
esac
if [ -z "$ok" ]; then
  echo "{refused.replace('"', '\\"')}" >&2
  exit 1
fi
{run}
"""
