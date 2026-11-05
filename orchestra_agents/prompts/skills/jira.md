# Skill: jira

Use when: the owner asks to follow Jira tickets (a poller's `ticket` items), or a workflow should track the tickets already under way.

- Connecting Jira is `integrate_system` first (the `connect-system` skill), with the scope the
  owner named (projects, a JQL filter) in `settings` and the token by reference (a variable's name
  or a file's path, never the token). Jira here is read-only: nothing is written to it except
  through an Outbox draft the owner approves.
- A workflow that follows tickets from a poller (Jira's `ticket`) also tracks the tickets already
  under way when tracking begins: a ticket assigned before the poller began is never relayed as
  assigned. Declare it with `adopt` (the poller's types that report a ticket now, the fields that
  say it is under way, the step each status implies: the agents guide's "Tickets already under
  way"), never by publishing a ticket's events yourself; then say what its answer's `adopt` says:
  how many were adopted, each at its step, and that any the poller first reports later is adopted
  then.
- A ticket's pull requests are related to it by the relator the Workflows plugin ships (the
  `github` skill), never by a goal re-declared without the link.
