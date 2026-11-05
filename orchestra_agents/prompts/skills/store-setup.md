# Skill: store-setup

Use when: the owner asks to connect another time-series store (Prometheus, VictoriaMetrics, Thanos, Mimir, Chronosphere), or a declared store reads wrongly.

## Another time-series store

**Delegate the survey.** Once the owner gave a store's limits, the reads of step 3 and 4 (the
name list, `find_balance`, previews) are the `survey` job: `delegate` it with the store, its bound
and the question, and choose from its report.

When the measurements already live in another store (the company's Prometheus, VictoriaMetrics,
Thanos, Mimir or Chronosphere), Orchestra reads them in place with `[[sources]]` and never writes
there. **Reading a store is observation, not a change to the system**: when the human asks you to
connect one, you connect it, in the turn, and report what you chose. You do not propose a
selection and wait for a go-ahead; they redirect you afterwards if they want something else.

1. **Read Orchestra's guide** once per session: `read_guide` orchestra, sections "`[[sources]]`
   and `[[series]]`", "Reading another store's schema" and "Two stores, and a busy one".
2. **The credential, by reference.** A token in a file is `auth = {"bearer_file": "<the path the
   human gave>"}`; one in an environment variable is `{"bearer_env": "<NAME>"}`. Never read the
   file, print or copy the token, or put it in a command or a query: the tools read it themselves.
   When the store refuses without a credential and the human named none, ask where it is (a file
   or a variable), never for its value.
3. **Look, with `url`, `auth` and `bound`, before declaring:** `check_sources` (does it answer; is the
   credential there; give `auth` only when the human named a credential for this store or it
   refuses without one), then `source_schema` **without `match` first**: every metric name within
   the store's bound, one line each, cheap even on a big store. **The bound is the owner's scope**
   for that store (their namespace, job or service and at most how many series): `bound` with a
   `url`, the source's `match` and `max_series` once declared. Every tool that lists, counts or
   reads its series (`source_schema`, `find_balance`, `preview_query`) stays inside it, and a store
   with no bound is never asked for every metric name: the tools refuse and say so. On a shared
   store, ask the owner its scope before reading it; never read it before they give it. **Any
   store: read nothing from it** (no `source_schema`, `find_balance`, `preview_query` or
   `infer_source`; `check_sources` only asks whether it answers, and takes a `bound` too: Orchestra
   reads no store without a match) **until the owner has given its
   limits** (the namespace or selector, and the series cap) **or said to use defaults**; a bound you
   picked yourself (a `job` from the code) is not theirs, and "proceed as you think best" is not
   their limits. The tools refuse until then; ask in one sentence ("Which part of the store may I
   read, a namespace or a selector, and at most how many series, or should I use the defaults?").
   Read the whole list before choosing: names rarely start
   with the word you would guess (`shop_orders_placed_total`, `fulfilment_…`), so never conclude a
   store lacks something from a `match` you guessed. Then `source_schema` with `match` for the
   details of the names you chose, and `preview_query` for a candidate's shape, scale, labels and
   units. A slow,
   rate-limited or size-limited store is no reason to stop or to ask: read it narrowly (a `match`
   per family you need), and Orchestra waits out its 429s and 503s and narrows what it refuses.
4. **Choose a first selection yourself**, from the question asked or, without one, from what carries
   the system's work: amounts that accumulate and drain (queues, backlogs and consumer lag,
   inventories, sessions, connections, jobs in progress) and the counters that fill and drain
   them, then requests and their errors. Production only: a staging or test twin is never read.
   One store reached at several addresses is one source. Leave out latencies, quantiles, ratios,
   flags, timestamps and the exporters' own timings as stocks.
   **A stock is only half found without its flows.** Right after the name list, **before choosing
   what to read**, call `find_balance` with `level` `"all"`: it takes every gauge of an amount
   held in the stores (queues, consumer lag, open connections, sessions, volumes' used bytes,
   inventories; `source_schema` lists them as `levels`), searches every declared store, or those
   in `sources` (`{"url", "auth"}` for one not declared yet), for the counters that fill and
   drain each (each queue, group or volume apart), and returns each balance, its fit and the
   `[[series]]` that declare it. Read every level it balances, with its counters: a proxy's
   connections and a log's volume are stocks of the system as much as a queue. Each time you
   add a store, call it with `"all"` again (never a list of your own: a level's flows may be in
   the new store, and a level in the old one). Its `declare_all` holds one of each quantity read
   twice (`same_as`): declare that. Declare an accepted balance as given (its `declare` list), and
   read its counters in that store's `match`. One with `bursts` or `one_sided` is the level, its
   measured side and a residual: declare those and name the residual. When it finds nothing,
   say the flow is not measured. A level both stores hold is one stock: declare it once, from
   the store whose balance is accepted; two balances of one level are one flow measured twice.
5. **Declare it** with `set_setting` key `sources` (the whole list of `[[sources]]` tables as
   JSON, the ones already there included): a `name` for what the store is (it prefixes every
   series read from it), the `url`, the `auth`, a narrow `match` (a list of selectors), the
   `machine` label (the one that names the service doing the work: `service`, `job`, `app`;
   never a pod or an instance), `[sources.labels]` for spellings of one label, and `settle` for a
   store that writes late (`source_schema` says how late its newest samples are: `late`). Then key
   `series` for the `[[series]]` that pin what inference does not rank or reads wrongly (the
   guide's "Translate into quantities of the system"): each with a plain name for what it measures
   and its `kind`, previewed first. A pin is read beside what inference keeps: once a store is
   translated into pins, set `infer = false` on its entry so only the pins are read, never a raw
   copy beside each. `target` repo, or overlay when the store is only the owner's.
6. **Check it**: `check_sources` and `infer_source` with its name (what Orchestra will read, what
   it left out and why; fix the selection where it reads wrongly), and `read_instrument` with its
   name once a discovery has read it. A second store is a second entry; a quantity both stores
   hold is read from one of them. Decide which yourself, never ask the human: preview both over
   one window (the same shape and scale is one quantity; compare two counters by their rates,
   `rate(x[5m])`, never their totals, which count from different starts) and keep the copy with
   the finer resolution and production explicit; leave the other out of `match` and the pins.
7. **See it reach the model.** A translation is done when discovery makes stocks of it. Wait for
   the next discovery (`read_instrument` with the source's name and `wait_s`; again if it has not
   run yet), then `get_model`: each level you meant as a stock should be one. For one in
   `left_out`, its reason says what is wrong: "no counters balance it" means a flow is missing or
   counts other things: a level summed over several queues or consumer groups beside one's
   counter, a global counter beside one queue's level. Keep separate backlogs separate (group a
   level and its flows by the same label, `by (consumergroup)`, `by (queue)`; never sum two into
   one), add the missing flow, and look again. A translation that leaves fewer stocks than the
   reading before it is worse: undo that part.
8. **Tell the human** in a few lines: what you read and why, the series names discovery will see
   (`<name>:<series>`), what you left out (and what is not measured at all, such as an outflow no
   series counts), and the commit.

Ask before reading only when the store bills per query, or when you cannot tell which of several
environments is production from the data. Credentials are never in either file.
