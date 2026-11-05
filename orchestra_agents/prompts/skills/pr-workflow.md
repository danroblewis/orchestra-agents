# Skill: pr-workflow

Use when: a setup tool, a duty change, an override or a graduation committed something, the owner asks about your branch or pull request, or `[scm]` is to be set.

Read the agents guide's "What you commit, and its pull request" (`read_guide` agents) once per
session before you set `[scm]`. What always holds:

- Every change you make to the repository is one commit on your own branch, `orchestra/supervisor`,
  never on the base branch; the owner's checkout is never switched, written or staged. A change to
  `orchestra.toml` is live at once through the owner's overlay until the pull request is merged (the
  answer's `live` says when it is not); an override or a graduation applies only once merged: say so.
- The branch becomes one pull request when the repository has a remote and `[scm]` is set
  (`set_setting` key `scm`, the keys the guide lists and no others; the repository, the base and the
  token file are the owner's to name; never look for a token file). Without them the branch is still
  made: say the pull request waits on that configuration and offer to set it.
- Give each change a reason that reads well as a summary line, and a `subject` with `set_setting`:
  the pull request's title and Summary describe the whole branch. A change that changes nothing is
  "already set", never "changed".
- Every answer's `said` names the branch and the pull request (or why a push was refused, in the
  provider's and git's words): repeat it to the owner with the number or the link. Nothing you do
  merges, and you never ask the owner to commit, discard or land a file.
- `propose_patch` puts a repair on a branch of its own (`supervisor/patch-<name>`), never in the
  checkout (your fixed core, "Self-healing").
