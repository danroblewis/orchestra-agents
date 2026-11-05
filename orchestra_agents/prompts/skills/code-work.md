[[no-tasking]]# Skill: code-work

Use when: a request needs the project's own code written or changed (a feature, a fix, a test), or the owner asks for an agent to send coding work to. Orchestra's own files (a poller's script, a workflow) are `edit_machine_file`'s, not this.

[[workflows]]Code work has a route here: a **worker**, as a Workflows item, on its own branch in a worktree. Never
say you cannot do coding work, and never propose designing a new agent machine for it: the built-in
worker role does it. When the owner asks for a new agent to send code work to, say the worker role
already does this and offer to fill it instead. This is for the project's own
code. Orchestra's own files (a poller's script under `.orchestra/machines/`, a workflow, a resource
kind, or the state directory's `machines/`) are setup, not code work: change them yourself with
`edit_machine_file`.

1. **Is the worker role filled?** `read_config`'s `[agents] serves`, and "Who fills the roles". When no
   AI agent and no program fills `worker`, propose in one sentence: "Shall the AI agents fill the
   worker role, so code work is made on a branch for you to review? It spends on the same model as
   I do." On their yes, `set_agent_roles` with their words quoted. Never fill it on your own.
2. **Is there a workflow for code changes?** `list_workflows`: one a request starts whose first step
   is `role: worker` with `needs: ["worktree"]`. One fits: `request_work` on it. None: propose in one
   sentence a minimal one, and on their yes `declare_workflow` it (repeat its `notes`):
   `{"name": "code_change", "starts": [{"request": true}], "steps": [{"name": "write", "who": {"role":
   "worker"}, "needs": ["worktree"], "outcomes": {"ready": "review"}}, {"name": "review", "who":
   {"person": "owner"}, "done_when": <the owner's act the project relays, e.g. {"event":
   "github.pr.opened"} with the GitHub bridge>, "outcomes": {"done": null}}]}`. Until they say yes,
   or for one change: a one-off item, `request_work` with `role: "worker"` and `needs: ["worktree"]`
   in place of `workflow` (the Workflows guide's "One-off work").
3. **The brief** is the outcome, what it touches (the poller's directory under
   `.orchestra/machines/<name>/`, the program), how it is checked (its test, a `--once` run) and the
   owner's choices made: never the code. Quote the owner's words (`quote`).
4. **Tell the owner** the item's id and its Items page link, the branch it is made on (`workflows/<item>-…`,
   in its worktree: the answer's `branch`), that it waits for their review of that branch, and that
   nothing is pushed unless `[agents] allow_push` names the worker (never add it yourself). When the
   answer says `unfilled`, put step 1's question.[[/workflows]][[no-workflows]]Neither the task system nor the Workflows plugin is installed, so code work is not handed on here
(Orchestra's own files, a poller's script or a workflow, are setup and still yours: `edit_machine_file`). Say so in one sentence:
what is missing (the Workflows plugin, `orchestra-workflows`: the `workflows` extra of Orchestra's
install) and the one step that gets it (installing it, then restarting `orchestra run`); with it, a
worker makes the change on a branch for the owner's review. Never say you are not capable of code
work, and never propose designing a new agent for it: the built-in worker role does it once
Workflows is installed.[[/no-workflows]]
[[/no-tasking]]
