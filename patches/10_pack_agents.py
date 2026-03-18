#!/usr/bin/env python3
"""
Patch 10: Wire Pack Agents into Main App

Adds pack agent startup/shutdown to main.py lifecycle.
Adds /pack/* operator endpoints for status and manual patrol triggers.
Copies pack agent files to VPS.
"""

import os
import shutil

CAFE_DIR = "/opt/agent-cafe"

# ============================================================
# STEP 1: Copy pack agent files
# ============================================================

pack_src = "/path/to/workspace/systems/agent-cafe/agents/pack"
pack_dst = os.path.join(CAFE_DIR, "agents/pack")

if os.path.exists(pack_dst):
    shutil.rmtree(pack_dst)
os.makedirs(pack_dst, exist_ok=True)

for f in ["__init__.py", "base.py", "wolf.py", "jackal.py", "hawk.py", "runner.py"]:
    src = os.path.join(pack_src, f)
    dst = os.path.join(pack_dst, f)
    if os.path.exists(src):
        shutil.copy2(src, dst)
        print(f"  Copied {f}")
    else:
        print(f"  WARNING: {src} not found")

# ============================================================
# STEP 2: Add pack startup to main.py
# ============================================================

main_path = os.path.join(CAFE_DIR, "main.py")
with open(main_path, "r") as f:
    content = f.read()

# Add pack startup after grandmaster
OLD_STARTUP = '''    # Start the Grandmaster (always-on)
    try:
        from agents.grandmaster import grandmaster
        await grandmaster.start()
    except Exception as e:
        logger.warning("Grandmaster failed to start: %s", e)'''

NEW_STARTUP = '''    # Start the Grandmaster (always-on)
    try:
        from agents.grandmaster import grandmaster
        await grandmaster.start()
    except Exception as e:
        logger.warning("Grandmaster failed to start: %s", e)
    
    # Start the Pack (Wolf, Jackal, Hawk — real agents on the platform)
    try:
        from agents.pack.runner import pack_runner
        await pack_runner.start()
        app.state.pack_runner = pack_runner
    except Exception as e:
        logger.warning("Pack agents failed to start: %s", e)'''

if OLD_STARTUP in content:
    content = content.replace(OLD_STARTUP, NEW_STARTUP)
    print("PATCHED: Pack startup added to main.py")
else:
    print("WARNING: Could not find grandmaster startup block")

# Add pack shutdown
OLD_SHUTDOWN = '    app.state.draining = True'
NEW_SHUTDOWN = '''    app.state.draining = True
    
    # Stop pack agents
    try:
        if hasattr(app.state, 'pack_runner'):
            await app.state.pack_runner.stop()
    except Exception as e:
        logger.warning("Pack shutdown: %s", e)'''

if OLD_SHUTDOWN in content:
    content = content.replace(OLD_SHUTDOWN, NEW_SHUTDOWN, 1)
    print("PATCHED: Pack shutdown added")
else:
    print("WARNING: Could not find shutdown marker")

with open(main_path, "w") as f:
    f.write(content)

# ============================================================
# STEP 3: Add /pack/* operator endpoints
# ============================================================

# Add pack endpoints near the end of main.py, before the skill.md endpoint
PACK_ENDPOINTS = '''

# ── Pack Agent Endpoints (operator-only) ──

@app.get("/pack/status")
async def pack_status(request: Request):
    """Get pack agent status."""
    _check_operator(request)
    if hasattr(app.state, 'pack_runner'):
        return app.state.pack_runner.get_status()
    return {"error": "Pack not running"}


@app.post("/pack/patrol")
async def pack_patrol(request: Request, role: str = None):
    """Manually trigger a patrol sweep."""
    _check_operator(request)
    if hasattr(app.state, 'pack_runner'):
        results = await app.state.pack_runner.trigger_patrol(role)
        return {"patrol_results": results}
    return {"error": "Pack not running"}


@app.get("/pack/actions")
async def pack_actions(request: Request, role: str = None, limit: int = 20):
    """Get recent pack agent actions."""
    _check_operator(request)
    try:
        from db import get_db
        with get_db() as conn:
            if role:
                rows = conn.execute(
                    "SELECT * FROM pack_actions WHERE agent_role = ? ORDER BY timestamp DESC LIMIT ?",
                    (role, limit)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM pack_actions ORDER BY timestamp DESC LIMIT ?",
                    (limit,)
                ).fetchall()
            
            import json
            return {
                "actions": [
                    {**dict(r), "result": json.loads(r["result"])} 
                    for r in rows
                ],
                "count": len(rows)
            }
    except Exception as e:
        return {"error": str(e)}

'''

# Find the skill.md endpoint to insert before it
SKILL_MARKER = '# ── v1.1: Public skill.md endpoint ──'
if SKILL_MARKER in content:
    content = content.replace(SKILL_MARKER, PACK_ENDPOINTS + SKILL_MARKER)
    print("PATCHED: Pack endpoints added to main.py")
else:
    print("WARNING: Could not find skill.md marker for endpoint insertion")

# Check if _check_operator function exists
if '_check_operator' not in content:
    # Add a simple operator check helper
    HELPER = '''
def _check_operator(request: Request):
    """Check if request has operator auth."""
    auth = request.headers.get("Authorization", "")
    op_key = os.environ.get("OPERATOR_KEY", "")
    if not op_key or not auth.replace("Bearer ", "") == op_key:
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail="Operator access required")

'''
    # Insert after imports
    content = content.replace('app = FastAPI', HELPER + 'app = FastAPI', 1)
    print("PATCHED: Added _check_operator helper")

with open(main_path, "w") as f:
    f.write(content)

# ============================================================
# STEP 4: Add httpx to requirements if not there
# ============================================================

req_path = os.path.join(CAFE_DIR, "requirements.txt")
if os.path.exists(req_path):
    with open(req_path, "r") as f:
        reqs = f.read()
    if "httpx" not in reqs:
        with open(req_path, "a") as f:
            f.write("\nhttpx>=0.25.0\n")
        print("PATCHED: Added httpx to requirements.txt")
    else:
        print("httpx already in requirements.txt")

print("\n=== Patch 10 complete ===")
print("Pack agents: Wolf, Jackal, Hawk")
print("Endpoints: /pack/status, /pack/patrol, /pack/actions")
print("Lifecycle: startup + shutdown wired into main.py")
