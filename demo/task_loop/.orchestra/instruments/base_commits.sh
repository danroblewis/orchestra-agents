#!/bin/sh
# The domain series: the commits on the base branch where the repository keeps it (origin), as
# Prometheus text. A measurement of the repository, not of the workflow: it never reads the bus.
#   base_commits.sh <base>
base=${1:-main}
origin=$(git config --get remote.origin.url) || exit 1
n=$(git --git-dir="$origin" rev-list --count "refs/heads/$base") || exit 1
echo "# HELP git_branch_commits Commits on a branch of the repository's origin."
echo "# TYPE git_branch_commits gauge"
echo "git_branch_commits{branch=\"$base\"} $n"
