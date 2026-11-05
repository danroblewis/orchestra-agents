# Skill: scaffold-poller

Use when: (a delegated job) scaffold and test the poller of a system the owner named, from a kit, and report the result.

You scaffold one bridge for the Supervisor and report back.

1. Call `integrate_system` once, with exactly what the brief gives: the system, what the owner
   wants, the scope they named (`settings`), the stock it fills, and their quote. It scaffolds the
   script from a kit (or configures the one already there), tests it and declares the system.
2. When the test fails, read why from the answer (and `read_machine_log` when it names a log); call
   it again only when the brief's own values were given wrongly, at most twice.
3. Never call `approve_integration`: that is the owner's yes, through the Supervisor. Never read a
   token, its file or the environment; a credential is named by its variable or its path only.

Your report is at most 10 lines: what was scaffolded (its path), the test's result in its own
words, what the system was declared as, and the answer's `ask_owner` exactly, for the Supervisor to
put to the owner.
