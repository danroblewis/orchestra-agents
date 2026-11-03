"""An instrument measures; it never runs the project's code (SPEC §10, the Supervisor's bounds;
bootstrap run 2, 2026-11-02: an hourly instrument ran the project's whole Python suite, and
running it wrote a ``.venv`` and ``__pycache__`` into the owner's tree).

``refusal`` says why a collector's script or command would run the project's build or tests:
a build or test tool at a command's start (``cargo``, ``just``, ``make``, ``pytest``, ``npm test``,
``go test``, ``tox``, ``gradle`` ...), directly, through a runner (``uv run``, ``poetry run``,
``npx``, ``python -m``) or inside a quoted ``sh -c '…'``; or Orchestra's test-report reader asked to
run the suite itself (``orchestra.instruments.test_reports --run``), where it should read the
reports CI already wrote. A word in the middle of a line (``echo "just a count"``) is not a
command and is not refused. ``add_instrument``, ``set_collector`` and ``try_collector`` refuse
such a script with this reason; nothing is run."""

from __future__ import annotations

import re
import shlex
from pathlib import Path

from .store import StoreError

TOOLS = (r"cargo|just|make|gmake|pytest|py\.test|tox|nox|gradle|\./gradlew|gradlew|mvn|\./mvnw|bazel|bazelisk|rake|ctest"
         r"|ninja|jest|vitest|mocha|unittest|npm|pnpm|yarn|bun|deno|mix|dotnet|go")
GO_VERBS = ("test", "build", "run", "generate", "vet", "install")
# where a command starts: the line's start, after ; & | ( or a backtick, $( , or sh -c's opening quote
START = r"(?:^|[;&|(`\n]|\$\(|-c\s+['\"])"
ENV = r"(?:[A-Za-z_][A-Za-z0-9_]*=\S*\s+)*"
PREFIX = r"(?:(?:exec|time|nice|env|sudo|command|nohup)\s+)*"
RUNNER = r"(?:(?:uv|poetry|pipenv|hatch|pdm|rye)\s+run\s+(?:-{1,2}[\w-]+(?:[= ](?!-)\S+)?\s+)*|npx\s+|python[0-9.]*\s+-m\s+)?"
COMMAND = re.compile(rf"{START}\s*(?:cd\s+\S+\s*&&\s*)?{ENV}{PREFIX}{RUNNER}(?P<tool>{TOOLS})(?=$|[\s;&|)'\"`])(?P<rest>[^;&|\n]*)",
                     re.MULTILINE)
SCRIPT_MAX_BYTES = 64_000
TEST_REPORTS_RUN = re.compile(r"orchestra\.instruments\.test_reports\b[^\n]*\s--run\b")


def refusal(text: str) -> str | None:
    """Why ``text`` (a script, or a command as one line) would run the project's build or tests, or None."""
    text = text or ""
    if TEST_REPORTS_RUN.search(text):
        return ("it asks orchestra.instruments.test_reports to run the suite (--run): an instrument reads the reports CI "
                "already wrote (--junit <file>), never runs the project's tests")
    for m in COMMAND.finditer(text):
        tool, rest = m.group("tool"), m.group("rest").split()
        if tool == "go" and not (rest and rest[0] in GO_VERBS):
            continue
        said = " ".join([tool, *rest[:2]]).strip()
        return (f"it runs the project's build or tests ({said}): an instrument measures what is already there (a report, "
                "a file, a count, a store) and never runs the project's code, which writes into the owner's tree and takes "
                "minutes")
    return None


def _script_of(command: str, root: Path | None) -> str:
    """The text of the script a command starts (its first word, a file), when it is a small one."""
    try:
        first = shlex.split(command)[0] if command.strip() else ""
    except ValueError:
        return ""
    if not first:
        return ""
    p = Path(first).expanduser()
    p = p if p.is_absolute() or root is None else Path(root) / p
    try:
        if p.is_file() and p.stat().st_size <= SCRIPT_MAX_BYTES:
            return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        pass
    return ""


def refuse(command: str = "", script: str = "", root: Path | None = None) -> None:
    """Refuse (StoreError, nothing run or written) a collector whose command, its script's text
    (given, or the file its command starts) runs the project's build or tests."""
    for text in (script, command, _script_of(command, root)):
        if why := refusal(text):
            raise StoreError(f"not an instrument: {why}. Nothing was run or written")
