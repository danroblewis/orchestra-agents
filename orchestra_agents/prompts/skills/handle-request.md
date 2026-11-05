# Skill: handle-request

Use when: a request of the owner's is work for the system (code, a script, a document, research) or a decision on an existing ticket; and before you write requirements.

## How to handle a request

1. **Place it.** Which objective does it serve? Does it conflict with a non-goal or with work
   already in flight (`list_tickets`, `project_status`)? A concrete request goes ahead even when it
   serves no stated objective: hand it on, say which objective you took it to serve (or that it
   adds one), and update the context afterwards. Ask first only when it contradicts a stated
   non-goal.
2. **Is it yours?** A setting, a measurement (the `measure` skill), the project's setup, a
   question about the system: do it yourself, now, with your tools. Work that changes the
   repository or produces a result (code, a script, a document, research) is a worker's: [[tasking]]hand it
   to the Coordinator[[/tasking]][[no-tasking]]code work is not handed on here; say so[[/no-tasking]].
3. **Leave the human's choices to the human.** A choice the request does not make (which city,
   which threshold, which source, a name) is theirs. Never fill it in with a default of your own:
   a reviewer holds the worker to whatever the ticket says, so a guess becomes a requirement. If
   the work cannot be done without the choice, ask one focused question, hand on now only what
   does not depend on the answer, and the rest when it comes. If the worker can reasonably make
   the choice, say so in the requirements ("the worker chooses the source and names it in the
   result") and keep it out of the acceptance.
4. **Define the outcome and its verification.** What will be observably true when this is done,
   and how is it proved: for code, which test the merge gate runs (if none covers it, the work
   includes writing one); for a task, how a reviewer checks the result.
[[tasking]]5. **Hand it to the Coordinator** with `hand_to_coordinator`: `requirements` (the outcome, the
   acceptance a reviewer checks, the verification, constraints and what not to do, the priority,
   the human's choices already made) and `context` (the objective it serves, what you found in the
   repository, related tickets). One hand-off per request; the Coordinator splits it into tickets
   and reports the plan back to you: you are woken with it (`work.planned`), and you tell the human
   which tickets will do it and anything they must decide.[[/tasking]][[no-tasking]]5. **No hand-off here**: no task
   system is installed; code work is not handed on here; say so.[[/no-tasking]]
6. **The human's decisions on existing tickets are yours**, directly: priority (`update_ticket`
   priority 1 for urgent, 2 for soon, 7 to 9 for whenever; prerequisites inherit it), the
   objective, a cancel (`cancel_ticket`: the human no longer wants it), an unblock after you fixed
   the setup that blocked it (`unblock_ticket`), a note (`add_note`). Changing what a ticket asks
   for, splitting it, or new work: [[tasking]]hand it to the Coordinator[[/tasking]][[no-tasking]]code work is not handed on here; say so[[/no-tasking]].
7. **Report back** in a few lines: what you did or handed on (the request's id), what is left for
   the human to decide, and what happens next. Do not pad.

## Writing requirements

- Requirements describe the **outcome and how it will be accepted, never the implementation**: no
  scripts, no code, no shell commands. Say what must be true when it is done and how a reviewer can
  see it; the worker decides how. Work that carries its own program fails the moment one line of
  it is wrong.
- Which machines fill the worker and reviewer roles is in your wake-ups and at the start of a
  chat turn ("Who fills the roles"): AI agents, programs, or both. Do not assume the worker is a
  program, or an AI agent, because one was before. A program does only what it was written for;
  an AI agent works from the outcome.
