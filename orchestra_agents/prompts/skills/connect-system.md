# Skill: connect-system

Use when: the owner asks to connect a system with items ("connect our Jira", PagerDuty, Slack channels, Jenkins, GitHub).

**Delegate the scaffolding.** Scaffolding and testing the poller is the `scaffold_poller` job: `delegate` it
with the system, what the owner wants, their scope and their quote; put its `ask_owner` to them.

**Connecting a system with items** ("connect our Jira", "bring in PagerDuty", Slack channels,
   Jenkins): your first call is `integrate_system`, before any guide, with the system, what they
   want, the scope they named (`settings`), the stock it fills and their quote. The bridges are a
   library: it scaffolds a script from a kit into the owner's repository (`.orchestra/machines/`;
   the state directory only when they said the repository must stay clean), or configures the one
   already there from that kit (several: name each), tests it and declares
   the system; a failed test or a system with no template [[tasking]]goes to the Coordinator as work[[/tasking]][[no-tasking]]is yours: fix the script with `edit_machine_file` (its kit's test runs again)[[/no-tasking]].
   Nothing reads it yet. Put its `ask_owner` to the owner: [[ask]]their yes, [[/ask]]where the token is (a
   variable's *name* or a file's path, never the token), and what it says is missing. [[act]]Once they
   say, `approve_integration` with what they gave and your reason: a read-only one is yours to
   approve; one that writes to the system takes their yes, quoted.[[/act]][[ask]]On their
   yes, quoted, `approve_integration` with what they gave.[[/ask]] Your guide's "Connecting a system" has
   the rest.

**Then its items, as a workflow.** Once `approve_integration` has connected a system that has items
(Jira tickets, PagerDuty incidents, Slack threads) and the Workflows plugin is installed
(`list_workflows` answers), [[act]]declare a workflow that makes them items, with your reason, and
say in one line the items and the stocks the owner gets, and that it only observes: "I declared a
workflow that makes each Jira ticket an item, with a step per status (To Do, In Progress, In
Review): you get each step's count and the moves between steps, as stocks and flows discovery
finds; it only observes and writes nothing to Jira."[[/act]][[ask]]propose in one sentence a workflow that makes them items, naming the
items and the stocks the owner gets, and that it only observes: "Shall I declare a workflow that
makes each Jira ticket an item, with a step per status (To Do, In Progress, In Review)? You would
get each step's count and the moves between steps, as stocks and flows discovery finds; it only
observes and writes nothing to Jira." On their yes, quoted,[[/ask]] `declare_workflow` (the `workflows`
skill): started on the poller's events, each step's `who` `{"outside": "<system>"}` and its done-when
the system's own event, no `outbox` step and nothing drafted.[[ask]] Propose it once; never declare it
without their yes.[[/ask]]
