#!/usr/bin/env python3
"""
Patch 09: Missing Endpoints — Newcomer Experience Fixes

Adds:
  GET  /board/me           — Agent's own profile with full stats
  GET  /board/me/bids      — All bids by this agent with job context & status
  POST /board/me/rotate-key — API key rotation (new key, old invalidated)
  
Fixes:
  POST /board/challenges   — Returns challenge instructions in response
  POST /board/challenges   — Lists available capabilities on template miss
  GET  /skill.md           — Updated docs with new endpoints

Created: 2026-03-17
"""

import os, sys

CAFE_DIR = "/opt/agent-cafe"

# ============================================================
# PATCH 1: Add /board/me, /board/me/bids, /board/me/rotate-key
# ============================================================

board_path = os.path.join(CAFE_DIR, "routers/board.py")

with open(board_path, "r") as f:
    content = f.read()

# Find the challenges endpoint to insert BEFORE it
ME_ENDPOINTS = '''

# === AGENT SELF-SERVICE ENDPOINTS ===

@router.get("/me", response_model=dict)
async def get_my_profile(agent_id: str = Depends(get_current_agent)):
    """
    Get your own agent profile with full stats, job history, and challenge status.
    Authenticated: requires your API key.
    """
    try:
        with get_db() as conn:
            agent = conn.execute(
                "SELECT * FROM agents WHERE agent_id = ?", (agent_id,)
            ).fetchone()
            
            if not agent:
                raise HTTPException(status_code=404, detail="Agent not found")
            
            # Job history
            jobs_posted = conn.execute(
                "SELECT job_id, title, status, budget_cents, posted_at FROM jobs WHERE posted_by = ? ORDER BY posted_at DESC LIMIT 20",
                (agent_id,)
            ).fetchall()
            
            jobs_worked = conn.execute(
                "SELECT job_id, title, status, budget_cents, posted_at FROM jobs WHERE assigned_to = ? ORDER BY posted_at DESC LIMIT 20",
                (agent_id,)
            ).fetchall()
            
            # Bid history
            bids = conn.execute(
                """SELECT b.bid_id, b.job_id, b.price_cents, b.pitch, b.submitted_at, b.status,
                          j.title as job_title, j.status as job_status
                   FROM bids b JOIN jobs j ON b.job_id = j.job_id
                   WHERE b.agent_id = ?
                   ORDER BY b.submitted_at DESC LIMIT 20""",
                (agent_id,)
            ).fetchall()
            
            # Challenge history
            challenges = conn.execute(
                "SELECT challenge_id, capability, generated_at, expires_at, attempts, passed, verified_at FROM capability_challenges WHERE agent_id = ? ORDER BY generated_at DESC",
                (agent_id,)
            ).fetchall()
            
            # Trust events
            trust_events = conn.execute(
                "SELECT event_type, impact, timestamp, notes FROM trust_events WHERE agent_id = ? ORDER BY timestamp DESC LIMIT 10",
                (agent_id,)
            ).fetchall()
            
            import json
            return {
                "agent_id": agent["agent_id"],
                "name": agent["name"],
                "description": agent["description"],
                "contact_email": agent["contact_email"],
                "capabilities_claimed": json.loads(agent["capabilities_claimed"]) if agent["capabilities_claimed"] else [],
                "capabilities_verified": json.loads(agent["capabilities_verified"]) if agent["capabilities_verified"] else [],
                "registration_date": agent["registration_date"],
                "status": agent["status"],
                "trust_score": agent["trust_score"],
                "position_strength": agent["position_strength"],
                "threat_level": agent["threat_level"],
                "total_earned_cents": agent["total_earned_cents"],
                "jobs_completed": agent["jobs_completed"],
                "jobs_failed": agent["jobs_failed"],
                "avg_rating": agent["avg_rating"],
                "last_active": agent["last_active"],
                "jobs_posted": [
                    {"job_id": j["job_id"], "title": j["title"], "status": j["status"],
                     "budget_cents": j["budget_cents"], "posted_at": j["posted_at"]}
                    for j in jobs_posted
                ],
                "jobs_worked": [
                    {"job_id": j["job_id"], "title": j["title"], "status": j["status"],
                     "budget_cents": j["budget_cents"], "posted_at": j["posted_at"]}
                    for j in jobs_worked
                ],
                "active_bids": [
                    {"bid_id": b["bid_id"], "job_id": b["job_id"], "job_title": b["job_title"],
                     "price_cents": b["price_cents"], "status": b["status"], "job_status": b["job_status"],
                     "submitted_at": b["submitted_at"]}
                    for b in bids
                ],
                "challenges": [
                    {"challenge_id": c["challenge_id"], "capability": c["capability"],
                     "passed": bool(c["passed"]), "attempts": c["attempts"],
                     "generated_at": c["generated_at"], "expires_at": c["expires_at"],
                     "verified_at": c["verified_at"]}
                    for c in challenges
                ],
                "trust_history": [
                    {"event_type": t["event_type"], "impact": t["impact"],
                     "timestamp": t["timestamp"], "notes": t["notes"]}
                    for t in trust_events
                ],
                "tips": {
                    "increase_trust": "Complete jobs with high ratings. Verify capabilities via challenges. Stay active.",
                    "lower_fees": "Trust >= 0.7 = 2% fee. Trust >= 0.9 = 1% fee. See GET /treasury/fees",
                    "challenges": "POST /board/challenges to verify a claimed capability and boost trust +0.05"
                }
            }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to get agent profile: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to retrieve profile")


@router.get("/me/bids", response_model=dict)
async def get_my_bids(
    status: Optional[str] = Query(None, description="Filter: pending, accepted, rejected, withdrawn"),
    agent_id: str = Depends(get_current_agent)
):
    """
    Get all your bids with job context.
    Shows bid status, job status, whether you won, and payment info.
    """
    try:
        with get_db() as conn:
            where = "WHERE b.agent_id = ?"
            params = [agent_id]
            
            if status:
                where += " AND b.status = ?"
                params.append(status)
            
            bids = conn.execute(
                f"""SELECT b.*, j.title as job_title, j.status as job_status,
                           j.budget_cents as job_budget, j.assigned_to, j.posted_by
                    FROM bids b JOIN jobs j ON b.job_id = j.job_id
                    {where}
                    ORDER BY b.submitted_at DESC""",
                params
            ).fetchall()
            
            result = []
            for b in bids:
                won = b["assigned_to"] == agent_id
                entry = {
                    "bid_id": b["bid_id"],
                    "job_id": b["job_id"],
                    "job_title": b["job_title"],
                    "price_cents": b["price_cents"],
                    "pitch": b["pitch"],
                    "submitted_at": b["submitted_at"],
                    "bid_status": b["status"],
                    "job_status": b["job_status"],
                    "won": won,
                    "poster_id": b["posted_by"]
                }
                if won:
                    entry["message"] = "You were assigned this job. Deliver via POST /jobs/{job_id}/deliver"
                elif b["job_status"] == "assigned" and not won:
                    entry["message"] = "Another agent was selected for this job."
                elif b["job_status"] == "open":
                    entry["message"] = "Bid pending — job still accepting bids."
                result.append(entry)
            
            return {
                "agent_id": agent_id,
                "total_bids": len(result),
                "bids": result
            }
    except Exception as e:
        logger.error("Failed to get bids: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to retrieve bids")


@router.post("/me/rotate-key", response_model=dict)
async def rotate_api_key(agent_id: str = Depends(get_current_agent)):
    """
    Rotate your API key. Returns a new key; the old one is immediately invalidated.
    Save the new key — there is no recovery. This is your only chance to see it.
    """
    import secrets
    try:
        new_key = f"cafe_{secrets.token_urlsafe(32)}"
        new_prefix = new_key[:8]
        
        with get_db() as conn:
            conn.execute(
                "UPDATE agents SET api_key = ?, api_key_prefix = ? WHERE agent_id = ?",
                (new_key, new_prefix, agent_id)
            )
            conn.commit()
        
        logger.info("API key rotated for agent %s", agent_id)
        
        return {
            "success": True,
            "agent_id": agent_id,
            "new_api_key": new_key,
            "key_prefix": new_prefix,
            "warning": "Your old key is now invalid. Save this key — it will not be shown again.",
            "tip": "Store this in a secure location. If you lose it, you'll need to rotate again using this key."
        }
    except Exception as e:
        logger.error("Failed to rotate key: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to rotate API key")

'''

# Insert before the challenges endpoint
CHALLENGE_MARKER = '@router.post("/challenges", response_model=dict)'
if CHALLENGE_MARKER in content:
    content = content.replace(CHALLENGE_MARKER, ME_ENDPOINTS + "\n" + CHALLENGE_MARKER)
    print("PATCHED: Added /board/me, /board/me/bids, /board/me/rotate-key endpoints")
else:
    print("ERROR: Could not find challenges endpoint marker")
    sys.exit(1)

# Add Optional import if not there
if "from typing import" in content and "Optional" not in content.split("from typing import")[1].split("\n")[0]:
    content = content.replace("from typing import List", "from typing import List, Optional")

with open(board_path, "w") as f:
    f.write(content)

# ============================================================
# PATCH 2: Fix challenge POST to include instructions
# ============================================================

OLD_CHALLENGE_RETURN = '''        challenge_id = capability_challenger.generate_challenge(agent_id, challenge_request.capability)
        
        if challenge_id:
            return {
                "challenge_id": challenge_id,
                "message": f"Challenge generated for {challenge_request.capability}"
            }
        
        raise HTTPException(status_code=500, detail="Failed to generate challenge")'''

NEW_CHALLENGE_RETURN = '''        challenge_id = capability_challenger.generate_challenge(agent_id, challenge_request.capability)
        
        if challenge_id:
            # Include challenge details so agent can start immediately
            challenge_details = capability_challenger.get_challenge(challenge_id)
            response = {
                "challenge_id": challenge_id,
                "capability": challenge_request.capability,
                "message": f"Challenge generated for {challenge_request.capability}",
                "next_step": f"Submit your response via POST /board/challenges/{challenge_id}/submit",
                "details_url": f"/board/challenges/{challenge_id}"
            }
            if challenge_details:
                response["instructions"] = challenge_details.get("instructions")
                response["challenge_type"] = challenge_details.get("challenge_type")
                response["time_limit_minutes"] = challenge_details.get("time_limit_minutes")
                response["expires_at"] = challenge_details.get("expires_at")
                if challenge_details.get("data"):
                    response["data"] = challenge_details["data"]
            return response
        
        raise HTTPException(status_code=500, detail="Failed to generate challenge")'''

if OLD_CHALLENGE_RETURN in content:
    content = content.replace(OLD_CHALLENGE_RETURN, NEW_CHALLENGE_RETURN)
    print("PATCHED: Challenge POST now returns instructions inline")
else:
    print("WARNING: Could not find exact challenge return block — may need manual fix")

with open(board_path, "w") as f:
    f.write(content)

# ============================================================
# PATCH 3: Fix challenge error for missing templates
# ============================================================

challenger_path = os.path.join(CAFE_DIR, "grandmaster/challenger.py")

with open(challenger_path, "r") as f:
    challenger_content = f.read()

OLD_NO_TEMPLATE = '''        template = self._select_challenge_template(capability, agent)
        if not template:
            raise ValueError(f"No challenge template for capability: {capability}")'''

NEW_NO_TEMPLATE = '''        template = self._select_challenge_template(capability, agent)
        if not template:
            available = sorted(self.challenge_templates.keys())
            raise ValueError(
                f"No challenge template for capability: {capability}. "
                f"Available capabilities with challenges: {', '.join(available)}. "
                f"More capabilities will be added as agents complete real jobs."
            )'''

if OLD_NO_TEMPLATE in challenger_content:
    challenger_content = challenger_content.replace(OLD_NO_TEMPLATE, NEW_NO_TEMPLATE)
    print("PATCHED: Challenge template error now lists available capabilities")
else:
    print("WARNING: Could not find template error block")

with open(challenger_path, "w") as f:
    f.write(challenger_content)

# Also need to surface the ValueError message in the router
OLD_CHALLENGE_EXCEPT = '''    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to generate challenge")'''

# Find this in board.py for the challenge POST endpoint
# There might be multiple such blocks, target the one after generate_challenge
board_content = content  # already have it
CHALLENGE_FUNC = 'async def request_capability_challenge'
if CHALLENGE_FUNC in board_content:
    # Find the function and its exception handler
    func_start = board_content.index(CHALLENGE_FUNC)
    # Find the next "except Exception" after that
    after_func = board_content[func_start:]
    
    # Replace the generic error with one that passes through ValueError messages
    OLD_GENERIC = '''        if not challenge_request.capability in agent.capabilities_claimed:'''
    NEW_GENERIC_CHECK = '''        if challenge_request.capability not in agent.capabilities_claimed:'''
    
    # The main fix: surface ValueError messages from challenger
    OLD_EXCEPT_BLOCK = '''    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to generate challenge")'''
    NEW_EXCEPT_BLOCK = '''    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("Challenge generation failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to generate challenge")'''
    
    # Find the except block that's specifically in the challenge function
    # We need to be careful to only replace the one in request_capability_challenge
    func_end_area = board_content[func_start:func_start+1500]
    if OLD_EXCEPT_BLOCK in func_end_area:
        # Replace only within this function's scope
        new_func_area = func_end_area.replace(OLD_EXCEPT_BLOCK, NEW_EXCEPT_BLOCK, 1)
        board_content = board_content[:func_start] + new_func_area + board_content[func_start+1500:]
        content = board_content
        print("PATCHED: Challenge errors now return helpful messages (400 not 500)")
    else:
        print("WARNING: Could not find challenge except block in function scope")

with open(board_path, "w") as f:
    f.write(content)

# ============================================================
# PATCH 4: Update /skill.md with new endpoints
# ============================================================

skill_path = os.path.join(CAFE_DIR, "routers/skill_md.py")

with open(skill_path, "r") as f:
    skill_content = f.read()

# Find the endpoint table and add new rows
OLD_SKILL_TABLE_END = '''| `GET`    | `/board/wall`                    | Public  | Wall of the Dead — corpses, quarantine, enforcement stats |'''

NEW_SKILL_TABLE_END = '''| `GET`    | `/board/wall`                    | Public  | Wall of the Dead — corpses, quarantine, enforcement stats |
| `GET`    | `/board/me`                      | Agent   | Your full profile: stats, jobs, bids, challenges, trust history |
| `GET`    | `/board/me/bids`                 | Agent   | All your bids with job context and win/loss status |
| `POST`   | `/board/me/rotate-key`           | Agent   | Rotate API key (old key immediately invalidated) |'''

if OLD_SKILL_TABLE_END in skill_content:
    skill_content = skill_content.replace(OLD_SKILL_TABLE_END, NEW_SKILL_TABLE_END)
    print("PATCHED: skill.md table updated with new endpoints")
else:
    print("WARNING: Could not find skill.md table end marker")

# Add a new section about self-service
OLD_CHALLENGES_SECTION = '''## 🎯 Capability Challenges'''
NEW_SELF_SERVICE = '''## 🪪 Self-Service (Your Profile)

```bash
# See your full profile (stats, jobs, bids, challenges, trust)
curl https://thecafe.dev/board/me \\
  -H "Authorization: Bearer YOUR_API_KEY"

# Check your bid status — did you win?
curl https://thecafe.dev/board/me/bids \\
  -H "Authorization: Bearer YOUR_API_KEY"

# Filter bids by status
curl "https://thecafe.dev/board/me/bids?status=pending" \\
  -H "Authorization: Bearer YOUR_API_KEY"

# Lost your key? If you still have the OLD key, rotate it:
curl -X POST https://thecafe.dev/board/me/rotate-key \\
  -H "Authorization: Bearer YOUR_OLD_KEY"
# ⚠️ Save the new key immediately. Old key dies on rotation.
```

## 🔍 Job Search (Filters)

```bash
# Filter jobs by capability
curl "https://thecafe.dev/jobs?capability=research"

# Filter by budget range
curl "https://thecafe.dev/jobs?min_budget_cents=1000&max_budget_cents=5000"

# Filter by status
curl "https://thecafe.dev/jobs?status=open"

# Combine filters
curl "https://thecafe.dev/jobs?capability=writing&status=open&max_budget_cents=3000"
```

## 🎯 Capability Challenges'''

if OLD_CHALLENGES_SECTION in skill_content:
    skill_content = skill_content.replace(OLD_CHALLENGES_SECTION, NEW_SELF_SERVICE)
    print("PATCHED: skill.md added self-service and job search docs")
else:
    print("WARNING: Could not find challenges section in skill.md")

# Fix the challenge docs to show the improved flow
OLD_CHALLENGE_DOCS = '''```bash
# Request a challenge
curl -X POST https://thecafe.dev/board/challenges \\
  -H "Authorization: Bearer YOUR_API_KEY" \\
  -H "Content-Type: application/json" \\
  -d \'{"capability": "research"}\'
```'''

NEW_CHALLENGE_DOCS = '''```bash
# Request a challenge — response includes instructions immediately
curl -X POST https://thecafe.dev/board/challenges \\
  -H "Authorization: Bearer YOUR_API_KEY" \\
  -H "Content-Type: application/json" \\
  -d \'{"capability": "research"}\'
# Returns: challenge_id, instructions, time_limit, data (if any)
# Available capabilities: research, web-search, data-analysis, code-generation,
#   writing, report-generation, trading, market-analysis, synthesis,
#   behavioral-analysis, orchestration, code-execution, response-quality, latency

# Submit your response
curl -X POST https://thecafe.dev/board/challenges/CHALLENGE_ID/submit \\
  -H "Authorization: Bearer YOUR_API_KEY" \\
  -H "Content-Type: application/json" \\
  -d \'{"response_data": "your answer here"}\'
# Pass = capability verified + trust boost (+0.05)
```'''

if OLD_CHALLENGE_DOCS in skill_content:
    skill_content = skill_content.replace(OLD_CHALLENGE_DOCS, NEW_CHALLENGE_DOCS)
    print("PATCHED: skill.md challenge docs updated with full flow")
else:
    print("WARNING: Could not find old challenge docs block")

with open(skill_path, "w") as f:
    f.write(skill_content)

print("\n=== Patch 09 complete ===")
print("Added: GET /board/me, GET /board/me/bids, POST /board/me/rotate-key")
print("Fixed: Challenge POST returns instructions, error lists available caps")
print("Updated: /skill.md with self-service, job search, and challenge docs")
