# Skill: review-change

Use when: a Workflows offer asks you to review a change (its outcomes include `approved`).

1. Read the brief, the item's fields below and the change: in the worktree you were handed, `git log`
   and `git diff` against the base (`git merge-base HEAD origin/main`); without one, read only.
2. Judge it as the owner would before showing it to anyone: does it do what the brief asks, is it
   tested (run the tests), does it match the code around it, is `pr_title`/`pr_body` (when written)
   true to the diff, is there anything embarrassing (debug output, dead code, unrelated changes).
3. Never change the code and never ask the owner. Report with your exit tool:
   - `approved` when you would show it as it is;
   - `changes` (or the offer's word for it) with `fields` `{"review_notes": "..."}`: each problem
     on its own line, with the file and what to do. The worker reads these on rework, so make them
     enough to act on alone.
