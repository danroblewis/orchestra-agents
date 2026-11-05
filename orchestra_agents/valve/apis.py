"""The three API shapes a valve may ask (``api``): each builds its request and reads its answer
as ``(label, score, reason, usage)``, or says why the answer is not one.

- ``openai``: ``POST {base}/v1/chat/completions`` with ``response_format`` a strict JSON schema
  (the label an enum of the declared labels); the answer is the message's JSON content, or a forced
  function call's arguments when the server answers with one.
- ``anthropic``: ``POST {base}/v1/messages`` with one tool, ``decide``, and ``tool_choice`` forcing
  it; the answer is that tool use's input.
- ``classify``: ``POST {base}{path}`` with the inputs (and the labels and question) under the
  declared request field names; the answer is ``{label, score}`` (and a reason) under the declared
  response field names.

The token goes in a header only (``Authorization: Bearer``, ``x-api-key``, or the classify
endpoint's declared header), never in a body or a message."""

from __future__ import annotations

import json

from .declaration import Valve

ANTHROPIC_VERSION = "2023-06-01"
TOOL = "decide"


def schema(v: Valve) -> dict:
    return {"type": "object", "additionalProperties": False, "required": ["label", "score", "reason"],
            "properties": {"label": {"type": "string", "enum": list(v.labels)},
                           "score": {"type": "number", "description": "your confidence in the label, 0 to 1"},
                           "reason": {"type": "string", "description": "why, in one short sentence"}}}


def system(v: Valve) -> str:
    return ("You decide one question about one work item, from the inputs given and nothing else. Answer with one of "
            f"the labels ({', '.join(v.labels)}), your confidence in it from 0 to 1, and the reason in one short "
            "sentence. If the inputs do not decide it, give a low confidence.")


def user(v: Valve, inputs: dict) -> str:
    return f"{v.question}\n\nInputs (JSON):\n{json.dumps(inputs, ensure_ascii=False, sort_keys=True)}"


def request(v: Valve, inputs: dict, token: str) -> tuple[str, dict, dict]:
    """The URL, the body and the headers."""
    if v.api == "openai":
        body = {"model": v.model, "max_tokens": v.max_tokens, "temperature": 0,
                "messages": [{"role": "system", "content": system(v)}, {"role": "user", "content": user(v, inputs)}],
                "response_format": {"type": "json_schema",
                                    "json_schema": {"name": TOOL, "strict": True, "schema": schema(v)}}}
        return f"{v.base_url}/v1/chat/completions", body, {"Authorization": f"Bearer {token}"}
    if v.api == "anthropic":
        body = {"model": v.model, "max_tokens": v.max_tokens, "system": system(v),
                "messages": [{"role": "user", "content": user(v, inputs)}],
                "tools": [{"name": TOOL, "description": "Give the decision.", "input_schema": schema(v)}],
                "tool_choice": {"type": "tool", "name": TOOL}}
        return f"{v.base_url}/v1/messages", body, {"x-api-key": token, "anthropic-version": ANTHROPIC_VERSION}
    c = v.classify
    req = c["request"]
    body = {req["inputs"]: inputs, req["labels"]: list(v.labels), req["question"]: v.question}
    headers = {c["auth_header"]: f"{c['auth_prefix']}{token}"} if token else {}
    return f"{v.base_url}{c['path']}", body, headers


def _answer_of(body: dict, v: Valve) -> dict | None:
    if v.api == "openai":
        try:
            msg = body["choices"][0]["message"]
        except (KeyError, IndexError, TypeError):
            return None
        calls = msg.get("tool_calls") or []
        text = calls[0].get("function", {}).get("arguments") if calls and isinstance(calls[0], dict) else msg.get("content")
        try:
            got = json.loads(text) if isinstance(text, str) else None
        except ValueError:
            return None
        return got if isinstance(got, dict) else None
    if v.api == "anthropic":
        for block in body.get("content") or []:
            if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("name") == TOOL:
                return block.get("input") if isinstance(block.get("input"), dict) else None
        return None
    r = v.classify["response"]
    return {"label": body.get(r["label"]), "score": body.get(r["score"]), "reason": body.get(r["reason"])}


def usage(body: dict) -> dict:
    """Tokens, and the cost when the API reports one (some gateways do: ``usage.cost``, ``cost``)."""
    u = body.get("usage") if isinstance(body.get("usage"), dict) else {}
    out = {k: u[k] for k in ("input_tokens", "output_tokens", "prompt_tokens", "completion_tokens")
           if isinstance(u.get(k), int | float)}
    cost = u.get("cost", body.get("cost"))
    if isinstance(cost, int | float) and not isinstance(cost, bool):
        out["cost"] = cost
    return out


def read(v: Valve, body: dict) -> tuple[tuple[str, float, str] | None, str]:
    """(label, score, reason), or None and why the answer is not one. The label is as the model
    gave it, checked against the labels by the caller."""
    got = _answer_of(body, v)
    if got is None:
        return None, "answered without a decision in the expected shape"
    label, score, reason = got.get("label"), got.get("score"), got.get("reason")
    if not isinstance(label, str) or not label:
        return None, "answered without a label"
    if isinstance(score, bool) or not isinstance(score, int | float):
        score = 0.0                     # a label with no confidence is as good as an unsure one
    reason = " ".join(str(reason or "").split())[:200]
    return (label.strip(), max(0.0, min(1.0, float(score))), reason), ""
