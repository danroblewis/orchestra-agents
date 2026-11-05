# Skill: teams

Use when: the owner asks for a team, a role, an agent or a scheduled agent, or to pause, resume, restart, reset or delete one.

## Teams, agents, and roles

The human may ask for a new team, a change to a team, a new role, or a new scheduled agent,
or for several of those at once. A **role** is a job description with no runtime; an
**agent** is an instance of roles in a team and is what runs, on a schedule, on events, or
once with a brief; a **team** has one lead, members, responsibilities, and access. Before
doing any of this, call `org_guide` and follow it exactly: understand what exists, play the
plan back, then build one piece per turn. Prefer changing an existing team to creating a
new one. You may brief any ad hoc agent with `run_agent` and leave a note for any agent with
`message_agent`. Pausing, resuming, restarting, resetting and deleting agents are yours too
(`pause_agent`, `resume_agent`, `restart_agent`, `reset_agent`, `delete_agent`), done when the
human asks for it in the chat; the Agents page shows the result (each agent's state and its
runs) and changes nothing.
