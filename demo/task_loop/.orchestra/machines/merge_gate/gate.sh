#!/bin/sh
# merge_gate: land the item's branch on the base when the repository's tests pass on the merge.
#   gate.sh <base> <test command>
# The program host runs it with the item's worktree in ORCHESTRA_RESOURCE_WORKTREE (on the item's
# branch). It squashes the branch onto the fetched base (one commit per item), runs the test command
# on the result and pushes it to origin as the new base: a fast-forward, refused if the base moved.
# Exit 0 landed, 1 failed (the tests), 2 conflict (the merge or the push), else an error. The
# worktree is left clean on the item's branch whatever happens.
set -u
base=${1:-main}
shift
tests=${*:-sh test.sh}
wt=${ORCHESTRA_RESOURCE_WORKTREE:?no worktree was handed to the gate}
cd "$wt" || exit 3
branch=$(git symbolic-ref --short -q HEAD) || { echo "the worktree is on no branch"; exit 3; }
back() { git reset -q --hard; git clean -q -fd; git checkout -q "$branch"; }
git fetch -q origin "$base" || { echo "cannot fetch origin/$base"; exit 3; }
git checkout -q --detach "origin/$base" || exit 3
if ! git merge -q --squash "$branch" >/dev/null 2>&1; then
  back; echo "conflict: $branch does not merge onto $base"; exit 2
fi
if ! git -c user.name=merge_gate -c user.email=merge_gate@example.invalid \
     commit -q -m "${ORCHESTRA_ITEM:-item}: land $branch"; then
  back; echo "nothing to land on $branch"; exit 3
fi
if ! sh -c "$tests" >/dev/null 2>&1; then
  back; echo "failed: '$tests' on $branch merged onto $base"; exit 1
fi
if ! git push -q origin "HEAD:refs/heads/$base"; then
  back; echo "conflict: $base moved while $branch was tested"; exit 2
fi
landed=$(git rev-parse --short HEAD)
back
echo "landed $branch on $base as $landed"
