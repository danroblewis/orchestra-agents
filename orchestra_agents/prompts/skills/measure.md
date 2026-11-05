# Skill: measure

Use when: the owner wants something measured, or says a measurement is missing.

## Measurements

When the human wants something measured ("count the TODOs in the code", "measure the open orders
in the database every minute"), you set it up yourself, in the turn, and see it stored:

1. **Read Orchestra's guide** on instruments (`read_guide` orchestra, sections "Instruments and
   machines", "The `python` rule", "Naming series"), once per session.
2. **Look in the store first** (`list_metrics`): a series already recorded always beats a new
   instrument; name the one you found, or say you found none. A stock the owner declares is
   measured with the design tools (the `design` skill).
3. **Find where the number lives**, read-only: the database and its schema, the files, an app's
   existing `/metrics`, another metrics store.
4. **Choose the instrument.**
   - A URL that already serves Prometheus text (an exporter, an app's `/metrics`): a `[[scrape]]`
     (`set_collector` with `url`). It is read only while the program runs: a program of the
     project's that must keep running is a `[[machines]]` entry too (`set_machine`).
   - Another metrics store (Prometheus, VictoriaMetrics, Chronosphere): `[[sources]]` (the `store-setup` skill).
   - Otherwise a `[[collect]]` command that prints the measurements and exits. When a one-liner
     over tools already there does it (`git grep`, `sqlite3`, `psql -tAc`, `curl -s ... | jq`,
     `wc -l`), it is configuration: declare it yourself with `set_collector`, `shell: true`, e.g.
     `echo "todo_comments $(git grep -I -c TODO -- . | awk -F: '{s+=$2} END {print s+0}')"`.
   - Anything more (logic, parsing, paging or authenticating an API, several sources) is a
     script, and a script is code: [[tasking]]hand it to the Coordinator[[/tasking]][[workflows]]a worker's, as a Workflows item on a branch (skill `code-work`), and say what it would be[[/workflows]][[no-workflows]]not handed on here (say so, and that installing Workflows is the one step), and say what it would be[[/no-workflows]] (the script under `machines/`, the
     series it prints and their names, its `[[collect]]` entry with `every` and `timeout`;
     {machines_doc}). That needs `work = "code"`; if the project is on tasks, [[act]]set it
     (`set_work_mode`) with your reason and say so[[/act]][[ask]]it is the human's
     choice: ask, with your recommendation[[/ask]].
5. **Name each series** for what it measures, with its unit: a level is `todo_comments`,
   `orders_open`, `disk_free_bytes`; only a count that only goes up ends in `_total`; never
   `orchestra_`. Labels split one measurement (`state="open"`); keep their values few.
6. **Try it** with `try_collector` (the same command, `shell` and `timeout`) until it answers
   `works: true`: the series you meant, no bad lines. Give the entry a `timeout` well above what
   it took and an `every` at least as long (and as often as the human asked; 60s by default).
7. **Declare it** with `set_collector` (and the reason).
8. **See it stored**: `read_instrument` with its name and `wait_s` (a round runs within seconds of
   the declaration while `orchestra run` runs): points written, no `last_error`. A failure says
   why (the error, the log): fix the entry and try again. Without `orchestra run`, say it starts
   when the human runs it.
9. **Tell the human** what is measured, under which series names and labels, how often, the
   commit, and where to see it: the Instruments page (its rounds and log) and the Core page's
   metrics catalogue; the model takes it into account once discovery has about an hour of it.

Never run a collector or start a program yourself outside `try_collector`: Orchestra runs them,
and a program started from a session dies with it. A failing collector wakes no one: when the
human says a measurement is missing, look (`read_instrument`, `list_metrics`, `check_setup`); a
cause in its entry is yours to fix with `set_collector`, a cause in a script is a bug for a
worker, [[tasking]]handed to the Coordinator with the error quoted[[/tasking]][[workflows]]a Workflows item for the worker (skill `code-work`), with the error quoted[[/workflows]][[no-workflows]]not handed on here: say so, with the error quoted, and that installing Workflows is the one step[[/no-workflows]].
