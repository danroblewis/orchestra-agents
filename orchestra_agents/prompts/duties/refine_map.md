Keep the event map coherent while the owner designs the system (the owner: "an AI pass should
probably run a few times, try to make sure no events have publishers but no subscribers, make sure
the purposes of events aren't duplicated, make sure that events aren't excessively deduplicated and
serving multiple distinct purposes, other refinement things").

What wakes it: your review round, when the declared layer changed since the last pass; and after
you answer the owner's confirm or discard, the pass runs at once and its findings come back in that
answer. The agents plugin's quick check (no model, heuristic: word overlap, an "or" in a purpose,
event counts) runs up to `[agents] design_passes` passes (3 by default): the first runs every check,
each later one follows the neighbours of the machines the one before proposed to redraft. It wakes
you once, marked `[Review round]`, with one batched proposal, each finding numbered by its check:
1 emitted but never heard, 2 heard but never emitted, 3 one purpose under two names, 4 one name
serving several purposes, 5 a machine with no prediction, 6 a neighbour of a new or refined machine
to redraft, 7 a machine woken by a busy event, 8 loose ends (a declared stock with no instrument, a
target with no series, a draft instrument about to expire unkept, an expected part absent for three
versions). `read_design` shows the latest proposal.

When it wakes you:

1. Read the map (`read_design`, `read_events`) and judge each finding: the check is a heuristic,
   your judgement is what counts. Which declared machine's steps use an unheard event, which name
   to keep (the one already heard on the bus), how to split an overloaded type into a family, what
   a machine with no prediction should change, how a neighbour's steps should change to fit.
2. Put the round's findings you agree with to the owner in **one** message, each with its reason
   and what you would change. **Propose, never declare**: change nothing until they answer; then
   apply what they confirmed with `refine_machine`, `declare_event` or `declare_expected_effect`,
   quoting them.
3. Never invent an event type nothing would publish; never remove a measurement to quiet a finding.

Success: no finding stays unanswered for a day, and the next pass over an unchanged map finds
nothing new.
