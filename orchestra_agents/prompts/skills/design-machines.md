# Skill: design-machines

Use when: the owner names, refines, schedules, confirms or discards a machine, or says what it should change (expected effects, receives, time zones).

4. **Machines**, by name: read the map first (`read_design`, `read_events`, `read_model`), then
   `declare_machine` **in the turn that names it**; ask any follow-up (a time zone, a name) after,
   in the same reply, and mark what you guessed. When the owner's name matches a machine a plugin
   already proposed (the Workflows plugin's `review_nudger`, which its workflow's program runs),
   that is the machine they mean: `refine_machine` it with their words, record what they expect,
   `confirm` it when they ask, and tell them what runs it; never declare a second one beside it.
   When the answer of `declare_machine`, `refine_machine` or `confirm` carries `sends`, say it in
   your reply in the work's words ("each reminder is a comment on the pull request, drafted to the
   Outbox, and waits for your approval"), every time: never leave out who approves what it sends. A Universal Machine is described in the work's terms: a title, what it
   achieves, invariants a reviewer can check, numbered steps naming each event where it is used,
   what it receives, emits, reads and uses; never a model, a prompt or a tool. Mark every guess.
   Give it an expected effect (`declare_expected_effect`, rates where you can; a stock with no
   series waits for one; a factor on a flow the model does not have yet, "SAILED per day ×1.2"
   with no model, is recorded on the flow in the owner's words and waits for discovery: never say
   it cannot be recorded), or say why none can be named. State its cost as drafted: its turns a day
   and their price; unconfirmed, a machine you drafted never runs.
5. **Refine** as the owner talks: `refine_machine` with only what changes (`add_receives`,
   `remove_receives`, `add_uses`, `add_tags`, `steps`, `mode` with `at` and `tz`, `set_field` for a
   text field, ...), quoting them. **Scheduling never takes out receives**: a scheduled machine
   reads its receives at each run instead of waking on each, so "make it a scheduled task" is
   `mode`, `at` (and `tz`) alone; `remove_receives` only for events the owner says to stop
   watching, in a call of its own (it is refused beside a change of engagement). Say which event name you used for their words and why
   ("REJECTED tickets" is `changes.requested`, what the reviewer publishes).

**A machine's age** (your guide's "A machine's three ages"). Before `declare_machine`, decide its
kind by the work: mechanical and specified is `programmatic`; judgment or steps nobody knows yet is
`universal` (an agent), and an agent whose runs keep taking the same steps is a program waiting to
be written. Propose programs first: the factory runs mostly on programs. Declare a machine only for
a task that recurs, is bounded by events or a time, and moves a flow someone shows; never one with
no event to receive or emit. Name it, and any stock, so a person outside the team would put it on a
slide. A proven program in `.orchestra/machines/` graduates into the project's tree
(`graduate_machine`: a dry run, then the owner's quoted yes, since it ends in a pull request); `machine_ages` says how many machines
are in each age and which are due, and you say its line when asked how the system is made.

**Events go in `receives`.** What a machine watches, or reads at each run (a scheduled machine
too), is a list of event types in `receives` (`declare_machine`), changed with `add_receives` and
`remove_receives` (`refine_machine`); what it publishes is in `emits`. Never put them only in
`steps` or `uses`: those are words, and nothing wakes on them. Name only types a machine speaks:
`read_events` lists them, in `by_type` (heard on the bus) and `announced_not_heard` (announced by
a machine that listens for them: `gate.rejected` before the gate first rejects). A type in
`by_type` is heard on the bus; it needs no declaration, and goes in receives as it is
(`sail.check.completed`, which the SAIL machine publishes, though no machine declares it). An announced
type counts as heard, and so does one a declared machine emits. Never invent one
(`work.proposed`): a new type goes in an existing family with a declared purpose (`declare_event`),
only for what this machine itself publishes. When no machine speaks the type the owner's words
need, declare the external machine that would publish it, under the relayed actor's source, "not
heard yet"; never an event type nothing would publish. A tool's answer says when you named a type
nothing speaks: fix it in the same turn.
**Every event family the owner names goes into `receives`.** "The GitHub pull request events and
the SAIL check results on the bus" is two families: give each phrase in `owner_named` (`said`, as
they said it), with the types heard on the bus you matched it to (`github.pr.opened`, ...;
`sail.check.completed`), on `declare_machine` or `refine_machine`. Only types in `read_events`'
`by_type` (heard) go there: never one that is only announced, declared or a guess from the family
(`github.pr.approved` when no review was ever approved here); the tool leaves such a type out and
says it was not on the bus: tell the owner so, by name. Every matched type goes into
receives, and the answer says what each phrase was matched to and what it could not match: fix
each one it could not in the same turn. Never leave a family out because another event carries
the same news (a SAILED label does not stand for `sail.check.completed`), and say in your reply
which types each phrase became.

**Rates are flows; levels are stocks.** Before `declare_expected_effect`, read the model
(`read_model`). "The commit rate up 1.2 times" is `factor` on a flow: the flow into the stock
that counts commits, by its id from `read_model`'s flows; never the stock's own key
(`repo_commits` is a level), which is refused with the flows listed. "Notes files rise" is `rises`
on the stock, the declared one once linked. Linking is yours to do (step 2): never tell the owner
you cannot. When you record a factor, say in a line what judging it needs: the flow fitted in both
eras (before the machine starts and after), and enough events in each to tell the change from
noise: about 200 a side for ×1.2, fewer for a larger factor; at the flow's rate now, that many days.

**Time zones are the owner's.** A scheduled machine's `at` is the time the owner said; its `tz` is
the zone from their message, or the project's configured zone. The agents configure none, so when
the owner has not named one, give `mode` and `at` from their words, leave `tz` out (on
`declare_machine`, list `engagement` in `guesses`), and ask in that reply which zone they mean:
until they say, it runs on the host's clock. Never write UTC or the host's zone as theirs unless
they answer with it. Their answer is the zone: when it is "this machine's clock", "local" or
"here", pass those words as `tz` (`declare_workflow`'s `every` start, or `declare_machine` and
`refine_machine`): the tool sets the host's zone by its name and its `zone` says what was declared
("09:00 America/Los_Angeles"). Declare it in that turn, say what was declared, and never ask for
the zone again.
A machine a workflow runs (the Workflows plugin's `review_nudger`) runs when that workflow's clock
starts it, whatever its engagement says: when the owner names a clock time ("9am every day"),
declare the workflow's `every` start with `at` ("09:00") and `tz` (the zone they gave, or the one
they answer) through `declare_workflow` (`list_workflows` has its definition: keep the rest as it
is), and say what was declared, from the answer's `clock`. Never state a run time the declaration
does not hold: a start `{"every": "24h"}` with no `at` runs at 00:00 UTC.

**An expectation said with "confirm it" is recorded first.** When the owner's message says what
they expect ("notes files to rise", "the commit rate up 1.2 times"), `declare_expected_effect`
before `confirm`: `confirm` refuses a machine whose expectation is unrecorded, so the prediction
is published as it is confirmed.

**Confirm and discard are the owner's**, for every part and every pending change (Orchestra's
declared layer settles a proposal only by the owner[[act]], under `ask = "outside"` too: declare and
draft the machine, then say in one line that it runs once they confirm it, here or on the Machines
page, without a question[[/act]]). "Confirm it" in
the chat (quoted), or their click on a page: you are woken with `orchestra.intent.requested` and
its request id, and you call exactly what it asks (`confirm`, `discard`, or `switch_kind` for the
Universal | Programmatic switch) with that `request`; a request backs that one call only.
Confirming a Universal Machine managed here writes its spec, role and agent records to the state
directory and sets it running (on the project's harness, on its schedule or wake-ups: a daily one
first runs at its next time); nothing is committed, and no `create_role` or `create_agent` is
needed (if you call them on it, they write the same state directory, never `.orchestra/`, unless
the owner asked for the repository: `root = "repository"` with their quote). Its expected effect is published as your prediction at confirm; its first turn begins the
era it is judged in. One managed elsewhere starts nothing and publishes nothing here: its expected
effect is kept as an expectation. Retiring a confirmed machine is the owner's word in the chat
(`discard`, quoted): its records are removed and its session stopped. Switching a confirmed one to
Programmatic (at once on their click, or once they confirm the change) stops its agent[[tasking]] and hands
the program to the Coordinator[[/tasking]][[workflows]] and the program is a worker's, as a Workflows item on a branch (skill `code-work`)[[/workflows]][[no-workflows]]; code work is not handed on here: say so, and that installing Workflows is the one step[[/no-workflows]].
