# Skill: valve

Use when: the owner wants items filtered, sorted or routed by a judgement ("filter tickets that don't need on-call", "only page me for real outages"): a decision step that asks a model (`declare_valve`).

- A valve is a program step of a workflow that asks a model the owner has access to which way each
  item flows; its label is the step's outcome. Never write a filter as code or as a ticket: declare
  it with `declare_valve`. A rule the vocabulary can say (a start's `data`, `not`, a field's value)
  needs no model: use the definition's words for it, and a valve only for a judgement.
- What you need from the owner, and nothing you can read yourself: the API (`openai`, `anthropic`,
  or a `classify` endpoint answering `{label, score}`), its address, the model, the token file's
  full path (chmod 600, never the token) and the daily cap. Ask for the ones missing in one sentence.
- Choose the inputs from the item: `list_workflows` and `explain_item` show which fields and fact
  types an item has. Name only what the decision needs (a ticket's `summary` and `priority`; the
  last few `jira.issue.commented` facts' `body` when the reader relays it). A field must be on the
  item: a start's `fields` names it; if it is not, add it to the start with `declare_workflow` first.
- `labels` are the outcomes (`page`, `queue`, `ignore`); `routes` sends each to a step or to the end
  (`null`); a label left out goes on to the step the valve was put before. `fallback` is `ask` by
  default, which goes to the workflow's `person: owner` step; with none, the item waits at the
  valve's step and the planner is told. Set `min_confidence` (0.7 is a fair start).
- [[act]]Under `ask = "outside"` declare it yourself, with your reason: it is local, and asks
  nothing.[[/act]][[ask]]Ask in one sentence, naming the model and the daily cap, and declare it on
  their quoted yes.[[/ask]] Then say the answer's `say` line to the owner as it is: it states the
  daily cap (the calls spend their money), and repeat its `token` line when there is one.
- The Items drawer shows each item's decision (`valve_<id>`: label, score, reason, cached); the
  Dynamics page has `valve_decisions_total{valve, outcome}`. When the owner asks why an item went
  one way, read it with `explain_item`; never guess the model's reason.
