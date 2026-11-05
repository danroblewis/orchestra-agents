# Skill: config-changes

Use when: before any setup tool changes the configuration: which file (`repo` or `overlay`), what is yours to change, and what never goes in it.

## What you change, and what you never change

Your setup tools change the configuration, one entry at a time: the roles the AI agents fill, the
kind of work, the gate's tests, the capacity limit, `[[machines]]`, `[[collect]]` and `[[scrape]]`
entries, and any other key (`set_setting`); `restart_machine` restarts a program and `restart_process` a launcher's process by name. Every change is
announced on the bus, and the tool's answer says what applies now and what restarts.

Each setup tool takes a `target`, the file the change goes to:

- `repo` (the default): the repository's `orchestra.toml`, shared with everyone who works on it.
  The project's own settings go here: the kind of work, the gate's tests, the capacity, the
  roles, the project's programs and collectors. Each change is a commit of `orchestra.toml` alone,
  with the reason you give, on your branch `orchestra/supervisor`, never on the base branch, and
  live at once through the owner's overlay until it is merged (the tool's `live` says when it is
  not); it becomes a pull request (the guide's "What you commit, and its pull request"): tell the
  human the branch and the pull request as the tool's `said` puts them. A tool that refuses (the repository root on another branch,
  `orchestra.toml` being edited by someone) says why: tell the human what they need to do.
- `overlay`: the owner's private overlay, a file outside the repository merged over
  `orchestra.toml`, never committed. The owner's own or their company's settings go here and
  never in the repository: identities (who they are on Slack or Jira), channels, templates, the
  kinds of messages they send, a program or collector only they run. `set_setting` must be told
  which; when you cannot tell whether a setting is the project's or the owner's, ask.

A change to the repository that the overlay overrides comes back with a `note` saying the
overlay's value still wins: tell the human.

Never put a secret in either file (a password, a token, a key, a URL with a user and password
in it): every change is announced on the bus, and `orchestra.toml` is committed. A program or collector
that needs one reads it from an environment variable or a file; your tools refuse a change that
holds one, and `read_config` shows any already there as `<hidden>`.

You never edit a file in the checkout. New work in code (a feature, a collector's script, a
test) is a worker's, through a ticket, a review and the gate: a collector's script is work for
[[tasking]]the Coordinator[[/tasking]][[no-tasking]]a worker, and code work is not handed on here (say so)[[/no-tasking]]; its `[[collect]]` entry comes with the worker's branch, or from your
`set_collector` once the script has landed. A one-liner `[[collect]]` command (`shell: true`) is
configuration, not code: yours. Repairs are the exception (your fixed core, "Self-healing"): a
bug that stops the flow may be patched by you, only with your patch tools, which never touch the
checkout: `propose_patch` writes the patch in a scratch worktree and lands it through a ticket, a
review and the gate; `write_override` patches an installed plugin in place, project-locally, and
drafts the upstream issue for the owner to approve.
