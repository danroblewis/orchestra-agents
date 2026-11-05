# Skill: page-states

Use when: the owner asks where to look on a page or what a page's state means (ghost, expected, new, unconfirmed, never heard, waiting).

**The pages' states**, when they draw the declarations, so you can say where to look and what a
state means when the owner asks:

- **Ghost** (Dynamics): a declared stock or effect discovery has not found, drawn darker with a
  dashed outline; tagged "expected · declared by you" (or "· not in vN" once there is an earlier
  version) and "no series yet" or "not measured". It stays a ghost until a series measures it and
  a discovery finds it, or until you `link` it to the discovered stock ("may be: … · ask the
  Supervisor to link" is the page suggesting one).
- **Expected** (Dynamics): the Expected group of the Stocks table lists every declared part; a
  found one is tagged "expected", and the gold pin marks a discovered part the owner expects
  (`mark_expected`, or their click). Its drawer's "How to measure it" lists your options: the
  recording one, chosen as it starts, "recording · draft" with its sparkline, script path and
  entry, then kept.
- **New in vN** (Dynamics): with "changes since vN" on (the default), a part discovery found that
  the earlier version did not have is ringed and tagged "new in vN", a removed one struck through
  as a ghost; the Changes panel lists them with expect and ignore. A "new" part is not expected
  until someone marks it.
- **Unconfirmed** (Machines, also on a proposed stock or flow): a proposed machine is a dashed node,
  "unconfirmed · added by you" when the owner named it, else "unconfirmed"; its panel holds your
  draft with each guess tagged "a guess". "Unconfirmed" is the declared layer's state, not a switch:
  nothing runs only for a machine never heard on the bus and with no workflow behind it. One a plugin
  proposed that is heard is running (`list_machines` and `read_design`: "running: heard <when>,
  proposed by the workflows plugin"), and a program a workflow's step names "runs as the workflow's
  program (<workflow>)" in the Workflows plugin's program host, confirmed or not (`list_processes`
  lists the launcher's processes only, so it is not there). Never tell the owner such a machine does
  not run, needs confirming to run, or that its program still needs writing. A refinement is marked "+ added just
  now", "− removed" or "changed just now · was …". On a confirmed machine, a refinement is "a change
  … waits for you", with its own confirm and discard.
- **Declared · never heard** (Machines): confirmed, solid outline, but nothing has
  published under its source yet. **Measured, never heard**: an instrument attached to it records
  series but the bus has never carried its events; normal for an external machine known by its
  measurements, a question for one that should publish. "Attached, nothing measured" and
  "declared, neither heard nor measured" are the same question one step earlier.
- **Asked the Supervisor · waiting** (Machines): the owner clicked confirm, discard or the switch;
  the request is on the bus and you are woken with it. Answer it at once with the call it names
  and its `request`; the badge goes when you do.

## The pages

The pages (Orchestra's Core, Dashboard, Machines and Dynamics, and the plugins' pages) show the
system; the only controls on them are the task system's operator controls over its own queue
(pausing pickup, a capacity ceiling, stopping the work on a ticket). No page starts, stops or
restarts a program, or changes the configuration. When you tell the human how something happens,
name only what exists: your own tools, a ticket, or a command they run themselves (`orchestra
run` in the repository). Never suggest any other control on a page.
