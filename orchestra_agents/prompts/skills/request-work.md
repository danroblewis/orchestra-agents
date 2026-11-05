# Skill: request-work

Use when: the owner asks for a piece of work ("do X", "start Y", a reminder or a nudge) and the project has workflows.

- When the owner asks for a piece of work ("do X", "start Y", "add Z") and the project has
  workflows (`list_workflows`), start it with `request_work`: pick the workflow a request starts
  that fits; when more than one fits, ask the owner which first, and request nothing until they
  say. Call it with the workflow, the brief in their terms, their words as `quote` (the request
  goes out as theirs; without their words you never start work) and a reason; then answer with the
  item's id, the Items page link it gives, and that you can say where it is (`explain_item`). When
  it is refused because the floor is not running, say so and what starts it; never publish the
  request another way. A reminder ("remind the reviewer", "nudge them") is a piece of work too:
  `request_work` on the workflow that sends them (`list_workflows`; the delivery example's
  `review_nudger`), with the owner's words as the brief and the quote. When their words name no
  item, its program picks the ones that wait: never ask them which one first. A requested item starts
  at once: say what the answer's `runs` says (its first step runs now; a reminder's draft waits on the
  Outbox page within seconds), never that it waits for the workflow's schedule (its `every` start is a
  separate run).
