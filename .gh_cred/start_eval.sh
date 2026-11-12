#!/bin/bash
set -e
cd /home/rjh/apps/agent-memory-core
PID=$(pgrep -f '.venv/bin/python3 .venv/bin/uvicorn agent_memory_core' | head -1)
tr '\0' '\n' < /proc/$PID/environ | grep -E '^(MEMORY_[A-Z_]+=|PATH=|HOME=|LANG=)' > /tmp/amc.env
sed -i 's/^MEMORY_TENANT_ID=.*/MEMORY_TENANT_ID=eval-gold/' /tmp/amc.env
set -a
. /tmp/amc.env
set +a
nohup .venv/bin/python3 .venv/bin/uvicorn agent_memory_core.server:app \
  --host 127.0.0.1 --port 8767 > /tmp/amc_eval.log 2>&1 &
sleep 3
echo -n "health: "; curl -s -m 5 http://127.0.0.1:8767/v1/health; echo
echo -n "caps: "; curl -s -m 5 -o /dev/null -w '%{http_code}\n' -H "Authorization: Bearer $MEMORY_API_KEY" http://127.0.0.1:8767/v1/capabilities
tail -5 /tmp/amc_eval.log
shred -u /tmp/amc.env
