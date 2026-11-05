# Skill: workflows

Use when: the owner describes how a piece of work moves (its steps and who does each: `declare_workflow`), or a goal or workflow's status is asked or said.

- When the owner describes how a piece of work moves (its steps and who does each), and the
  Workflows plugin is installed, write it as a workflow with `declare_workflow`, never as a file
  or a ticket: the agents guide's "Workflows" section (`read_guide`) has the words and an example.

- When the owner's goal needs a link the data lacks (a ticket's steps wait on its pull requests'
  events, `of: children`, and nothing relates a pull request to its ticket, so tickets wait at that
  step), the waiting is the signal: never silently remove the goal by re-declaring the workflow
  without the relation (`declare_workflow` refuses it without the owner's words about the link).
  The linker is the relator the Workflows plugin ships, `relate_prs` (its guide, "Relating pull
  requests to tickets"), which publishes `item.related {kind: parent}`: propose it in one sentence, "Each pull request is related to its ticket by
  the `HK-123` key in its branch or title, by the relator the Workflows plugin ships; one with no key
  is shown as unrelated: shall I declare it?", and on their yes declare it with `declare_program`
  (GitHub's API address and the token file's path the owner gave, in its `args`). Never ask for one
  to be written. A general go-ahead ("proceed as you think best") is not their answer to that.
  Say a goal or its workflow is running only as `declare_workflow`'s or `list_workflows`' `status`
  says: while a step waits for its program, say it waits and what declares it, never "fully
  running"; repeat every `notes` line `declare_workflow` answers. Before you say what items carry ("each carrying the
  ticket key"), a bound or an interval, read it (`explain_item`, the tool's own answer): never claim
  one a tool has not shown you.

- A system just connected that has items (tickets, incidents, threads): the `connect-system`
  skill's last paragraph proposes the workflow that makes them items, observe only.
