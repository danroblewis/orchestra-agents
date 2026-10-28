# Modeling: reading the system dynamics model and testing a change on it

Orchestra measures the system this project runs (its metrics) and discovers a **system dynamics
model** from them. You can read it, copy it into an **experiment**, change the copy, run it, and
commit it as the **prediction** of a change you are about to make. Orchestra then judges the
prediction against what the system did. Nothing you do to an experiment changes the system.

## What the words mean

- **Stock**: something that accumulates and is measured as a level: tickets waiting, jobs in a
  queue, money in an account. `read_model` gives each stock's level now. A **hidden** stock is
  one no series measures (an experiment may add one); it starts at the level you give it.
- **Flow**: what moves material into or out of a stock, or between two, as a rate per the
  model's time unit (`rates are per minute`). `outside` is the world beyond the model. A flow
  measured by a counter (`jobs_done_total`) is checked against what was counted.
- **Equation**: a flow's rate, written in the equation language below, with named parameters:
  `k * queue [k=0.5]` means half the queue a time unit. A rate below 0 is taken as 0, and a
  stock's outflows share what it holds.
- **Origin**: where an equation came from. `fitted` (Orchestra fitted it to the data, with how
  many events and an error: `±4%`), `discovered`, or `declared` (written by someone, such as
  you in an experiment: an assumption until a later discovery fits it, given a ±50% band).
- **Trust**: a flow or stock is **not trusted** when its equation is declared, fitted on too few
  events, carried from the previous era, extrapolated past the rates or levels ever seen, or
  when the model's own replay misses that stock's measured level. The reasons are spelled out;
  read them before believing a number that depends on it.
- **Era**: each announced change to the system (a `system.changed`, a configuration change)
  begins an era. Each era is discovered from its own data; `read_eras` says what changed at the
  last boundary, flow by flow, with `z` (the change over its standard error: |z| >= 2 is a real
  change).
- **Observability**: whether what is measured determines each stock (`read_observability`). A
  stock that is not determined can drift in the model without any measurement noticing.
- **Machines**: a flow may name machines whose events match it. These are hypotheses from
  timing, not facts.
- **Prediction and verdict**: a committed experiment says what your change will do (its edits)
  and how the stocks will move (its forward run). After the horizon Orchestra judges it:

  | Verdict | Means | What to do |
  |---|---|---|
  | `passed` | what you predicted happened, and the stocks moved as the model said | your understanding held |
  | `no effect` | a predicted edit did not appear | the change did not take effect, or does not do what you thought |
  | `side effect` | something you did not predict changed, or a predicted flow moved by another amount | read `unpredicted` and the expectation's numbers; your change reached further than you modelled |
  | `model wrong elsewhere` | your edits happened, but a stock did not move as predicted | the change may be right; the model of the rest of the system is wrong at that stock |
  | `interrupted` | another change began a new era before the horizon | `partial` is what could be judged; nothing more to learn |
  | `undecided` | not enough evidence at twice the horizon | the flows you named count too little; choose a longer horizon next time |

  Each expectation is `met`, `absent` or `different`, with its numbers. For an equation you
  wrote: `your equation expected N events, M were counted, z` (|z| < 2 is a fit) beside the old
  equation's `z`.

## The tools

| Read (`model`) | Experiment (`experiments`, `replay`) | Predict (`predict`) |
|---|---|---|
| `read_model`, `read_series`, `read_eras`, `read_observability`, `check_equation`, `read_predictions`, `list_experiments`, `read_experiment`, `diff_experiment`, `read_run` | `create_experiment`, `edit_experiment`, `load_model`, `run_replay`, `run_forward`, `run_inverse`, `discard_experiment` | `commit_prediction`, `announce_change` |

Every answer starts with a `summary`: a few lines with the numbers that matter. Ask for
`raw: true` only when you need the trajectories or every number. `read_series` summarises a
history (range, mean, trend, latest readings); ask for `points: true` only when the shape matters.

## Testing a hypothesis about a change

1. **Read first.** `read_model` (the stocks, the flows and their equations), `read_eras` (what
   the last change did), and `read_series` for the stocks your change should move. Note which
   flows are not trusted.
2. **Draft.** `create_experiment` with a title and the question it answers. Then change the
   draft so it says what your change does, as equations, not as an outcome:
   - a whole new equation for a flow: `replace_flow` (`{"op": "replace_flow", "id": "reviewed_total",
     "equation": "k * min(reviewing, 2 * reviewers)", "params": {"k": 0.4}}`);
   - a flow that goes away: `remove_flow`; a new one: `add_flow` with its ends and equation;
   - a number of an existing equation: `set_params`; a stock nothing measures yet: `add_stock`;
   - many edits at once go in one `edits` batch; `load_model` replaces the whole draft when it is
     easier to write the model you believe in full.
   Use any stock, measured input or parameter you need. `check_equation` tells you whether an
   equation parses and what it reads before you edit.
3. **Replay before you believe it.** `run_replay` over a past window (an era, or `span: 1d`)
   runs the base and your draft on what really happened. Your equations should explain the past
   about as well as the base (stocks `same`, flows `same` or `better`), unless your change is
   exactly what made the past different. A draft that is `worse` on flows you did not mean to
   change is wrong there: fix the equation, not the outcome. An equation that only produces the
   outcome you want, and explains the past worse, is tuned, not true.
4. **Run forward.** `run_forward` with a horizon (`2h`): each stock's base, experiment and
   difference, with a band. Read the `not trusted` reasons before quoting a number. A limit
   (a stock reaching 0, or your bounds) that is `new` is something your change causes.
5. **Iterate.** Edit and run again until the outcomes you expect come from equations you can
   justify in a sentence each. Discard drafts you abandon.

## Asking backwards: the inverse run

`run_inverse` asks: "for this past window to have come out otherwise, what else would have had
to be true?" Give `targets` (`{"stock": "waiting", "scale": 0.5}`: half its mean;
`{"stock": S, "below": x}`; `{"flow": F, "scale": 2}`) and **hold** what must not move
(`{"flow": "arrivals"}`: its equation stays; `{"series": "waiting"}`: that history stays). It
finds the fewest changes to the draft's numbers (with `new_flows`, also a new flow) under which
the replay meets the targets, lists the side effects, and gives you the solution as a new draft.

- It optimises only what you name. If the answer changes something you cannot change (customer
  demand), hold it and ask again.
- `AMBIGUOUS` means another change does as well, or the data the solution would make does not
  single it out. Treat it as one possibility among several, not as the cause.
- It looks one change ahead: two changes that only work together are not found.

## Committing a prediction

Commit when you are about to make a change that should move a measured stock, and you can say
how. `commit_prediction` with the experiment and a `horizon` long enough for the flows you
changed to count a few dozen events (from `read_series`: if a flow runs at 2 a minute, 30m gives
60). It answers with a **change id**. Then:

1. make the change;
2. announce it with that id: `announce_change` with `when: now` once it is in effect, or
   `when: landed` for code that takes effect when your ticket lands (the host announces it then);
   the Supervisor passes `change` to the setup tool that makes a configuration change instead;
3. nothing is judged until it is announced. The verdict wakes you: read it with
   `read_predictions` and say plainly what it means for the change.

A prediction is for your own change only: a worker commits only an experiment it made on its
ticket, and only the agent that committed a prediction can announce its change. A change that
should change nothing (a refactor) can be committed with no edits: the null test.

## The equation language

{language}

| Rate | Means |
|---|---|
| `c` | constant |
| `k * waiting` | proportional to a stock |
| `c + k * demand` | linear in a measured input |
| `k * min(waiting, 2 * workers_free)` | capped: at most two a free worker |
| `k * waiting * (1 - backlog / cap)` | slowing as a backlog fills |
| `(target - level) / tau` | closing a gap |
| `rate * delay(approved, 2)` | following another level two time units late |
| `observed()` | as measured: no equation |
