# Skill: incidents

Use when: an alert or an incident comes in ("we got a page", PagerDuty).

- When an alert comes in ("we got a page", an incident), read `read_events` with types
  `pagerduty.` (every PagerDuty type), then name the incident, its service, and whether its source
  announced itself a fake (`list_machines`: its `fake` note). Then read `list_items`: an incident
  on a service a step waits on holds the items at that step: name them (`waiting_outside`, each
  with its ticket's key and pull request). No tool says which service a step's system runs on, so
  never say the work is unaffected while an item waits at such a step: name it as one the
  incident may hold.
