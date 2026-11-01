# Modeling: reading the system dynamics model and predicting a change

Orchestra measures the system this project runs (its metrics) and discovers a **system dynamics
model** from them. You can read it, and state what a change you are about to make will do as a
**prediction**. Orchestra then judges the prediction against what the system did. Reading the
model changes nothing in the system.

## What the words mean

- **Stock**: something that accumulates and is measured as a level: tickets waiting, jobs in a
  queue, money in an account. `read_model` gives each stock's level now. A **hidden** stock is
  one no series measures; the model starts it at an assumed initial level.
- **Flow**: what moves material into or out of a stock, or between two, as a rate per the
  model's time unit (`rates are per minute`). `outside` is the world beyond the model. A flow
  measured by a counter (`jobs_done_total`) is checked against what was counted.
- **Equation**: a flow's rate, written in the equation language below, with named parameters:
  `k * queue [k=0.5]` means half the queue a time unit. A rate below 0 is taken as 0, and a
  stock's outflows share what it holds.
- **Origin**: where an equation came from. `fitted` (Orchestra fitted it to the data, with how
  many events and an error: `±4%`), `discovered`, or `declared` (written by someone: an
  assumption until a later discovery fits it, given a ±50% band).
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
- **Prediction and verdict**: a prediction says what your change will do (which flows move by
  what factor, which stay, which stocks stay below or above a level). After the horizon
  Orchestra judges it, running the previous era's model forward with your expected edits to
  see how the stocks should have moved:

  | Verdict | Means | What to do |
  |---|---|---|
  | `passed` | what you predicted happened, and the stocks moved as the model said | your understanding held |
  | `no effect` | a predicted edit did not appear | the change did not take effect, or does not do what you thought |
  | `side effect` | something you did not predict changed, or a predicted flow moved by another amount | read `unpredicted` and the expectation's numbers; your change reached further than you modelled |
  | `model wrong elsewhere` | your expected edits happened, but a stock did not move as predicted | the change may be right; the model of the rest of the system is wrong at that stock |
  | `interrupted` | another change began a new era before the horizon | `partial` is what could be judged; nothing more to learn |
  | `undecided` | not enough evidence at twice the horizon | the flows you named count too little; choose a longer horizon next time |

  Each expectation is `met`, `absent` or `different`, with its numbers. For an equation you
  wrote: `your equation expected N events, M were counted, z` (|z| < 2 is a fit) beside the old
  equation's `z`.

## The tools

| Read (`model`) | Predict (`predict`) |
|---|---|
| `read_model`, `read_series`, `read_eras`, `read_observability`, `check_equation`, `read_predictions` | `predict_fix`, `announce_change` |

Every answer starts with a `summary`: a few lines with the numbers that matter. Ask for
`raw: true` only when you need every number. `read_series` summarises a history (range, mean,
trend, latest readings); ask for `points: true` only when the shape matters. `check_equation`
tells you whether an equation parses against the model and what it reads.

(The experiments, a draft copy of the model edited and run forward, replayed or inverted, were
removed on 2026-11-01. A designed machine's expected effect is the Supervisor's to declare and
confirm, `declare_expected_effect` and `confirm`, which publish its prediction.)

## Before you predict

Read first: `read_model` (the stocks, the flows and their equations), `read_eras` (what the last
change did), and `read_series` for the stocks your change should move. Note which flows are not
trusted: a prediction about a flow the model does not trust says more about the model than about
your change.

## Making a prediction

Predict when you are about to make a change that should move a measured stock, and you can say
how. `predict_fix` with what you expect (`{"flow": F, "factor": 2}`: its rate doubles, 0 stops
it; `{"flow": F, "unchanged": true}`; `{"stock": S, "below": x}` or `above`; `[]` for a change
that should change nothing measured, the null test), a sentence saying what the fix is, and a
`horizon` long enough for the flows you named to count a few dozen events (from `read_series`:
if a flow runs at 2 a minute, 30m gives 60). It answers with a **change id**. Then:

1. make the change;
2. announce it with that id: `announce_change` with `when: now` once it is in effect, or
   `when: landed` for code that takes effect when your ticket lands (the host announces it then);
   the Supervisor passes `change` to the setup tool that makes a configuration change instead;
3. nothing is judged until it is announced. The verdict wakes you: read it with
   `read_predictions` and say plainly what it means for the change.

A prediction is for your own change only: only the agent that made a prediction can announce its
change.

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
