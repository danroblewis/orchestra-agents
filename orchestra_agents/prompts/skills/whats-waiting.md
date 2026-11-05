# Skill: whats-waiting

Use when: the owner asks what is stuck, what is waiting or what needs them, or whether a machine runs.

- When the owner asks what is stuck, what is waiting or what needs them, and the project has
  workflows, read `list_items` first (every open item, its step, why it waits and on whom), then
  `explain_item` for any you must explain. Answer item by item with its id and the fields that name
  it outside (a ticket's key, a pull request's number), where it waits and why: a wait's `why` (an
  attention's own reason, "not past step ci within 30 min") is the cause to report, as it says;
  never say the attention events carry no reason. "What needs me" also
  names every machine `list_machines` lists as disabled, with the reason it gave (a Slack
  notifier whose token lacks a scope does no work until that is fixed). A machine a plugin proposed
  is not something that needs them: each machine's `running` (`list_machines`) says whether it runs,
  and one heard on the bus, or a workflow's program, runs whether or not it is confirmed; say a
  machine does not run only when `running` says it is not heard and no workflow runs it.
  An item waiting at a step on an outside system or an ad-hoc machine (its wait's `for` names
  the events that end it) is waiting for that event, not stuck on a quiet machine. A machine's
  state is what the tools returned: say what they returned; when a tool did not answer, say that.
  `gone_quiet` (the core's mark) is that it was not heard for a while, not that it stopped: never
  advise restarting a machine, or say it needs starting or is down, unless `list_processes` shows
  its process stopped.
