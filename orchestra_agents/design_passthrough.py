"""The design tools that pass one op of Orchestra's declared layer through (``intended_api``), each
with its provenance and a reason, and nothing of their own (machine-design.md §2, P4's table):

- ``propose_attachment`` and ``revise_attachment``: an instrument's series measure a declared
  machine (instruments-on-machines.md: ``machine``, ``instrument``, its match as ``prefix`` and
  ``labels``, ``instance_label``);
  declared provenance, never a sign the machine runs. There is no ``find_machine_series``: the
  attachment takes the match explicitly (read the store with ``list_metrics`` first);
- ``unexpect`` and ``unignore``: a discovered stock's or flow's expected or ignored mark taken off
  (the pages say "to list it again, ask …");
- ``alias``: a declared machine heard under another name on the bus (the Machines panel's "heard
  under another name? … join them")."""

from __future__ import annotations

from .config import Project
from .design import answer, apply, page_line
from .store import AgentStore, StoreError


def _match(a: dict) -> dict | None:
    """The attachment's match: ``prefix`` and ``labels`` (``label=value`` strings), or none given."""
    prefix, labels = (a.get("prefix") or "").strip(), a.get("labels") or []
    if not prefix and not labels:
        return None
    out: dict = {"prefix": prefix} if prefix else {}
    for lv in labels if isinstance(labels, list) else [labels]:
        k, sep, v = str(lv).partition("=")
        if not sep or not k.strip():
            raise StoreError(f"labels: {lv!r} is not label=value (job=postgres)")
        out.setdefault("labels", {})[k.strip()] = v.strip()
    return out


def propose_attachment(project: Project, store: AgentStore, a: dict, prov: dict) -> dict:
    mid, inst = (a.get("machine") or "").strip(), (a.get("instrument") or "").strip()
    if not mid or not inst:
        raise StoreError("name the declared machine (machine) and the instrument whose series measure it (instrument)")
    op = {"op": "propose_attachment", "machine": mid, "instrument": inst}
    if (m := _match(a)) is not None:
        op["match"] = m
    if a.get("instance_label"):
        op["instance_label"] = str(a["instance_label"])
    done = apply(project, store, [op], prov)
    return answer(project, done, [f"{inst}'s series proposed as measuring {mid}: declared provenance, never a sign it runs",
                                  page_line(project, "the Machines page shows it under the machine's \"Measured by\", unconfirmed")])


def revise_attachment(project: Project, store: AgentStore, a: dict, prov: dict) -> dict:
    aid = (a.get("attachment") or "").strip()
    if not aid:
        raise StoreError("name the attachment (attachment: its id, as read_design gives it)")
    set_ = {k: a[k] for k in ("instrument", "instance_label") if a.get(k)}
    if (m := _match(a)) is not None:
        set_["match"] = m
    if not set_:
        raise StoreError("say what changes: instrument, match or instance_label")
    done = apply(project, store, [{"op": "revise_attachment", "id": aid, "set": set_}], prov)
    return answer(project, done, [f"attachment {aid}: {', '.join(sorted(set_))} revised",
                                  page_line(project, "the Machines page marks the change on its \"Measured by\"")])


def _unmark(project: Project, store: AgentStore, a: dict, prov: dict, op: str, what: str) -> dict:
    part, key = a.get("part") or "stock", (a.get("key") or "").strip()
    if part not in ("stock", "flow") or not key:
        raise StoreError("name the discovered part: part (stock or flow) and its key, as read_design gives it")
    done = apply(project, store, [{"op": op, "part": part, "key": key}], prov)
    return answer(project, done, [f"{part} {key}: {what}", page_line(project, "the Dynamics page draws it as discovery found it")])


def unexpect(project: Project, store: AgentStore, a: dict, prov: dict) -> dict:
    return _unmark(project, store, a, prov, "unexpect", "no longer marked expected")


def unignore(project: Project, store: AgentStore, a: dict, prov: dict) -> dict:
    return _unmark(project, store, a, prov, "unignore", "listed again among the suggestions")


def alias(project: Project, store: AgentStore, a: dict, prov: dict) -> dict:
    mid, observed = (a.get("machine") or "").strip(), (a.get("observed") or "").strip()
    if not mid or not observed:
        raise StoreError("name the declared machine (machine) and the name it is heard under on the bus (observed)")
    done = apply(project, store, [{"op": "alias", "machine": mid, "observed": observed}], prov)
    return answer(project, done, [f"{mid} is heard as {observed}: joined",
                                  page_line(project, "the Machines page shows one machine, heard under its alias")])


COMMANDS = {"propose_attachment": propose_attachment, "revise_attachment": revise_attachment, "unexpect": unexpect,
            "unignore": unignore, "alias": alias}
