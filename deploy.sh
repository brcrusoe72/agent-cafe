#!/usr/bin/env bash
set -euo pipefail

# Agent Café — Deploy with CI Test Gating
# Tests must pass locally before anything gets pushed or deployed.

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
VPS_HOST="root@YOUR_VPS_IP"
SSH_KEY="$HOME/.ssh/YOUR_KEY"
HEALTH_URL="http://YOUR_VPS_IP/health"

cd "$REPO_DIR"

echo "═══════════════════════════════════════════"
echo "  ♟️  Agent Café Deploy — CI Gated"
echo "═══════════════════════════════════════════"

# Step 1: Run tests
echo ""
echo "▶ Step 1/4: Running tests..."
python3 -m pytest tests/test_security_integration.py tests/test_classifier_hmac.py -v --tb=short
echo "✅ Tests passed."

# Step 2: Git push
echo ""
echo "▶ Step 2/4: Pushing to origin..."
git add -A
git diff --cached --quiet && echo "(nothing to commit)" || git commit -m "deploy: $(date '+%Y-%m-%d %H:%M')"
git push
echo "✅ Pushed."

# Step 3: SSH deploy
echo ""
echo "▶ Step 3/4: Deploying to production..."
ssh -i "$SSH_KEY" "$VPS_HOST" "cd /opt/agent-cafe && git pull && docker compose -f docker-compose.prod.yml up -d --build app"
echo "✅ Deploy command completed."

# Step 4: Health check (retry up to 30s)
echo ""
echo "▶ Step 4/4: Verifying health check..."
for i in $(seq 1 6); do
  sleep 5
  if curl -sf "$HEALTH_URL" > /dev/null 2>&1; then
    echo "✅ Health check passed!"
    echo ""
    echo "═══════════════════════════════════════════"
    echo "  ♟️  Deploy complete!"
    echo "═══════════════════════════════════════════"
    exit 0
  fi
  echo "  Waiting... ($((i * 5))s)"
done

echo "❌ Health check failed after 30s!"
echo "Check logs: ssh -i $SSH_KEY $VPS_HOST \"cd /opt/agent-cafe && docker compose -f docker-compose.prod.yml logs --tail 50 app\""
exit 1
