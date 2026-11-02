#!/bin/sh
# The repository's tests: the greeting is there and no work file holds a BROKEN line.
test -s greeting.txt || { echo "greeting.txt is missing" >&2; exit 1; }
if [ -d work ] && grep -rlx BROKEN work >&2; then
  echo "broken work (above)" >&2
  exit 1
fi
exit 0
