"""A stand-in for Orchestra's declared layer (``intended-model.md`` §5–§6.3; Orchestra's
``orchestra/sd/intended/``, task C1), used only while the installed Orchestra has none
(``intended_api.py``, the seam; delete this file, ``intended_standin_ops.py``,
``intended_standin_consent.py`` and ``intended_carry.py`` once every project has Orchestra's).

It takes the same ops in the same shapes, with the same rules, as Orchestra's at C1 fe185af0
(``intended_standin_ops.py``, ``intended_standin_consent.py``): all of a call or none, change ids
``c<seq>.<i>``, ``read(version) -> {version, at, layer}``, ``apply -> {version, changes: [{op, part,
id, change}]}``; provenance as Orchestra's store takes it (``via`` and ``at`` refused when given:
the store sets them). It keeps each version (``d0001.json`` …) and the journal (``journal.jsonl``:
each call's provenance, its ops stamped with their change as Orchestra's are, and the ops as sent,
``sent``, from which ``intended_carry`` replays them into Orchestra's once that is installed) in the
agents' state directory under a file lock, so the MCP server's process and the host's see the same
thing. It derives nothing Orchestra derives on read (presence, recording, verdicts), and no page
draws it.

**Requests are noted, as Orchestra's web server notes them** (``requests.jsonl``,
``note_request``): a call answering the owner's request cites one noted here and not answered
yet; a bare event on the bus from source ``owner`` is not one. No page publishes a request to the
stand-in (none draws it), so only the tests note one."""

from __future__ import annotations

import copy
import fcntl
import json
import re
import time
from contextlib import contextmanager
from pathlib import Path

from .config import Project
from .intended_standin_ops import GROUP, apply_op, empty
from .store import StoreError

PROVENANCE = ("author", "source", "turn", "message", "quote", "reason", "request")
NAME = re.compile(r"^d(\d{4,})\.json$")
REQUESTED = "orchestra.intent.requested"
CONTROLS = ("confirm", "discard", "switch_kind")


def checked_provenance(p: dict | None) -> dict:
    """As Orchestra's ``checks.provenance`` for a plugin's call."""
    p = dict(p or {})
    if "via" in p or "at" in p:
        raise StoreError("provenance: via and at are set by the store, never given")
    if extra := sorted(set(p) - set(PROVENANCE)):
        raise StoreError(f"provenance: unknown field{'s' if len(extra) > 1 else ''} {', '.join(extra)}")
    author = p.get("author") or p.get("source")
    if not isinstance(author, str) or not author.strip():
        raise StoreError("provenance needs an author: owner, inference, or the plugin's source")
    if author == "owner" and not p.get("quote"):
        raise StoreError("a plugin's author: owner is backed by the owner's words: give the quote")
    return {**{k: v for k, v in p.items() if v is not None}, "author": author, "via": "plugin"}


class StandIn:
    def __init__(self, project: Project):
        self.project = project
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

    def _numbers(self) -> list[int]:
        try:
            return sorted(int(m.group(1)) for p in self.dir.iterdir() if (m := NAME.match(p.name)))
        except OSError:
            return []

    def read(self, version: int | None = None) -> dict:
        nums = self._numbers()
        n = (nums[-1] if nums else 0) if version is None else version
        if not n:
            return {"version": 0, "at": None, "layer": empty()}
        p = self.dir / f"d{n:04d}.json"
        if not p.exists():
            raise StoreError(f"there is no declared version d{n}")
        d = json.loads(p.read_text(encoding="utf-8"))
        return {"version": n, "at": d.get("at"), "layer": {**empty(), **(d.get("layer") or {})}}

    def journal(self, after: int = 0) -> list[dict]:
        p = self.dir / "journal.jsonl"
        lines = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()] if p.exists() else []
        return [e for e in lines if e.get("seq", 0) > after]

    def note_request(self, body: dict) -> dict:
        """Publish the owner's request (``{control, part, id, change, from}``) on the bus as the owner
        and note it, as Orchestra's web server does (its ``requests.publish``); the event."""
        from .events import Events
        if body.get("control") not in CONTROLS or body.get("part") not in ("machine", "attachment"):
            raise StoreError(f"a request is {', '.join(CONTROLS)} on a machine or an attachment")
        el = self.read()["layer"][GROUP[body["part"]]].get(body.get("id"))
        if el is None:
            raise StoreError(f"there is no declared {body['part']} {body.get('id')!r}")
        data = {k: body.get(k) for k in ("control", "part", "id", "change", "from")}
        ev = Events.for_project(self.project).publish(REQUESTED, data, source="owner", subject=f"{data['part']}:{data['id']}")
        with self._locked(), (self.dir / "requests.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps({"event": ev["id"], "at": time.time(), **data}) + "\n")
        return ev

    def _noted(self) -> list[dict]:
        p = self.dir / "requests.jsonl"
        return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()] if p.exists() else []

    def open_requests(self) -> dict[str, dict]:
        """Every noted request no call has answered yet, by its event id."""
        answered = {(e.get("provenance") or {}).get("request") for e in self.journal()}
        return {r["event"]: r for r in self._noted() if r.get("event") and r["event"] not in answered}

    def apply(self, ops: list[dict], provenance: dict) -> dict:
        if not isinstance(ops, list) or not ops:
            raise StoreError("apply takes a list of ops")
        prov = checked_provenance(provenance)
        with self._locked():
            cur = self.read()
            asked = self.open_requests() if prov.get("request") else {}
            prov["at"] = time.time()
            layer = copy.deepcopy(cur["layer"])
            seq = cur["version"] + 1
            changes = []
            for i, op in enumerate(ops):
                cid = f"c{seq}.{i}"
                try:
                    part, eid = apply_op(layer, op, {"provenance": prov, "change": cid, "requests": asked})
                except StoreError as e:
                    raise StoreError(f"op {i} ({op.get('op') if isinstance(op, dict) else op}): {e}; nothing was applied") from None
                changes.append({"op": op.get("op"), "part": part, "id": eid, "change": cid})
            tmp = self.dir / f"d{seq:04d}.json.tmp"
            tmp.write_text(json.dumps({"version": seq, "at": prov["at"], "layer": layer}), encoding="utf-8")
            tmp.replace(self.dir / f"d{seq:04d}.json")
            stamped = [{**op, "change": c["change"]} for op, c in zip(ops, changes, strict=True)]
            with (self.dir / "journal.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps({"seq": seq, "at": prov["at"], "provenance": prov, "ops": stamped, "sent": ops,
                                    "changes": changes}) + "\n")
        return {"version": seq, "changes": changes}

    def _unanswered(self, journal: list[dict]) -> list[dict]:
        """The latest noted request per element that no call answered (Orchestra's ``unanswered``)."""
        answered = {(e.get("provenance") or {}).get("request") for e in journal}
        latest = {(r["part"], r["id"]): r for r in self._noted()}
        return [{"what": "unanswered request", "part": p, "id": i, "control": r.get("control"), "event": r["event"]}
                for (p, i), r in latest.items() if r["event"] not in answered]

    def design(self) -> dict:
        from .design_machines import publishable
        from .intended_api import Keys, current_model
        cur = self.read()
        L = cur["layer"]
        journal = self.journal()
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
        for key, mk in L["marks"]["expected"].items():
            elements.append({"part": mk["part"], "key": key, "state": "expected"})
        open_items += self._unanswered(journal)
        keys = Keys(current_model(self.project)) if L["machines"] else None
        for mid, m in L["machines"].items():
            for e in (m.get("expected_effect") or {}).get("edits") or []:
                if (why := publishable(self.project, L, mid, e, keys=keys, core={})[1]) is not None:
                    open_items.append({"what": "unpublishable draft", "part": "machine", "id": mid, "edit": e, "why": why})
        last = [c for e in journal[-20:] for c in e.get("changes", [])][-20:]
        return {"version": cur["version"], "elements": elements, "open": open_items, "last_changes": last, "stabilised": None}
