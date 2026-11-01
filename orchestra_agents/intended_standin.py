"""A stand-in for Orchestra's declared layer (``intended-model.md`` §5–§6.3; Orchestra's
``orchestra/sd/intended/``, task C1), used only while the installed Orchestra has no
``project.intended`` (``intended_api.py``, the seam; delete this file when Orchestra's is
installed).

It takes the same ops in the same shapes as Orchestra's (C1's interim commit 082ccc89): all of a
call or none, change ids ``c<seq>.<i>``, ``read() -> {version, at, layer}``, ``apply -> {version,
changes: [{op, part, id, change}]}``; what the owner asks for with a quote is declared (or applied)
at once, anything else proposed (or pending); discard removes. It keeps the layer and the journal in
the agents' state directory (``intended/layer.json``, ``journal.jsonl``) under a file lock, so the
MCP server's process and the host's see the same thing. It checks less than Orchestra does and
derives nothing (presence, recording, verdicts)."""

from __future__ import annotations

import copy
import fcntl
import json
import re
import time
from contextlib import contextmanager
from pathlib import Path

from .config import Project
from .store import StoreError

ID = re.compile(r"^[a-z][a-z0-9_]{1,40}$")
GROUP = {"stock": "stocks", "effect": "effects", "flow": "flows", "machine": "machines", "attachment": "attachments",
         "event": "events"}
KINDS = ("universal", "programmatic")
MANAGED = ("orchestra", "external")
MODES = ("ad hoc", "continuous", "scheduled")
MISSED = ("skip", "run once")
LISTS = ("tags", "receives", "emits", "reads")
ENVELOPE = ("id", "title", "description", "tags", "kind", "managed", "engagement", "receives", "emits", "reads", "spec")
OPTION = ("key", "title", "how", "kind", "instrument", "simulated", "cost", "caveat", "clearance", "provenance", "commit",
          "first_points")


def empty() -> dict:
    return {"stocks": {}, "effects": {}, "flows": {}, "machines": {}, "events": {}, "attachments": {}, "targets": {},
            "marks": {"expected": {}, "ignored": {}}}


def slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", (name or "").lower()).strip("_")
    return s if s[:1].isalpha() or not s else f"x_{s}"


def check_engagement(e) -> dict:
    if not isinstance(e, dict) or e.get("mode") not in MODES:
        raise StoreError(f"engagement is {{mode, at?, tz?, missed?}} with mode one of {', '.join(MODES)}")
    if e["mode"] != "scheduled" and any(k in e for k in ("at", "tz", "missed")):
        raise StoreError("at, tz and missed belong to a scheduled engagement only")
    if e.get("missed") is not None and e["missed"] not in MISSED:
        raise StoreError(f"missed is one of {', '.join(MISSED)}")
    if extra := set(e) - {"mode", "at", "tz", "missed"}:
        raise StoreError(f"engagement has no {sorted(extra)}")
    return e


def check_envelope(m: dict) -> None:
    if not ID.match(str(m.get("id") or "")):
        raise StoreError(f"a machine's id is its bus source: ^[a-z][a-z0-9_]{{1,40}}$ (not {m.get('id')!r})")
    if extra := set(m) - set(ENVELOPE):
        raise StoreError(f"unknown envelope fields {sorted(extra)} (the rest of a machine is its spec)")
    if m.get("kind") not in KINDS or m.get("managed") not in MANAGED:
        raise StoreError(f"kind is {' or '.join(KINDS)}; managed is {' or '.join(MANAGED)}")
    check_engagement(m.get("engagement"))
    for k in LISTS:
        if not isinstance(m.get(k, []), list) or not all(isinstance(x, str) for x in m.get(k, [])):
            raise StoreError(f"{k} is a list of strings")
    if not isinstance(m.get("spec", {}), dict):
        raise StoreError("spec is an object")


def only(op: dict, allowed: tuple) -> None:
    if extra := sorted(set(op) - set(allowed) - {"op", "guessed"}):
        raise StoreError(f"{op.get('op')}: unknown field(s) {', '.join(extra)}")


class StandIn:
    def __init__(self, project: Project):
        self.dir: Path = project.state_dir / "intended"

    @contextmanager
    def _locked(self):
        self.dir.mkdir(parents=True, exist_ok=True)
        with (self.dir / ".lock").open("a") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)

    def read(self, version: int | None = None) -> dict:
        p = self.dir / "layer.json"
        if not p.exists():
            return {"version": 0, "at": None, "layer": empty()}
        return json.loads(p.read_text(encoding="utf-8"))

    def journal(self) -> list[dict]:
        p = self.dir / "journal.jsonl"
        return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()] if p.exists() else []

    def apply(self, ops: list[dict], provenance: dict) -> dict:
        if not isinstance(ops, list) or not ops:
            raise StoreError("apply takes a list of ops")
        prov = {k: v for k, v in {**provenance, "at": provenance.get("at") or time.time()}.items() if v is not None}
        if prov.get("author") == "owner" and not prov.get("quote"):
            raise StoreError("a plugin's author: owner is backed by the owner's words: give the quote")
        with self._locked():
            cur = self.read()
            layer = copy.deepcopy(cur["layer"])
            seq = cur["version"] + 1
            changes = []
            for i, op in enumerate(ops):
                cid = f"c{seq}.{i}"
                try:
                    part, eid = self._one(layer, op, {"provenance": prov, "change": cid, "op": op})
                except StoreError as e:
                    raise StoreError(f"op {i} ({op.get('op')}): {e}; nothing was applied") from None
                changes.append({"op": op.get("op"), "part": part, "id": eid, "change": cid})
            tmp = self.dir / "layer.json.tmp"
            tmp.write_text(json.dumps({"version": seq, "at": prov["at"], "layer": layer}), encoding="utf-8")
            tmp.replace(self.dir / "layer.json")
            with (self.dir / "journal.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps({"seq": seq, "at": prov["at"], "provenance": prov, "ops": ops, "changes": changes}) + "\n")
        return {"version": seq, "changes": changes}

    def _one(self, L: dict, op: dict, ctx: dict) -> tuple[str, str]:
        kind, prov = op.get("op"), ctx["provenance"]
        state = "declared" if prov.get("author") == "owner" else "proposed"
        new = {"state": state, "provenance": prov, "change": ctx["change"], "guessed": list(op.get("guessed") or []), "pending": []}
        if kind in ("declare_stock", "declare_effect"):
            only(op, ("id", "name", "unit", "near", "note"))
            part = kind.split("_")[1]
            eid = op.get("id") or slug(op.get("name") or "")
            if not eid or not (op.get("name") or "").strip():
                raise StoreError("a stock or effect needs a name")
            if eid in L[GROUP[part]]:
                raise StoreError(f"{part} {eid} is declared already")
            L[GROUP[part]][eid] = {"id": eid, "part": part, **{k: op.get(k) for k in ("name", "unit", "near", "note")},
                                   "found_as": None, "measure": None, "target": None, **new}
            return part, eid
        if kind == "set_target":
            only(op, ("part", "id", "key", "target", "value", "hi", "over_s"))
            if op.get("target") not in ("below", "above", "within"):
                raise StoreError("a target's op is below, above or within")
            t = {"op": op["target"], "value": op.get("value"), "over_s": op.get("over_s"), **({"hi": op["hi"]} if "hi" in op else {})}
            if op.get("id") is not None:
                self._el(L, op["part"], op["id"])["target"] = t
                return op["part"], op["id"]
            L["targets"][op["key"]] = {"part": op["part"], "key": op["key"], "target": t, "provenance": prov, "change": ctx["change"]}
            return op["part"], op["key"]
        if kind in ("expect", "unexpect", "ignore", "unignore"):
            only(op, ("part", "key"))
            if op.get("part") not in ("stock", "flow") or not op.get("key"):
                raise StoreError("a mark names a discovered stock or flow by key")
            box = L["marks"]["expected" if kind in ("expect", "unexpect") else "ignored"]
            if kind in ("expect", "ignore"):
                box[op["key"]] = {"part": op["part"], "key": op["key"], "provenance": prov, "change": ctx["change"]}
            elif box.pop(op["key"], None) is None:
                raise StoreError(f"{op['key']} is not marked")
            return op["part"], op["key"]
        if kind == "link":
            only(op, ("id", "part", "found_as"))
            part = op.get("part") or ("stock" if op.get("id") in L["stocks"] else "effect")
            el = self._el(L, part, op.get("id"))
            el["found_as"] = op.get("found_as")
            return part, el["id"]
        if kind == "declare_event":
            only(op, ("type", "description"))
            L["events"][op["type"]] = {"type": op["type"], "description": op.get("description"), "implicit": False,
                                       "provenance": prov, "change": ctx["change"]}
            return "event", op["type"]
        if kind == "record_measurement":
            only(op, ("part", "id", "options", "chosen"))
            el = self._el(L, op.get("part") or "stock", op.get("id"))
            for o in op.get("options") or []:
                if extra := set(o) - set(OPTION):
                    raise StoreError(f"an option's unknown fields: {sorted(extra)}")
            el["measure"] = {"options": op.get("options") or [], "chosen": op.get("chosen"), "change": ctx["change"]}
            return el["part"], el["id"]
        if kind == "propose_machine":
            m = {k: v for k, v in op.items() if k not in ("op", "guessed")}
            check_envelope({"kind": "universal", "managed": "orchestra", "engagement": {"mode": "ad hoc"}, **m})
            if m["id"] in L["machines"]:
                raise StoreError(f"machine {m['id']} is already in the declared layer: revise it")
            L["machines"][m["id"]] = {"title": m["id"], "description": None, "tags": [], "kind": "universal", "managed": "orchestra",
                                      "engagement": {"mode": "ad hoc"}, "receives": [], "emits": [], "reads": [], "spec": {},
                                      **m, **new, "state": "proposed", "aliases": [], "expected_effect": None, "latest": None}
            self._implicit(L, L["machines"][m["id"]], ctx)
            return "machine", m["id"]
        if kind == "revise_machine":
            only(op, ("id", "set", "add", "remove"))
            m = self._el(L, "machine", op.get("id"))
            for what in ("add", "remove"):
                if bad := set(op.get(what) or {}) - set(LISTS):
                    raise StoreError(f"{what} takes the list fields ({', '.join(LISTS)}), not {sorted(bad)}")
            if "id" in (op.get("set") or {}):
                raise StoreError("a machine's id is not revised")
            trial = {k: copy.deepcopy(m[k]) for k in ENVELOPE}
            _apply(trial, op.get("set") or {}, op.get("add") or {}, op.get("remove") or {})
            check_envelope(trial)
            self._revise(m, op, ctx)
            self._implicit(L, m, ctx)
            return "machine", m["id"]
        if kind == "set_expected_effect":
            only(op, ("machine", "edits", "horizon_s"))
            m = self._el(L, "machine", op.get("machine"))
            prev = m.get("expected_effect") or {}
            m["expected_effect"] = {"edits": op.get("edits") or [], "horizon_s": op.get("horizon_s"),
                                    "prediction": prev.get("prediction"), "change": ctx["change"]}
            return "machine", m["id"]
        if kind == "record_prediction":
            only(op, ("machine", "prediction"))
            m = self._el(L, "machine", op.get("machine"))
            if not m.get("expected_effect"):
                raise StoreError(f"machine {m['id']} has no expected effect to link a prediction to")
            m["expected_effect"]["prediction"] = op.get("prediction")
            return "machine", m["id"]
        if kind in ("confirm", "discard"):
            only(op, ("part", "id", "change"))
            part, eid = op.get("part"), op.get("id")
            el = self._el(L, part, eid)
            if op.get("change"):
                p = next((x for x in el.get("pending") or [] if x["change"] == op["change"]), None)
                if p is None:
                    raise StoreError(f"{eid} has no pending change {op['change']}")
                el["pending"] = [x for x in el["pending"] if x is not p]
                if kind == "confirm":
                    diff = _apply(el, p["set"], p["add"], p["remove"])
                    el["latest"] = {"change": op["change"], "at": prov["at"], "confirmed_by": ctx["change"], "quotes": [], **diff}
                return part, eid
            if el.get("state") != "proposed":
                raise StoreError(f"{part} {eid} is {el.get('state')}, not proposed")
            if kind == "confirm":
                el.update(state="declared", guessed=[], confirmed={"by": prov.get("author"), "change": ctx["change"]})
            else:
                del L[GROUP[part]][eid]
            return part, eid
        raise StoreError(f"unknown op {kind!r}")

    @staticmethod
    def _el(L: dict, part: str, eid) -> dict:
        el = L.get(GROUP.get(part, ""), {}).get(eid)
        if el is None:
            raise StoreError(f"there is no {part} {eid!r} in the declared layer")
        return el

    @staticmethod
    def _implicit(L: dict, m: dict, ctx: dict) -> None:
        for t in [*m.get("receives", []), *m.get("emits", [])]:
            L["events"].setdefault(t, {"type": t, "description": None, "implicit": True, "provenance": ctx["provenance"],
                                       "change": ctx["change"]})

    @staticmethod
    def _revise(m: dict, op: dict, ctx: dict) -> None:
        prov, cid = ctx["provenance"], ctx["change"]
        set_, add, remove = (op.get(k) or {} for k in ("set", "add", "remove"))
        if not (prov.get("author") == "owner" and prov.get("quote")):
            m["pending"].append({"change": cid, "set": set_, "add": add, "remove": remove,
                                 "guessed": list(op.get("guessed") or []), "provenance": prov})
            return
        diff = _apply(m, set_, add, remove)
        touched = set(set_) | set(add) | set(remove)
        m["guessed"] = [g for g in m.get("guessed") or [] if g.split("[", 1)[0] not in touched]
        m["latest"] = {"change": cid, "at": prov["at"], "quotes": [prov["quote"]], **diff}

    def design(self) -> dict:
        cur = self.read()
        L = cur["layer"]
        elements, open_items = [], []
        for part, group in GROUP.items():
            if part == "event":
                continue
            for eid, el in L[group].items():
                row = {"part": part, "id": eid, "state": el.get("state")}
                for k in ("found_as", "guessed"):
                    if el.get(k):
                        row[k] = el[k]
                if el.get("pending"):
                    row["pending"] = [p["change"] for p in el["pending"]]
                elements.append(row)
                if el.get("state") == "proposed":
                    open_items.append({"what": "unconfirmed", "part": part, "id": eid})
                open_items += [{"what": "pending change", "part": part, "id": eid, "change": c} for c in row.get("pending", [])]
        last = [c for e in self.journal()[-20:] for c in e.get("changes", [])][-20:]
        return {"version": cur["version"], "elements": elements, "open": open_items, "last_changes": last, "stabilised": None}


def _apply(el: dict, set_: dict, add: dict, remove: dict) -> dict:
    """As Orchestra's ``revise._apply``: change ``el`` in place; the difference {added, removed, before}."""
    added, removed, before = {}, {}, {}
    for k, v in set_.items():
        if k == "spec" and isinstance(el.get("spec"), dict):
            for sk in dict.fromkeys([*el["spec"], *v]):
                if el["spec"].get(sk) != v.get(sk):
                    before[sk] = copy.deepcopy(el["spec"].get(sk))
        elif el.get(k) != v:
            before[k] = copy.deepcopy(el.get(k))
        el[k] = v
    for k, items in add.items():
        cur = list(el.get(k) or [])
        if new := [x for x in items if x not in cur]:
            el[k] = cur + new
            added[k] = new
    for k, items in remove.items():
        cur = list(el.get(k) or [])
        if gone := [x for x in items if x in cur]:
            el[k] = [x for x in cur if x not in gone]
            removed[k] = gone
    return {"added": added, "removed": removed, "before": before}
