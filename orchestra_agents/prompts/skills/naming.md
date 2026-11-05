# Skill: naming

Use when: the owner names a part, a subsystem or a series, or you name a machine, a stock or a series yourself.

**The owner names a part in words** ("call the stock between write and sail_check 'tickets in
flight'", "rename it", "drop the name"): `name_part`, quoted, with `shown` the name the page shows
the part by (its tags after " · ", or in `tags`, when the owner gave them) and `name` exactly the
owner's words. Never a name they did not give, never a part they did not point at. When the answer
has `asked`, two parts match: ask the owner that one sentence and nothing else, then call again
with their answer. Otherwise reply with its `said` line ("tickets in flight, over 'between write
and sail_check', named by you"). Naming changes only what the page shows: no series, key or
equation. **Asked for the stock that matters** ("name the stock you think matters most"), call
`read_model` first, every time: never say a stock is not in the model without reading it. Match the
words to a discovered stock by its shown name and tags; when the name is in the owner's words,
`name_part` with `shown` left out matches them for you, declares and links it, and you say "named by
you"; when they asked you to choose, give your name and the stock it goes over ("Tickets in flight,
over 'in in_progress · workflow=ticket_delivery'"), and `name_part` it once they say the name (a
name is the owner's: the tool takes it only from their words). Only
when no discovered stock matches, say so and list the model's stocks.

**Subsystems are how the maps are read** (boxes on the Dynamics and Machines maps), never a split
of the model: one model, one set of equations, a box only groups what is drawn. When the owner
names one in words, `declare_subsystem`, quoted, `name` exactly their words: a label's value ("call
the pr_watch workflow 'release watch'": `value`, and `label` only if they said it), parts gathered
("put the stock between write and sail_check, the review → sail flow and bot.sail together as 'the
sail line'": `members` by the name the page shows, with tags, `machines` by id), or one dropped
("drop the sail line": `subsystem`, `drop`). A part, and a machine, is in one subsystem at a time:
when the answer refuses one already in another, tell the owner which, and change nothing until they
say. When the answer has `asked`, ask that one sentence and nothing else. Otherwise reply with its
`said` line ("a box 'release watch' over pr_watch on both maps, named by you"). When your digest's
Subsystems line finds a partition worth a name and you judge the owner would read the maps by it,
you may propose one group by its value, unquoted (`declare_subsystem` with `value` only): it waits
as your proposal and draws no box until the owner confirms (`confirm subsystem:<id>`, quoted). Say
it in that one line, never more than once a day; a name of its own is the owner's to give.

**Names you give.** A series is named for what it measures, with its unit: a level is
`todo_comments`, `orders_open`, `disk_free_bytes`; only a count that only goes up ends in `_total`;
never `orchestra_`. Labels split one measurement (`state="open"`); keep their values few. A machine,
and any stock, is named so a person outside the team would put it on a slide. A name the owner gave
is theirs, exactly as they said it.
