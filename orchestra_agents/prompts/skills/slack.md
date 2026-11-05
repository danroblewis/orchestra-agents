# Skill: slack

Use when: the owner asks to follow Slack (or Discord) channels, or which bot threads count.

- Connecting Slack is `integrate_system` first (the `connect-system` skill), with the channels the
  owner named in `settings` and the token by reference, never the token. A notifier whose token
  lacks a scope does no work until that is fixed: `list_machines` says it is disabled, with why.
- A chat queue (Slack's, Discord's) leaves out a bot's threads only where the owner's words say so.
  In a channel the owner named for incidents or alerts (its name or their words), the alert bot's
  threads are the incidents: start them as alerts (their own workflow, items `alert`, the policy the
  owner gave that channel), and count the other channels' bot threads with the people's. Your
  answer at setup says which channels count bot threads and why (`declare_workflow`'s
  `bot_threads`), in one line.
- Anything sent to a channel as the owner is an Outbox draft they approve; you never change an
  Outbox kind to `auto`.
