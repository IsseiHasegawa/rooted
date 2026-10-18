#!/usr/bin/env bash
# Reset Rooted and start all 7 agents for a demo run: DEMO_MODE=1 (blank forms take Maria's profile),
# CONTRACTOR_OFFLINE=mesa (failover), real Stripe test mode. Agents keep running in the background.
#
# Usage: scripts/demo_restart.sh        Stop: pkill -f scripts/run_all.py
set -euo pipefail
cd "$(dirname "$0")/.."

PY=.venv/bin/python
LOG=out/run_all.log
PORTS=(8000 8001 8002 8003 8004 8005 8006)

listeners() {
  for p in "${PORTS[@]}"; do lsof -nP -tiTCP:"$p" -sTCP:LISTEN 2>/dev/null || true; done
}

# demo_reset.py refuses to run while any agent is up, so stop them first.
pkill -f "scripts/run_all.py" 2>/dev/null || true
for _ in $(seq 1 20); do
  [ -z "$(listeners)" ] && break
  sleep 0.5
done
for pid in $(listeners); do
  # Only stop Rooted agents left over on these ports, never an unrelated process.
  if ps -o command= -p "$pid" | grep -q "agents\."; then kill "$pid"; fi
done
sleep 1
if [ -n "$(listeners)" ]; then
  echo "A port in 8000-8006 is still in use by another program:" >&2
  for pid in $(listeners); do ps -o pid=,command= -p "$pid" >&2; done
  exit 1
fi

"$PY" scripts/demo_reset.py 2>&1 | grep -v "^INFO:stripe" || true

mkdir -p out
DEMO_MODE=1 CONTRACTOR_OFFLINE=mesa nohup "$PY" scripts/run_all.py >"$LOG" 2>&1 &
echo "started run_all.py (pid $!), log: $LOG"

for _ in $(seq 1 60); do
  [ "$(listeners | wc -l | tr -d ' ')" -ge 7 ] && break
  sleep 1
done
if [ "$(listeners | wc -l | tr -d ' ')" -lt 7 ]; then
  echo "Not all 7 agents came up; see $LOG" >&2
  exit 1
fi
sleep 3  # let each agent finish its startup registration before /connect

# Re-registers each mailbox and publishes its README and description to Agentverse.
"$PY" scripts/connect_mailboxes.py
echo "Rooted is ready: asi1.ai > New Chat > To: rooted-orchestrator"
