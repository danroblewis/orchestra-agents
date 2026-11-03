"""``[scm]`` as ``set_setting`` writes it: checked against orchestra-scm's own schema
(``orchestra_scm.settings``: its ``KEYS`` and ``problems``) before anything is written, so a guessed key
(the live check, 2026-11-03: ``template_sections``) is refused with the known ones named, and the
section the Supervisor's pull requests are opened through is one the library reads.

- ``key`` is ``scm`` (the whole table, an object) or ``scm.<name>``; a deeper key is refused.
- The section as it would be after the change (the merged configuration's ``[scm]`` with it) is what
  is checked, so an earlier guessed key still there is named too (remove it with value null).
- The token is never a value: a key that names one is refused by the library, anything shaped like a
  secret here, and ``token_file`` is a file's full path checked by T27's rule (``credential_paths``):
  refused inside the project's repository or where a repository tracks it, a caution when one
  encloses it untracked and not ignored. The file itself is never read here.

orchestra-scm is optional (AGENTS.md rule 3): imported only here, only when ``[scm]`` is set."""

from __future__ import annotations

from pathlib import Path

from . import credential_paths
from .secret_shapes import secret_in
from .store import StoreError

SECTION = "scm"


def schema() -> tuple[set[str], object]:
    """orchestra-scm's keys and its ``problems``; StoreError when the library is not installed."""
    try:
        from orchestra_scm.settings import KEYS, problems
    except ImportError:
        raise StoreError("[scm] is read by orchestra-scm, which is not installed here, so nothing would read it; "
                         "nothing was written") from None
    return set(KEYS), problems


def after(current: dict, key: str, value) -> dict | None:
    """``[scm]`` once ``key`` is set to ``value`` (None removes it); None when the whole table goes."""
    parts = key.split(".")
    if parts[0] != SECTION:
        raise ValueError(key)
    if len(parts) == 1:
        if value is None:
            return None
        if not isinstance(value, dict):
            raise StoreError('[scm] is a table: give its keys as an object, {"repo": "owner/name", "base": "main"}')
        return dict(value)
    if len(parts) > 2:
        raise StoreError(f"{key}: [scm] has no nested tables; its keys are scm.<name> (scm.repo, scm.protect)")
    out = dict(current)
    if value is None:
        out.pop(parts[1], None)
    else:
        out[parts[1]] = value
    return out


def check(project, key: str, value) -> str | None:
    """Refuse (StoreError) an ``[scm]`` change orchestra-scm would refuse or that holds a token; the
    caution T27's rule gives for the token file, or None. A key outside ``[scm]`` is not checked here."""
    if key.split(".")[0] != SECTION:
        return None
    keys, problems = schema()
    current = dict((project.orchestra.config.raw or {}).get(SECTION) or {})
    section = after(current, key, value)
    if section is None:
        return None
    for k, v in section.items():
        if why := secret_in(f"{k} = {v!r}"):
            raise StoreError(f"[scm] {k} looks like it holds a secret ({why}); a token is never a setting, only the file "
                             "that holds it (token_file). Nothing was written")
    if bad := problems(section, Path(project.repo_root)):
        raise StoreError("orchestra-scm would refuse this [scm], so nothing was written: " + "; ".join(bad)
                         + f". Its keys: {', '.join(sorted(keys))}")
    caution = None
    if "token_file" in section:
        tf = section["token_file"]
        if not isinstance(tf, str):
            raise StoreError("[scm] token_file is the full path of the file that holds the token, as a string")
        _, caution = credential_paths.check(tf, "[scm] token_file", Path(project.repo_root))
    return caution


def valid(project) -> bool:
    """Whether the merged configuration's ``[scm]`` is one orchestra-scm reads, with a repository named."""
    section = dict((project.orchestra.config.raw or {}).get(SECTION) or {})
    if not section.get("repo"):
        return False
    try:
        _, problems = schema()
    except StoreError:
        return False
    return not problems(section, Path(project.repo_root))
