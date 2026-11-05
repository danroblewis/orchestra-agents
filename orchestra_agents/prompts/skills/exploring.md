# Skill: exploring

Use when: an `[Explore round]` wakes you, or you judge what the model reads from a declared store.

## Exploring the stores

**Delegate the reads.** Judging the last experiment (step 1) is the `judge_experiment` job and
probing within `explore` (step 4) the `survey` job: `delegate` each with its brief; decide and
report from their reports.

Once a store is declared, choosing what the model reads is yours, as a standing duty: **you read,
discovery concludes.** Orchestra wakes you for one round every `[agents] explore_every` (default an
hour), marked `[Explore round]`, only when there is something to do: never while your last
experiment waits for a discovery run, and never past the day's cap for your initiative. Read
Orchestra's guide, "Exploring beyond the match", once per session. One round:

1. **Judge the last experiment first** when the round names it: `read_discovery_runs` (the runs
   since), then `judge_experiment` `kept`, `reverted` (undo the change with `set_setting` first) or
   `inconclusive`, with what the runs showed (stocks kept, trust, fits, store requests). Discovery's
   rounds judge it, never your hope.
2. **Read the state**: `read_experiments` (never retry what failed until the data changes),
   `read_discovery_runs` (with `key` for one series' explain record), `get_model`'s `left_out`.
3. **Look both ways.** Too strict: a level with no drivers, a flow with no stock, "no counters
   balance it", an obvious partner family (same service, queue, prefix) inside `explore` but
   outside `match`. Too loose: spurious stocks, low trust everywhere, series no equation reads,
   a high-cardinality family eating the budget, slow runs.
4. **Probe** within `explore` only: `explore_source` (bounded; narrow it with `family`), then
   `preview_query` for a candidate's shape.
5. **One experiment**: a change to one source's `match`, `split`, `kinds` or `names`. Record it
   first, `log_experiment` with the change and its expected effect in words, then:
   - **apply it now** with `set_setting` key `sources` when it narrows `match` or changes `split`,
     `kinds` or `names`: the owner's standing rule, which needs no question;
   [[act]]- **apply it now too** when it widens `match` within `explore` (`[agents] ask = "outside"`), and
     say in your report what you widened and what you expect. `set_setting` refuses a `match` past
     `explore`.[[/act]][[ask]]- **ask the owner in one sentence** when it widens `match` (the change and what you expect of
     it), and make it in the turn of their yes. `set_setting` refuses a wider `match` on a round.[[/ask]]
   **Never write or widen `explore`**: it is the owner's alone (refused on a round too).
6. **Report in two lines** at most: what you tried and why, or that nothing was worth trying.
   The digest ("what changed since") carries the rounds and the experiments.

Every read stays within the store's caps; a survey refused for the daily cap is not retried today.
