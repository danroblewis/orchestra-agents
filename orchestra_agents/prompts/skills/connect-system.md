# Skill: connect-system

Use when: the owner asks to connect a system with items ("connect our Jira", PagerDuty, Slack channels, Jenkins, GitHub).

**Connecting a system with items** ("connect our Jira", "bring in PagerDuty", Slack channels,
   Jenkins): your first call is `integrate_system`, before any guide, with the system, what they
   want, the scope they named (`settings`), the stock it fills and their quote. The bridges are a
   library: it scaffolds a script from a kit into the owner's repository (`.orchestra/machines/`;
   the state directory only when they said the repository must stay clean), or configures the one
   already there from that kit (several: name each), tests it and declares
   the system; a failed test or a system with no template [[tasking]]goes to the Coordinator as work[[/tasking]][[no-tasking]]is not handed on here (code work is not handed on here; say so)[[/no-tasking]].
   Nothing reads it yet. Put its `ask_owner` to the owner: their yes, where the token is (a
   variable's *name* or a file's path, never the token), and what it says is missing. On their
   yes, quoted, `approve_integration` with what they gave. Your guide's "Connecting a system" has
   the rest.
