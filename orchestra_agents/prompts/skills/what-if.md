# Skill: what-if

Use when: the owner asks what a change would do ("what would happen if…"), or before a change you expect to move a measured stock.

## "What would happen if…"

When the human asks what a change would do (reviews twice as fast, another worker, a new
machine), ask the model rather than guess. Read `modeling_guide` once; then read the model
(`read_model`, `read_eras`, `read_series`) and answer with the key numbers and what was not
trusted. A designed machine's expected effect is declared and confirmed with the design tools
(`declare_expected_effect`, `confirm`). Before a configuration change you expect to move a
measured stock, `predict_fix` and pass its `change` id to the setup tool; the verdict wakes you,
and you tell the human what it said.
