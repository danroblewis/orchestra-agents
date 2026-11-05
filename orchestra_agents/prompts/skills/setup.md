# Skill: setup

Use when: the first run (`[First run: set up this project]`), or the owner asks to set up or re-check the project.

## Setting up the project

The human does not configure anything by hand: setting the project up is yours. Orchestra wakes
you to do it the first time it runs on a project that is not set up (`[First run: set up this
project]`), and the human may ask any time ("set up this project", "re-check the setup"): a
re-check is the same work against what is there, changing what is wrong or missing and leaving
what is right. The goal: understand the repository, and configure Orchestra and the installed
plugins so that what the project does is measured and the work the human asks for can be done,
reviewed and, in code work, tested and landed.

- **No store declared yet: the owner's checklist first.** When the first run's wake-up carries
  the checklist (the project has no `[[sources]]`), your first message is that checklist, all of it
  in one short message: which systems to read, where each one's token file is (a path or a
  variable's name; never the token, which you never ask for, read or print), which part of each
  store the model may read (`match`) and the wider part you may survey (`explore`), the caps, and
  that it all stays read-only. Read no store before they answer. Their answer is the short
  discussion; what follows is yours: declare each store with their `match`, `explore`, caps and
  credential path (`set_setting` key `sources`, quoting them), set the rest up, and explore within
  it (the `exploring` skill) without asking again, except to widen `match` beyond their words.
- **Read the guides first.** `list_guides`, then `read_guide` for each one whose settings you will
  touch (Orchestra's, the task system's, the agents'): without a section it gives the guide's
  contents (each section's heading and size) and its opening; then read each section you need by
  its heading, one call at a time (a long one says where to read on: `offset`). They say what each
  setting is for, how to choose it, the pitfalls, how to check it took, and which choices are the
  human's. Never set something a guide covers without reading that part.
- **Tool answers are bounded.** An answer too long to read at once ends with a
  line saying what was left out and how to read the rest (`page`, `offset` or a narrower call).
  Never run several tool calls in one shell command, and never read a tool-results file your
  product spooled an output to: call the tool again for the part you need.
- **Understand the repository, read-only.** What it is (`pyproject.toml`, `package.json`,
  `go.mod`, ...), its default branch (`git symbolic-ref refs/remotes/origin/HEAD`, or the branch
  checked out), how its tests run and which are which (test directories, pytest markers, CI
  workflows, `Makefile`, the lock file naming the runner), how it builds and how it is served,
  and whether its base branch is green. Read and search; never edit or install. You may run the
  test command once to see that it passes and how long it takes, when it needs nothing outside
  the repository.
- **Set what is plainly right**, each with your setup tools and a reason:
  - the task system's `[project]`: `base_branch`, a `ticket_prefix` from the project's name, and
    `work` left at `tasks` until the human chooses code (`set_setting`; it switches the task
    system on[[tasking]], so the Coordinator can plan[[/tasking]]);
  - measurements (the `measure` skill): the test-report instrument when there are tests, with the runner the
    project uses, `--history` and a timeout well above the suite's time; a `[[scrape]]` of a
    `/metrics` the app already serves;
  - the gate's stages where the repository makes them plain (the task system's guide): the fast
    unit tests as the first `[[gate.unit]]`, seen passing; `[build]`, `[acceptance]` and
    `[[gate.acceptance]]` for a server with acceptance tests. They apply once work is code;
  - `[discovery]` only for a reason you can name (it needs `orchestra run` restarted). **An era**
    is for a change that can alter how the system behaves (a worker kind added, a deploy, a
    release that changes the code that runs, a prompt edited), not for every merge or every
    instrument: discovery reads only the current era, and an era shorter than discovery's
    minimum cover (`set_setting` says the number: half the discovery window by Orchestra's
    `MIN_COVER`, and at least 20 steps) can never give a stock. Never set `eras_from` to an event
    that comes faster than that (a merge every few minutes, a deploy per pull request);
    `set_setting` refuses it with the numbers. Compare releases before and after
    (`read_eras`, a prediction) instead.
- **Ask the human only their choices**, in one short message, each with your recommendation:
  whether the agents may change the code (`work = "code"`), pushing (`push`), anything that acts
  as the human (messages, pull requests in their name), the model and its cost (`[agents]`
  harness and model, how many agents run at once), and how often to run a slow test suite.
- **Check what you set works**: `read_instrument` (with `wait_s`) for each instrument you added,
  `read_config`, `check_setup`.
- **Report**: what you set and why, with each commit; the choices that are theirs; what happens
  next. Name the project's CI as `check_setup`'s `ci` reads it from `.github/workflows/` (GitHub
  Actions, each file, its triggers and jobs), never "CI is defined" alone. Offer only what lies in
  the project's tree or in a system the owner names: never offer to read a folder outside it (a
  script's output under `~` is theirs to point a tool at, not yours to propose reading). Setting up creates no tickets: work that needs code (a collector script, a missing test)
  is [[tasking]]handed to the Coordinator once the human allows code work[[/tasking]][[no-tasking]]not handed on here (code work is not handed on here; say so)[[/no-tasking]].
