# Skill: github

Use when: the owner asks to follow GitHub pull requests, or a ticket's steps wait on pull requests nothing relates to it.

- Connecting GitHub is `integrate_system` first (the `connect-system` skill): the repositories the
  owner named, GitHub's API address, and the token file's path they gave (never the token).
- When the owner's goal needs a link the data lacks (a ticket's steps wait on its pull requests'
  events, `of: children`, and nothing relates a pull request to its ticket), the waiting is the
  signal: never silently remove the goal by re-declaring the workflow without the relation
  (`declare_workflow` refuses it without the owner's words about the link). The linker is the
  relator the Workflows plugin ships, `relate_prs`: [[act]]declare it with `declare_program` and your
  reason, and say so in one line[[/act]][[ask]]propose it in one sentence, and on their yes
  declare it with `declare_program`[[/ask]] (the `workflows` skill has the words).
- Your own pull request (the branch your setup changes go to) is the `pr-workflow` skill.
