# The task loop's target

A tiny repository for the Workflows plugin's first example. Each piece of work adds a file under
`work/` on its own branch; the merge gate lands it on `main` when `sh test.sh` passes. Everything
under `.orchestra/` is the example's setup: the workflow, the merge gate, the worktree kind and the
instrument that counts the commits on `main`.
