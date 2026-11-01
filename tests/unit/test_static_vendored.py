"""The agents' pages load their libraries from the plugin's own vendored copies, never a CDN, so
they work behind a proxy that blocks one (R2, g obs 8; Orchestra's pages do the same)."""

import re
from pathlib import Path

STATIC = Path(__file__).parents[2] / "orchestra_agents" / "static"
CDN = re.compile(r"https?://(cdnjs\.cloudflare\.com|cdn\.jsdelivr\.net|unpkg\.com)")


def test_no_page_or_script_fetches_from_a_cdn():
    found = [p.name for p in STATIC.glob("*") if p.suffix in (".js", ".html", ".css") and CDN.search(p.read_text(encoding="utf-8"))]
    assert found == []


def test_every_vendored_file_agents_js_names_is_there():
    js = (STATIC / "agents.js").read_text(encoding="utf-8")
    names = re.findall(r"\$\{AGENTS_VENDOR\}/([\w.-]+\.(?:js|css))\b", js)
    assert {"marked.min.js", "purify.min.js", "highlight.min.js", "github-dark.min.css"} <= set(names)
    assert all((STATIC / "vendor" / n).is_file() for n in names)
