"""Clearance of a measurement option, decided from facts, never from the model's words
(machine-design.md §2.2). An option needs the owner's approval (``needs approval``: nothing reads
it, nothing previews it, ``add_instrument`` refuses it) when it reads a company system: a ``source``
option, one whose label, how, system, url, command or script names one (``COMPANY``), or one that
reaches anything but this machine, the project's own metrics store and Orchestra's own endpoint
(``far``): a URL or ``host:port`` to another host, a bare dotted host name, a host given to
``-h``/``--host``, ``ssh``, ``nc`` or ``psql``, a company CLI (``gh``, ``jira``, ``aws``,
``kubectl``, ``ssh``), or a ``curl``/``wget`` whose URL comes from an environment variable or has
no scheme. A ``[[sources]]`` store and a scrape target off this machine are another system, so they
need approval too. Until data classes exist (the work charter §8) the owner cannot clear one yet,
so every such option stays "needs approval"."""

from __future__ import annotations

import re
import socket
from urllib.parse import urlsplit

from . import intended_api
from .config import Project

# a company system: an option naming one (in its label, how, system, url or command) needs the owner's approval
COMPANY = ("jira", "github", "gitlab", "bitbucket", "prometheus", "jenkins", "confluence", "slack", "pagerduty", "datadog",
           "grafana", "splunk", "sentry", "opsgenie", "servicenow", "circleci", "buildkite")
NEEDS_APPROVAL = "needs approval"
UNTIL = "until data classes exist (the owner cannot clear one yet)"
CLEARED = "cleared: reads only this machine, the project's own metrics store and its repository"
LOCAL = ("localhost", "127.0.0.1", "::1", "0.0.0.0", "")
CLIS = ("gh", "jira", "aws", "kubectl", "ssh", "nc", "psql")
TLDS = ("com", "net", "org", "io", "co", "dev", "app", "cloud", "ai", "internal", "corp", "lan", "local", "intra", "intranet",
        "example", "us", "uk", "de", "eu", "biz", "info")
BARE_HOST = re.compile(r"(?<![\w/.@$-])((?:[a-z0-9][a-z0-9-]*\.)+(?:" + "|".join(TLDS) + r"))(?![\w.-])", re.IGNORECASE)
CLI = re.compile(r"(?:^|[\s;|&(`])(?:\S*/)?(" + "|".join(CLIS) + r")(?=\s|$)", re.MULTILINE)
HOST_FLAG = re.compile(r"(?:^|\s)(?:-h|--host)(?:\s+|=)(['\"]?)([^\s'\"]+)\1")
FETCH = re.compile(r"(?:^|[\s;|&(`])(?:\S*/)?(curl|wget)\b([^\n;|&]*)", re.MULTILINE)
URL = re.compile(r"\b[a-z][a-z0-9+.-]*://(?:[^/\s@'\"]*@)?(\[[^\]]+\]|[^/\s:'\"?#]+)", re.IGNORECASE)
HOST_PORT = re.compile(r"(?<![\w/.@-])((?:[a-z0-9-]+\.)+[a-z][a-z0-9-]*):\d{2,5}\b", re.IGNORECASE)


def allowed_hosts(project: Project) -> set[str]:
    """This machine, the project's own metrics store and Orchestra's own endpoint: never another
    store (``[[sources]]``) or a scrape target, which are other systems."""
    out = set(LOCAL) | {socket.gethostname().lower()}
    raw = project.orchestra.config.raw
    urls = [(raw.get("metrics") or {}).get("url"), intended_api.endpoint_url(project)]
    return out | {(urlsplit(u).hostname or "").lower() for u in urls if isinstance(u, str) and "://" in u}


def reaches(text: str) -> set[str]:
    """The hosts a url, command or script names: a scheme's URL, host:port, a bare dotted host
    name, and a host given to ``-h`` or ``--host``."""
    text = text or ""
    found = {m.group(1).strip("[]").lower() for m in URL.finditer(text)}
    found |= {m.group(1).lower() for m in HOST_PORT.finditer(text)}
    found |= {m.group(1).lower() for m in BARE_HOST.finditer(URL.sub(" ", text))}
    found |= {m.group(2).lower() for m in HOST_FLAG.finditer(text) if not m.group(2).startswith(("-", "/", ".", "$"))}
    return found


def far(project: Project, text: str) -> list[str]:
    """Why ``text`` (a url, command or script) reaches beyond this machine and the project's own
    store; empty when it does not."""
    text = text or ""
    why = []
    if hosts := sorted(reaches(text) - allowed_hosts(project)):
        why.append(f"it reaches {', '.join(hosts)}, neither this machine nor the project's own metrics store")
    if clis := sorted({m.group(1) for m in CLI.finditer(text)}):
        why.append(f"it runs {', '.join(clis)}, which reach{'es' if len(clis) == 1 else ''} other systems")
    for m in FETCH.finditer(text):
        args = [a for a in m.group(2).split() if not a.startswith("-")]
        if any("$" in a for a in args):
            why.append(f"its {m.group(1)} takes its URL from an environment variable, so where it goes cannot be checked")
        elif not any("://" in a for a in args):
            why.append(f"its {m.group(1)} names no URL with a scheme, so where it goes cannot be checked")
    return list(dict.fromkeys(why))


def company_named(*texts: str) -> list[str]:
    words = set(re.findall(r"[a-z0-9]+", " ".join(t or "" for t in texts).casefold()))
    return [c for c in COMPANY if c in words]


def clearance(project: Project, kind: str, *texts: str) -> tuple[str, str | None]:
    """(clearance, why) from facts: the option's kind, the company systems it names, where it reaches."""
    if kind == "source":
        return NEEDS_APPROVAL, "it reads another store"
    if named := company_named(*texts):
        return NEEDS_APPROVAL, f"it names {', '.join(named)}"
    if why := far(project, " \n".join(t for t in texts if t)):
        return NEEDS_APPROVAL, "; ".join(why)
    return CLEARED, None
