#!/bin/bash
# Restart the live demo as the Workflows task loop, without the task system: stop what restart.sh
# or this script started, re-sync both worktrees (the agents' one without orchestra-tasking), start
# the task loop fresh in ~/orchestra-live-demo/project on PORT (its coordinator keeps asking for
# work, so there is no tickets step), re-seed the declared layer. demo/README-task-loop.md.
set -u
D=$HOME/orchestra-live-demo
P=$D/project
PORT=${PORT:-8096}
AG=${AG:-$HOME/orchestra-agents-wt-demo}
CORE=$HOME/orchestra-wt/demo
BATCH=${BATCH:-5}
EVERY=${EVERY:-300}

stop_pid() {  # $1 = pid file
  [ -f "$1" ] || return 0
  pid=$(cat "$1")
  if kill -0 "$pid" 2>/dev/null; then
    kill -TERM "$pid"
    for _ in $(seq 1 90); do kill -0 "$pid" 2>/dev/null || break; sleep 1; done
    kill -0 "$pid" 2>/dev/null && kill -KILL "$pid"
  fi
  rm -f "$1"
}
stop_pid "$D/tickets.pid"    # restart.sh's ticket feeder, when switching over from it
stop_pid "$D/run.pid"
# anything left that was started in the project (the host's children run with cwd/args there)
for pid in $(pgrep -f "$P" 2>/dev/null); do kill -TERM "$pid" 2>/dev/null; done
sleep 2
for pid in $(pgrep -f "$P" 2>/dev/null); do kill -KILL "$pid" 2>/dev/null; done
for pid in $(lsof -nP -t -iTCP:$PORT -sTCP:LISTEN 2>/dev/null); do
  if ps -o command= -p "$pid" | grep -q "orchestra web --port $PORT"; then kill -TERM "$pid"; fi
done

(cd "$CORE" && uv sync --extra dev --extra sd -q) || exit 1
# the dev extra brings the Workflows plugin (and the bridges); its task system is left out, and
# uv sync, exact, removes it when an earlier sync installed it. Every uv run below is --no-sync,
# so none puts it back.
(cd "$AG" && uv sync --extra dev --no-install-package orchestra-tasking -q) || exit 1

rm -rf "$P"      # the task loop wants a fresh target (its origin is inside it) and removes its old state directory itself
cd "$AG"
nohup uv run --no-sync demo/run_task_loop.py "$P" --port "$PORT" --dummy --batch "$BATCH" --every "$EVERY" >"$D/run.log" 2>&1 </dev/null &
echo $! >"$D/run.pid"
for _ in $(seq 1 180); do grep -q "The work:" "$D/run.log" && break; sleep 1; done
grep -q "The work:" "$D/run.log" || { echo "demo did not come up; see $D/run.log"; exit 1; }

uv run --no-sync --project "$AG" python "$AG/demo/seed_task_loop.py" "$P" || exit 1

TOKEN=$(cd "$P" && ORCHESTRA_REPO="$P" uv run --no-sync --project "$AG" python -m orchestra web token)
umask 077
printf 'https://orchestra.dansproject.space/login?next=%%2Fmachines#token=%s\n' "$TOKEN" >"$D/LOGIN.txt"
chmod 600 "$D/LOGIN.txt"
unset TOKEN
echo "task loop up on :$PORT (pid $(cat "$D/run.pid")); $BATCH items every $EVERY s; login in $D/LOGIN.txt"
