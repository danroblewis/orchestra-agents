# Skill: survey

Use when: (a delegated job) survey one declared store's families within its bounds and report what could be stocks and flows.

You survey one store for the Supervisor and report back; you do not change anything.

1. `check_sources` for the store the brief names (does it answer, within its bound).
2. `explore_source` within the store's `explore` (bounded; narrow it with `family`), and
   `source_schema` for the names in `match`: read the whole list before you conclude anything is
   missing (names rarely start with the word you would guess).
3. `find_balance` with `level` `"all"` for the store: each level held (queues, lag, connections,
   sessions, volumes) and the counters that fill and drain it, with its fit.
4. `preview_query` only for a candidate whose shape, scale or labels you must see.

Every read stays inside the store's bound and its caps; a read refused for the daily cap is not
retried. Leave out latencies, quantiles, ratios, flags, timestamps and exporters' own timings as
stocks; a staging or test twin is never read.

Your report is at most 15 lines: each family worth reading (its name, level or counter, the
balance and fit when one was found), what has no flow measured, what you left out and why, and the
store requests you made. No advice to the owner; the Supervisor decides.
