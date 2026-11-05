# Skill: code-change

Use when: a Workflows offer hands you a worktree to change the project's code (any workflow, any step).

1. **Read first.** The brief, the item's fields below, the code near what you will change and the
   repository's `AGENTS.md` and `README.md`. A Jira key in the fields (`jira_key`, `ticket`,
   `tickets`, a key in the brief): read the ticket with the tools your session has, read only.
2. **Rework comes with notes.** When the item comes back to you, the notes are in its fields:
   `review_notes` (the AI reviewer), `ci_notes` (a failed CI), `pr_comments` (the pull request's
   reviewers), `triage_notes` (a rejected SAIL), or any other `*_notes`. Read them before anything
   else and fix every point. When it came back with a pull request (`pr_number`) and no notes, read
   what was said there yourself, read only: `gh pr view <pr_number> --comments`, `gh pr checks
   <pr_number>`.
3. **Work out the requirements yourself.** Never ask the owner: they review only the final pull
   request. Where the brief is open, pick the reading that fits the code and say which in the body.
4. **Make the change** in the worktree, matching the code around it; add or update a test. Run the
   project's tests where you can (say which, and what passed). Commit on the branch you are on,
   leave it clean, and never push or open a pull request: the workflow's `open_pr` step does, and
   pushes your new commits to the same pull request on rework.
5. **Write the pull request's text** into the item with your exit tool's `fields`:
   - `pr_title`: one line, imperative, under 72 characters, the ticket's key first when there is one
     (`ABC-12: count the widgets on the report page`);
   - `pr_body`: what changed and why, in a few sentences; then `## Testing` (what you ran and what
     passed); then `## Risks` (what could break, or "none known"). Plain and specific: the owner reads
     it to decide whether they would be embarrassed to show it.
   On rework, write both again so they describe the whole branch as it is now.
6. Then report the outcome the offer lists (with `blocked` and why only when you truly cannot).
