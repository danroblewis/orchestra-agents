"""Where a program in ``.orchestra/machines/<name>/`` goes when it graduates into the project's own
tree, and the least wiring its language needs there (``graduate_machine``, ``machine_graduate.py``):

- **Python** (``pyproject.toml``): into the project's package, ``<pkg>/<name>/`` (or
  ``src/<pkg>/<name>/``), with an ``__init__.py`` and a ``__main__.py`` that runs the entry script as
  a module, so it runs as ``python3 -m <pkg>.<name>`` (``uv run python -m <pkg>.<name>`` when the
  project has a ``uv.lock``: the project's own Python, not Orchestra's);
- **Node** (``package.json``): into ``scripts/<name>/``, with a script entry in ``package.json``, so it
  runs as ``npm run <name>``;
- **Rust** (``Cargo.toml``): into ``src/bin/<name>/``, with a ``[[bin]]`` entry in ``Cargo.toml``, so
  it runs as ``cargo run --bin <name>``.

The language is the machine's files' (``.py``, ``.js``, ``.rs``), and the project's manifest for it
must be at the repository's root; anything else (a machine in two languages, a crate of its own, a
project with no package to put it in) is refused with why, for the Coordinator's worker. Nothing
here writes until ``apply`` is called with the plan ``plan`` worked out; the plan is plain data, so
the dry run shows exactly what the move does. The program's code is never changed: it moves whole,
so its announcement (``orchestra.machine.started``) and its events stay what they were."""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

from .store import StoreError

LANGUAGES = ("python", "node", "rust")
MANIFEST = {"python": "pyproject.toml", "node": "package.json", "rust": "Cargo.toml"}
SUFFIXES = {"python": (".py",), "node": (".js", ".mjs", ".cjs"), "rust": (".rs",)}
SKIP = ("__pycache__", "node_modules", "target", ".pytest_cache")


def files_of(mdir: Path) -> list[Path]:
    return sorted(p for p in mdir.rglob("*") if p.is_file() and not any(s in p.parts for s in SKIP))


def language_of(mdir: Path, repo: Path) -> str:
    """The machine's language, from its files; refused when it is none or several of the three."""
    found = {lang for lang, sx in SUFFIXES.items() for p in files_of(mdir) if p.suffix in sx}
    if (mdir / "Cargo.toml").exists():
        found.add("rust")
    if len(found) != 1:
        said = ", ".join(sorted(found)) or "no .py, .js or .rs file"
        raise StoreError(f"{mdir.name}'s language is not one of python, node, rust ({said}): give language, or hand the "
                         "move to the Coordinator; nothing was moved")
    return found.pop()


def _entry(mdir: Path, name: str, lang: str) -> str:
    """The file that runs the machine, relative to its directory."""
    sx = SUFFIXES[lang]
    named = [f"{name}{s}" for s in sx] + {"python": ["__main__.py", "main.py"], "node": [f"index{s}" for s in sx],
                                          "rust": ["main.rs", "src/main.rs"]}[lang]
    for n in named:
        if (mdir / n).is_file():
            return n
    cands = [p for p in files_of(mdir) if p.suffix in sx and not p.name.startswith("test_") and ".test." not in p.name]
    if len(cands) == 1:
        return str(cands[0].relative_to(mdir))
    raise StoreError(f"which file runs {name} is not clear in {mdir} (looked for {', '.join(named)}): nothing was moved")


def _python(repo: Path, name: str, entry: str) -> dict:
    try:
        proj = tomllib.loads((repo / "pyproject.toml").read_text(encoding="utf-8")).get("project") or {}
    except tomllib.TOMLDecodeError as e:
        raise StoreError(f"pyproject.toml does not read: {e}") from None
    pkg = re.sub(r"[-.]+", "_", str(proj.get("name") or "")).lower()
    where = next((d for d in (repo / pkg, repo / "src" / pkg) if pkg and (d / "__init__.py").is_file()), None)
    if where is None:
        raise StoreError(f"the project has no package to put {name} in (pyproject.toml names {pkg or 'none'}; no "
                         f"{pkg or '<pkg>'}/__init__.py or src/{pkg or '<pkg>'}/__init__.py): making one is code work for "
                         "the Coordinator; nothing was moved")
    dest = where / name
    module = f"{pkg}.{name}"
    if (where / f"{name}.py").exists():
        raise StoreError(f"{where.relative_to(repo)}/{name}.py exists already: the module {module} is taken; give destination")
    run = "uv run python" if (repo / "uv.lock").exists() else "python3"
    writes = [{"path": str((dest / "__init__.py").relative_to(repo)), "text": "", "if_missing": True}]
    if entry != "__main__.py":
        mod = entry[:-3].replace("/", ".")
        writes.append({"path": str((dest / "__main__.py").relative_to(repo)), "if_missing": True,
                       "text": (f'"""Runs {name} as `python3 -m {module}` (graduated from .orchestra/machines/{name}/)."""\n\n'
                                "import runpy\n\n"
                                f'runpy.run_module(f"{{__package__}}.{mod}", run_name="__main__")\n')})
    notes = [] if where.parent == repo else [(f"src layout: {module} imports only where the project is installed ({run} does "
                                              "that with uv; otherwise install it first)")]
    return {"dest": str(dest.relative_to(repo)), "command": f"{run} -m {module}", "writes": writes, "manifest": None,
            "notes": [*notes, (f"a sibling imported as a top-level module (import helper) becomes a relative import "
                               f"(from . import helper) when it runs as {module}: check it runs (see verify)")]}


def _node(repo: Path, name: str, entry: str) -> dict:
    try:
        pkg = json.loads((repo / "package.json").read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise StoreError(f"package.json does not read: {e}") from None
    dest = f"scripts/{name}"
    script = f"node {dest}/{entry}"
    have = (pkg.get("scripts") or {}).get(name)
    if have not in (None, script):
        raise StoreError(f"package.json has a script {name} already ({have!r}): give the machine another name, or hand it "
                         "to the Coordinator; nothing was moved")
    return {"dest": dest, "command": f"npm run {name}", "writes": [],
            "manifest": {"file": "package.json", "what": f"scripts.{name} = {script!r}", "kind": "npm_script",
                         "key": name, "value": script}, "notes": []}


def _rust(repo: Path, name: str, entry: str, mdir: Path) -> dict:
    try:
        cargo = tomllib.loads((repo / "Cargo.toml").read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as e:
        raise StoreError(f"Cargo.toml does not read: {e}") from None
    if (mdir / "Cargo.toml").exists():          # a crate of its own: it joins the workspace as a member, unchanged
        crate = (tomllib.loads((mdir / "Cargo.toml").read_text(encoding="utf-8")).get("package") or {}).get("name") or name
        dest = f"crates/{name}"
        return {"dest": dest, "command": f"cargo run -p {crate}", "writes": [],
                "manifest": {"file": "Cargo.toml", "what": f"[workspace] members += {dest!r}", "kind": "cargo_member",
                             "key": name, "value": dest},
                "notes": ([] if "workspace" in cargo else [("Cargo.toml gets a [workspace] of its own: the project's "
                                                             "package and the machine's crate build together from now on")])}
    if "package" not in cargo:
        raise StoreError("Cargo.toml is a workspace with no [package] of its own: which member takes the binary is the "
                         "Coordinator's to choose; nothing was moved")
    dest = f"src/bin/{name}"
    path = f"{dest}/{entry}"
    for b in cargo.get("bin") or []:
        if b.get("name") == name and b.get("path") != path:
            raise StoreError(f"Cargo.toml has a [[bin]] {name} already (path {b.get('path')}): nothing was moved")
    return {"dest": dest, "command": f"cargo run --bin {name}", "writes": [],
            "manifest": {"file": "Cargo.toml", "what": f"[[bin]] name = {name!r}, path = {path!r}", "kind": "cargo_bin",
                         "key": name, "value": path},
            "notes": ["the crates it uses must be in Cargo.toml's [dependencies]: cargo build --bin " + name + " says"]}


def plan(repo: Path, mdir: Path, name: str, lang: str | None = None, destination: str | None = None) -> dict:
    """Where the machine goes, what runs it there, and what is written beside it; nothing written."""
    lang = lang or language_of(mdir, repo)
    if lang not in LANGUAGES:
        raise StoreError(f"language is one of {', '.join(LANGUAGES)}")
    if not (repo / MANIFEST[lang]).is_file():
        have = [m for m in MANIFEST.values() if (repo / m).is_file()]
        raise StoreError(f"{name} is {lang}, and the project has no {MANIFEST[lang]} at its root"
                         + (f" (it has {', '.join(have)})" if have else "") + ": where it goes is the Coordinator's to "
                         "choose; nothing was moved")
    entry = "Cargo.toml" if lang == "rust" and (mdir / "Cargo.toml").exists() else _entry(mdir, name, lang)
    p = {"python": lambda: _python(repo, name, entry), "node": lambda: _node(repo, name, entry),
         "rust": lambda: _rust(repo, name, entry, mdir)}[lang]()
    if destination:
        d = Path(destination)
        if d.is_absolute() or ".." in d.parts or d.parts[:1] == (".orchestra",):
            raise StoreError("destination is a path inside the repository and outside .orchestra/ (scripts/relator)")
        if lang != "node":
            raise StoreError(f"a {lang} machine's destination follows the language ({p['dest']}): leave destination out")
        p["dest"] = str(d)
        p["manifest"] = {**p["manifest"], "value": f"node {d}/{entry}", "what": f"scripts.{name} = 'node {d}/{entry}'"}
    if lang == "rust":
        cargo_text((repo / "Cargo.toml").read_text(encoding="utf-8"), p["manifest"])     # refused now, not after the move
    if (repo / p["dest"]).exists():
        raise StoreError(f"{p['dest']} exists already in the project: give destination, or hand the move to the Coordinator; "
                         "nothing was moved")
    return {"language": lang, "entry": entry, **p}


def with_args(command: str, args: list[str], lang: str) -> str:
    """The new command with the arguments the old one gave after the script (``measure``)."""
    import shlex
    if args[:1] == ["--"]:
        args = args[1:]
    if not args:
        return command
    rest = " ".join(shlex.quote(a) for a in args)
    return f"{command} -- {rest}" if lang in ("node", "rust") else f"{command} {rest}"


def cargo_text(text: str, m: dict) -> str:
    """Cargo.toml with the machine's ``[[bin]]`` or workspace member added, as text: appended at the
    end (a table may come last), or the member put first in an existing ``[workspace] members``.
    Checked by reading the result back; refused when it does not hold the entry."""
    body = text if text.endswith("\n") or not text else text + "\n"
    cargo = tomllib.loads(text)
    if m["kind"] == "cargo_bin":
        if any(b.get("name") == m["key"] for b in cargo.get("bin") or []):
            return text
        new = body + f'\n[[bin]]\nname = {json.dumps(m["key"])}\npath = {json.dumps(m["value"])}\n'
    elif "workspace" not in cargo:
        new = body + f'\n[workspace]\nmembers = [{json.dumps(m["value"])}]\n'
    elif m["value"] in (cargo["workspace"].get("members") or []):
        return text
    else:
        head = re.search(r"^\[workspace\][^\n]*\n", text, re.MULTILINE)
        mem = re.compile(r"^(members\s*=\s*\[)", re.MULTILINE)
        hit = mem.search(text, head.end()) if head else None
        nxt = re.compile(r"^\[", re.MULTILINE).search(text, head.end()) if head else None
        if hit is None or (nxt is not None and hit.start() > nxt.start()):
            raise StoreError("Cargo.toml's [workspace] has no members list to add the crate to: that is the Coordinator's "
                             "to wire; nothing was moved")
        new = text[:hit.end()] + json.dumps(m["value"]) + ", " + text[hit.end():]
        new = new.replace(json.dumps(m["value"]) + ", ]", json.dumps(m["value"]) + "]")
    got = tomllib.loads(new)
    ok = (any(b.get("name") == m["key"] and b.get("path") == m["value"] for b in got.get("bin") or [])
          if m["kind"] == "cargo_bin" else m["value"] in ((got.get("workspace") or {}).get("members") or []))
    if not ok:
        raise StoreError(f"Cargo.toml would not hold {m['what']} after the edit: nothing was moved")
    return new


def apply_manifest(repo: Path, m: dict | None) -> str | None:
    """The language's one entry, written to its manifest: what was written, or None."""
    if not m:
        return None
    f = repo / m["file"]
    f.write_text(manifest_text(f.read_text(encoding="utf-8"), m), encoding="utf-8")
    return f"{m['file']}: {m['what']}"


def manifest_text(text: str, m: dict) -> str:
    """The manifest ``text`` with the language's one entry ``m`` written in."""
    if m["kind"] == "npm_script":
        pkg = json.loads(text)
        indent = re.match(r"\{\s*\n(\s+)", text)
        pkg.setdefault("scripts", {})[m["key"]] = m["value"]
        return json.dumps(pkg, indent=len(indent.group(1)) if indent else 2) + ("\n" if text.endswith("\n") else "")
    return cargo_text(text, m)
