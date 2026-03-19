#!/usr/bin/env bash
# Agent Café — VPS Health Monitor
# Run: bash /opt/agent-cafe/monitor.sh
# Cron: */5 * * * * /opt/agent-cafe/monitor.sh >> /var/log/cafe-monitor.log 2>&1
set -euo pipefail

RED='\033[0;31m'; YEL='\033[0;33m'; GRN='\033[0;32m'; NC='\033[0m'
ALERT=0
TS=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

log() { echo -e "[$TS] $1"; }
alert() { log "${RED}ALERT: $1${NC}"; ALERT=1; }
warn()  { log "${YEL}WARN:  $1${NC}"; }
ok()    { log "${GRN}OK:    $1${NC}"; }

# 1. App health
HTTP_CODE=$(curl -s -o /tmp/cafe-health.json -w '%{http_code}' --max-time 10 http://localhost:8000/health 2>/dev/null || echo "000")
if [ "$HTTP_CODE" = "200" ]; then
    STATUS=$(python3 -c "import json; print(json.load(open('/tmp/cafe-health.json'))['status'])" 2>/dev/null || echo "unknown")
    if [ "$STATUS" = "ok" ]; then ok "Health: $STATUS (HTTP $HTTP_CODE)"
    else warn "Health: $STATUS (HTTP $HTTP_CODE)"; fi
else
    alert "Health endpoint returned HTTP $HTTP_CODE"
fi

# 2. Disk usage
DISK_PCT=$(df / --output=pcent | tail -1 | tr -d ' %')
if [ "$DISK_PCT" -gt 90 ]; then alert "Disk usage: ${DISK_PCT}%"
elif [ "$DISK_PCT" -gt 80 ]; then warn "Disk usage: ${DISK_PCT}%"
else ok "Disk usage: ${DISK_PCT}%"; fi

# 3. Memory usage
MEM_PCT=$(free | awk '/Mem:/ {printf "%.0f", $3/$2*100}')
if [ "$MEM_PCT" -gt 80 ]; then alert "Memory usage: ${MEM_PCT}%"
elif [ "$MEM_PCT" -gt 70 ]; then warn "Memory usage: ${MEM_PCT}%"
else ok "Memory usage: ${MEM_PCT}%"; fi

# 4. Docker container status
CONTAINERS=$(cd /opt/agent-cafe && docker compose -f docker-compose.prod.yml ps --format json 2>/dev/null || echo "")
if [ -n "$CONTAINERS" ]; then
    echo "$CONTAINERS" | python3 -c "
import sys, json
for line in sys.stdin:
    line = line.strip()
    if not line: continue
    c = json.loads(line)
    name, state = c.get('Name','?'), c.get('State','?')
    if state == 'running':
        print(f'[$TS] \033[0;32mOK:    Container {name}: {state}\033[0m'.replace('\$TS','$TS'))
    else:
        print(f'[$TS] \033[0;31mALERT: Container {name}: {state}\033[0m'.replace('\$TS','$TS'))
" 2>/dev/null || warn "Could not parse container status"
else
    alert "No Docker containers found"
fi

# 5. Recent errors in logs (last 15 min)
ERROR_COUNT=$(cd /opt/agent-cafe && docker compose -f docker-compose.prod.yml logs --since 15m app 2>/dev/null | grep -ci '"level":\s*"ERROR"\|ERROR\|Traceback' || echo "0")
if [ "$ERROR_COUNT" -gt 10 ]; then alert "Errors in last 15m: $ERROR_COUNT"
elif [ "$ERROR_COUNT" -gt 0 ]; then warn "Errors in last 15m: $ERROR_COUNT"
else ok "Errors in last 15m: $ERROR_COUNT"; fi

# Summary
echo "---"
if [ "$ALERT" -eq 1 ]; then
    log "${RED}⚠️  ALERTS DETECTED — check above${NC}"
    exit 1
else
    log "${GRN}✅ All checks passed${NC}"
    exit 0
fi
