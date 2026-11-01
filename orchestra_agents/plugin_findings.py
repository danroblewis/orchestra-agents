"""What each installed plugin says against the project's configuration, for ``check_setup``: every
loaded plugin's ``Plugin.check`` (Orchestra's ``config_edit.plugin_checks`` finds them, as it does for
every configuration change) run on the merged configuration and the repository root.

A plugin's findings are its own words, passed on unread. Orchestra's contract gives a check no
severity: a change that makes a plugin say something new is refused, so every finding is a
``problem``. A check that raises, or returns something other than a list of strings, is reported as
``its check failed: <error>`` (severity ``error``) and never keeps the others from running; a plugin
that did not load is named under ``not_checked``."""

from __future__ import annotations

from orchestra.config_edit import plugin_checks

from .config import Project


def loaded_checks(project: Project) -> tuple[list[tuple[str, object]], list[str]]:
    """``([(plugin name, check)], [why a plugin was not asked])``, as Orchestra loads them."""
    try:
        return plugin_checks(project.orchestra)
    except Exception as e:  # noqa: BLE001 - a broken install must not break check_setup
        return [], [f"the plugins could not be listed, so none was checked: {type(e).__name__}: {e}"]


def _run(check, raw: dict, root) -> list[dict]:
    try:
        said = check(raw, root)
        if not isinstance(said, list | tuple) or not all(isinstance(x, str) for x in said):
            raise TypeError(f"returned {type(said).__name__}, not a list of strings")
    except Exception as e:  # noqa: BLE001 - one plugin's mistake must not hide the others' findings
        return [{"severity": "error", "text": f"its check failed: {type(e).__name__}: {e}"}]
    return [{"severity": "problem", "text": x} for x in said]


def findings(project: Project) -> dict:
    """``{"plugins": [{plugin, findings: [{severity, text}]}], "not_checked": [...]}``; a plugin
    with nothing to say has an empty list."""
    checks, not_checked = loaded_checks(project)
    raw = project.orchestra.config.raw
    return {"plugins": [{"plugin": name, "findings": _run(check, raw, project.repo_root)} for name, check in checks],
            "not_checked": list(not_checked)}
