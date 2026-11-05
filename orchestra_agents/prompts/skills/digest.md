# Skill: digest

Use when: the owner asks what changed since a time ("since I left", "since 3pm", "since yesterday"), or says "more" after it.

- When the owner asks what changed since a time ("since I left", "since 3pm", "since yesterday"),
  call `read_events` once, with `since` (the time, or `24h`), and reply with its `digest` exactly as
  returned: no rewording, no additions, no omissions, then nothing. The digest is the whole answer,
  written by Orchestra from every event in the window and the open items now (what looks wrong
  now first, each class counted and its lists cut with "… and K more", newest first, each time in
  the pages' zone, what waits on the owner, each prediction's state), and its last line is the
  read's `closing_line`, the model line. When the owner then says "more", call `read_events` again
  with the same `since` and `full` true, and reply with that `digest` the same way.
