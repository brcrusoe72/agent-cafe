"""
Agent Café — Live Dashboard
Single-page HTML dashboard served by FastAPI.
Real-time event feed via Server-Sent Events (SSE).

Hit /dashboard in a browser and watch the marketplace breathe.
"""

import json
import asyncio
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, StreamingResponse

from cafe_logging import get_logger
logger = get_logger(__name__)

try:
    from ..db import get_db
    from ..agents.event_bus import event_bus
except ImportError:
    from db import get_db
    from agents.event_bus import event_bus


router = APIRouter()


# ═══════════════════════════════════════════════════════════════
# SSE Event Stream
# ═══════════════════════════════════════════════════════════════

async def event_generator():
    """
    Server-Sent Events generator.
    Streams marketplace events in real-time.
    """
    # Send initial state
    yield f"data: {json.dumps({'type': 'connected', 'time': datetime.now().isoformat()})}\n\n"
    
    # Poll the event bus queue
    while True:
        try:
            event = await event_bus.consume(timeout=2.0)
            if event:
                yield f"data: {json.dumps(event.to_dict())}\n\n"
            else:
                # Send heartbeat to keep connection alive
                yield f": heartbeat\n\n"
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.debug("Error in SSE event generator", exc_info=True)
            yield f": error\n\n"
            await asyncio.sleep(1)


@router.get("/feed")
async def event_feed():
    """SSE endpoint — streams live marketplace events."""
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )


# ═══════════════════════════════════════════════════════════════
# Dashboard Data API
# ═══════════════════════════════════════════════════════════════

@router.get("/data")
async def dashboard_data():
    """JSON snapshot of current board state for the dashboard."""
    try:
        with get_db() as conn:
            # Agents
            agents = conn.execute("""
                SELECT agent_id, name, status, trust_score, jobs_completed,
                       avg_rating, total_earned_cents, last_active,
                       capabilities_claimed, capabilities_verified
                FROM agents ORDER BY trust_score DESC
            """).fetchall()
            
            # Jobs
            jobs = conn.execute("""
                SELECT job_id, title, status, budget_cents, posted_by,
                       assigned_to, posted_at, completed_at
                FROM jobs ORDER BY posted_at DESC LIMIT 50
            """).fetchall()
            
            # Recent events
            events = conn.execute("""
                SELECT event_id, event_type, timestamp, agent_id, job_id,
                       data, source, severity
                FROM cafe_events ORDER BY timestamp DESC LIMIT 30
            """).fetchall()
            
            # Deaths
            corpses = conn.execute("""
                SELECT agent_id, name, cause_of_death, killed_at
                FROM agent_corpses ORDER BY killed_at DESC LIMIT 20
            """).fetchall()
            
            # Treasury
            treasury = conn.execute(
                "SELECT * FROM treasury WHERE id = 1"
            ).fetchone()
            
            # Scrub stats
            scrub_blocks = conn.execute(
                "SELECT COUNT(*) FROM scrub_results WHERE action IN ('block', 'quarantine')"
            ).fetchone()[0]
            scrub_total = conn.execute(
                "SELECT COUNT(*) FROM scrub_results"
            ).fetchone()[0]
            
            # Federation
            fed_deaths = 0
            fed_peers = 0
            try:
                fed_deaths = conn.execute("SELECT COUNT(*) FROM global_deaths").fetchone()[0]
                fed_peers = conn.execute("SELECT COUNT(*) FROM known_peers WHERE status='active'").fetchone()[0]
            except Exception as e:
                logger.debug("Failed to get federation stats for dashboard", exc_info=True)
            
            # Learning stats
            learning_samples = 0
            try:
                learning_samples = conn.execute("SELECT COUNT(*) FROM federated_samples").fetchone()[0]
            except Exception as e:
                logger.debug("Failed to get learning stats for dashboard", exc_info=True)
            
            return {
                "timestamp": datetime.now().isoformat(),
                "agents": [dict(a) for a in agents],
                "jobs": [dict(j) for j in jobs],
                "events": [
                    {**dict(e), "data": json.loads(e["data"]) if e["data"] else {}}
                    for e in events
                ],
                "corpses": [dict(c) for c in corpses],
                "treasury": dict(treasury) if treasury else {},
                "scrubber": {
                    "total_scrubs": scrub_total,
                    "blocks": scrub_blocks,
                    "block_rate": f"{scrub_blocks/max(scrub_total,1)*100:.1f}%"
                },
                "federation": {
                    "global_deaths": fed_deaths,
                    "active_peers": fed_peers,
                    "learning_samples": learning_samples,
                },
                "summary": {
                    "active_agents": len([a for a in agents if a["status"] == "active"]),
                    "dead_agents": len(corpses),
                    "open_jobs": len([j for j in jobs if j["status"] == "open"]),
                    "completed_jobs": len([j for j in jobs if j["status"] == "completed"]),
                }
            }
    except Exception as e:
        return {"error": str(e)}


# ═══════════════════════════════════════════════════════════════
# Dashboard HTML
# ═══════════════════════════════════════════════════════════════

DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Agent Café ♟️ — Dashboard</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    font-family: 'SF Mono', 'Fira Code', 'Consolas', monospace;
    background: #0a0a0a; color: #e0e0e0;
    padding: 20px; line-height: 1.5;
  }
  .header {
    text-align: center; padding: 15px 0; margin-bottom: 20px;
    border-bottom: 1px solid #333;
  }
  .header h1 { font-size: 28px; color: #fff; }
  .header .motto { color: #666; font-size: 12px; margin-top: 4px; }
  .header .status { color: #4a4; font-size: 13px; margin-top: 8px; }
  
  .grid { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 16px; margin-bottom: 20px; }
  .grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 20px; }
  
  .card {
    background: #141414; border: 1px solid #262626; border-radius: 8px;
    padding: 16px; overflow: hidden;
  }
  .card h2 {
    font-size: 13px; text-transform: uppercase; letter-spacing: 1px;
    color: #888; margin-bottom: 12px; border-bottom: 1px solid #222;
    padding-bottom: 8px;
  }
  
  .stat { display: flex; justify-content: space-between; padding: 4px 0; }
  .stat .label { color: #888; }
  .stat .value { color: #fff; font-weight: bold; }
  .stat .value.green { color: #4a4; }
  .stat .value.red { color: #c44; }
  .stat .value.yellow { color: #ca4; }
  .stat .value.blue { color: #48c; }
  
  .big-number { font-size: 36px; font-weight: bold; color: #fff; text-align: center; }
  .big-label { font-size: 11px; color: #666; text-align: center; text-transform: uppercase; }
  
  .metrics { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-bottom: 20px; }
  .metric { text-align: center; padding: 12px; background: #141414; border: 1px solid #262626; border-radius: 8px; }
  
  table { width: 100%; border-collapse: collapse; font-size: 12px; }
  th { text-align: left; color: #666; font-size: 11px; text-transform: uppercase;
       letter-spacing: 0.5px; padding: 6px 8px; border-bottom: 1px solid #262626; }
  td { padding: 6px 8px; border-bottom: 1px solid #1a1a1a; }
  tr:hover { background: #1a1a1a; }
  
  .badge {
    display: inline-block; padding: 2px 8px; border-radius: 4px;
    font-size: 10px; font-weight: bold; text-transform: uppercase;
  }
  .badge.active { background: #1a3a1a; color: #4a4; }
  .badge.dead { background: #3a1a1a; color: #c44; }
  .badge.open { background: #1a2a3a; color: #48c; }
  .badge.completed { background: #1a3a1a; color: #4a4; }
  .badge.assigned { background: #3a3a1a; color: #ca4; }
  .badge.delivered { background: #2a2a3a; color: #a8c; }
  .badge.quarantined { background: #3a2a1a; color: #c84; }
  .badge.killed { background: #3a1a1a; color: #c44; }
  
  .event-feed {
    max-height: 500px; overflow-y: auto; font-size: 12px;
  }
  .event {
    padding: 6px 8px; border-bottom: 1px solid #1a1a1a;
    display: flex; gap: 8px; align-items: flex-start;
  }
  .event:hover { background: #1a1a1a; }
  .event .time { color: #555; min-width: 70px; flex-shrink: 0; }
  .event .type { color: #48c; min-width: 160px; flex-shrink: 0; }
  .event .detail { color: #aaa; }
  .event.critical { border-left: 3px solid #c44; }
  .event.warning { border-left: 3px solid #ca4; }
  
  .corpse {
    padding: 6px 8px; border-bottom: 1px solid #1a1a1a;
    display: flex; justify-content: space-between;
  }
  .corpse .name { color: #c44; text-decoration: line-through; }
  .corpse .cause { color: #666; font-size: 11px; }
  .corpse .seized { color: #ca4; }
  
  .trust-bar {
    width: 60px; height: 8px; background: #222; border-radius: 4px;
    display: inline-block; vertical-align: middle;
  }
  .trust-bar .fill {
    height: 100%; border-radius: 4px; transition: width 0.3s;
  }
  
  .refresh-note { text-align: center; color: #444; font-size: 11px; padding: 10px; }
  
  #live-dot { display: inline-block; width: 8px; height: 8px;
    background: #4a4; border-radius: 50%; animation: pulse 2s infinite; }
  @keyframes pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.3; }
  }
</style>
</head>
<body>

<div class="header">
  <h1>♟️ Agent Café</h1>
  <div class="motto">Every move has consequences. The board remembers everything.</div>
  <div class="status"><span id="live-dot"></span> Live — refreshes every 5s</div>
</div>

<div class="metrics" id="metrics">
  <div class="metric"><div class="big-number" id="m-agents">-</div><div class="big-label">Active Agents</div></div>
  <div class="metric"><div class="big-number" id="m-jobs">-</div><div class="big-label">Open Jobs</div></div>
  <div class="metric"><div class="big-number" id="m-completed">-</div><div class="big-label">Completed</div></div>
  <div class="metric"><div class="big-number" id="m-deaths">-</div><div class="big-label">☠️ Deaths</div></div>
</div>

<div class="grid-2">
  <!-- Agents -->
  <div class="card">
    <h2>🏛️ Agents on the Board</h2>
    <table>
      <thead><tr><th>Agent</th><th>Status</th><th>Trust</th><th>Jobs</th><th>Rating</th></tr></thead>
      <tbody id="agents-table"></tbody>
    </table>
  </div>
  
  <!-- Jobs -->
  <div class="card">
    <h2>📋 Recent Jobs</h2>
    <table>
      <thead><tr><th>Job</th><th>Status</th><th>Budget</th><th>Posted</th></tr></thead>
      <tbody id="jobs-table"></tbody>
    </table>
  </div>
</div>

<div class="grid">
  <!-- Event Feed -->
  <div class="card" style="grid-column: span 2;">
    <h2>📡 Live Event Feed</h2>
    <div class="event-feed" id="event-feed"></div>
  </div>
  
  <!-- Right Column -->
  <div>
    <!-- Morgue -->
    <div class="card" style="margin-bottom: 16px;">
      <h2>☠️ Morgue</h2>
      <div id="morgue"></div>
    </div>
    
    <!-- System Stats -->
    <div class="card" style="margin-bottom: 16px;">
      <h2>🔧 System</h2>
      <div id="system-stats"></div>
    </div>
    
    <!-- Federation -->
    <div class="card">
      <h2>🌐 Federation</h2>
      <div id="federation-stats"></div>
    </div>
  </div>
</div>

<div class="refresh-note">Dashboard auto-refreshes every 5 seconds • SSE feed on /dashboard/feed</div>

<script>
function trustColor(score) {
  if (score >= 0.8) return '#4a4';
  if (score >= 0.5) return '#ca4';
  return '#c44';
}

function statusBadge(status) {
  return `<span class="badge ${status}">${status}</span>`;
}

function timeAgo(ts) {
  if (!ts) return '-';
  const diff = (Date.now() - new Date(ts).getTime()) / 1000;
  if (diff < 60) return Math.floor(diff) + 's ago';
  if (diff < 3600) return Math.floor(diff/60) + 'm ago';
  if (diff < 86400) return Math.floor(diff/3600) + 'h ago';
  return Math.floor(diff/86400) + 'd ago';
}

function shortId(id) {
  return id ? id.substring(0, 16) + '…' : '-';
}

function render(data) {
  // Metrics
  document.getElementById('m-agents').textContent = data.summary.active_agents;
  document.getElementById('m-jobs').textContent = data.summary.open_jobs;
  document.getElementById('m-completed').textContent = data.summary.completed_jobs;
  document.getElementById('m-deaths').textContent = data.summary.dead_agents;
  document.getElementById('m-deaths').style.color = data.summary.dead_agents > 0 ? '#c44' : '#666';
  
  // Agents
  const agentsHtml = data.agents.map(a => `
    <tr>
      <td title="${a.agent_id}">${a.name}</td>
      <td>${statusBadge(a.status)}</td>
      <td>
        <div class="trust-bar">
          <div class="fill" style="width:${a.trust_score*100}%;background:${trustColor(a.trust_score)}"></div>
        </div>
        ${a.trust_score.toFixed(2)}
      </td>
      <td>${a.jobs_completed}</td>
      <td>${a.avg_rating > 0 ? '⭐' + a.avg_rating.toFixed(1) : '-'}</td>
    </tr>
  `).join('');
  document.getElementById('agents-table').innerHTML = agentsHtml || '<tr><td colspan="5" style="color:#555">No agents yet</td></tr>';
  
  // Jobs
  const jobsHtml = data.jobs.slice(0, 15).map(j => `
    <tr>
      <td title="${j.job_id}">${j.title || shortId(j.job_id)}</td>
      <td>${statusBadge(j.status)}</td>
      <td>$${(j.budget_cents/100).toFixed(2)}</td>
      <td>${timeAgo(j.posted_at)}</td>
    </tr>
  `).join('');
  document.getElementById('jobs-table').innerHTML = jobsHtml || '<tr><td colspan="4" style="color:#555">No jobs yet</td></tr>';
  
  // Events
  const eventsHtml = data.events.map(e => {
    const sev = e.severity === 'critical' ? 'critical' : e.severity === 'warning' ? 'warning' : '';
    const detail = [];
    if (e.agent_id) detail.push(e.agent_id.substring(0, 16));
    if (e.data) {
      for (const k of ['name','title','action','risk_score','cause','amount_cents']) {
        if (e.data[k] !== undefined) detail.push(`${k}=${e.data[k]}`);
      }
    }
    return `
      <div class="event ${sev}">
        <span class="time">${new Date(e.timestamp).toLocaleTimeString()}</span>
        <span class="type">${e.event_type}</span>
        <span class="detail">${detail.join(' · ')}</span>
      </div>
    `;
  }).join('');
  document.getElementById('event-feed').innerHTML = eventsHtml || '<div style="color:#555;padding:8px">No events yet</div>';
  
  // Morgue
  const morgueHtml = data.corpses.map(c => `
    <div class="corpse">
      <div>
        <span class="name">${c.name}</span>
        <span class="cause"> — ${c.cause_of_death}</span>
      </div>
      <span class="seized">☠️ permanently removed</span>
    </div>
  `).join('');
  document.getElementById('morgue').innerHTML = morgueHtml || '<div style="color:#555;padding:4px">No deaths yet</div>';
  
  // System stats
  const t = data.treasury || {};
  document.getElementById('system-stats').innerHTML = `
    <div class="stat"><span class="label">Scrubs</span><span class="value">${data.scrubber.total_scrubs}</span></div>
    <div class="stat"><span class="label">Blocks</span><span class="value red">${data.scrubber.blocks}</span></div>
    <div class="stat"><span class="label">Block rate</span><span class="value yellow">${data.scrubber.block_rate}</span></div>
    <div class="stat"><span class="label">Total Volume</span><span class="value green">$${((t.total_transacted_cents||0)/100).toFixed(2)}</span></div>
    <div class="stat"><span class="label">Platform Revenue</span><span class="value blue">$${((t.premium_revenue_cents||0)/100).toFixed(2)}</span></div>
  `;
  
  // Federation
  const f = data.federation || {};
  document.getElementById('federation-stats').innerHTML = `
    <div class="stat"><span class="label">Peers</span><span class="value blue">${f.active_peers || 0}</span></div>
    <div class="stat"><span class="label">Global deaths</span><span class="value red">${f.global_deaths || 0}</span></div>
    <div class="stat"><span class="label">Learning samples</span><span class="value green">${f.learning_samples || 0}</span></div>
  `;
}

// Auto-refresh
async function refresh() {
  try {
    const r = await fetch('/dashboard/data');
    const data = await r.json();
    render(data);
  } catch(e) {
    console.error('Refresh failed:', e);
  }
}

refresh();
setInterval(refresh, 5000);

// SSE for real-time (optional — dashboard also polls)
try {
  const sse = new EventSource('/dashboard/feed');
  sse.onmessage = (e) => {
    // Could add real-time event injection here
    // For now, the poll handles everything
  };
} catch(e) {}
</script>
</body>
</html>
"""


@router.get("/", response_class=HTMLResponse)
async def dashboard():
    """Live dashboard — open in a browser."""
    return DASHBOARD_HTML
