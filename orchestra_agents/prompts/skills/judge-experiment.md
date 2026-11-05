# Skill: judge-experiment

Use when: (a delegated job) judge the explore round's last experiment from the discovery runs since it, and record the verdict.

You judge one experiment for the Supervisor and report back.

1. `read_experiments` for the experiment the brief names: its change and its expected effect.
2. `read_discovery_runs` since it was applied (with `key` for one series' explain record) and
   `get_model`'s `left_out`: stocks kept or lost, trust, fits, the store's requests.
3. `judge_experiment` with `kept`, `reverted` or `inconclusive` and what the runs showed, in a
   sentence. Discovery's rounds judge it, never a hope. For `reverted`, undo the change first with
   `set_setting` key `sources` (the whole list, the change taken out), never widening `match` and
   never writing `explore`.

Your report is at most 6 lines: the verdict, the evidence (numbers from the runs), and whether the
change was undone (with the commit).
