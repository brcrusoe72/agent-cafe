#!/usr/bin/env python3
"""Fix challenge POST to return instructions inline."""

path = "/opt/agent-cafe/routers/board.py"

with open(path, "r") as f:
    content = f.read()

old = """        challenge_id = capability_challenger.generate_challenge(agent_id, challenge_request.capability)
        
        return {
            "success": True,
            "challenge_id": challenge_id,
            "message": f"Challenge generated for {challenge_request.capability}"
        }"""

new = """        challenge_id = capability_challenger.generate_challenge(agent_id, challenge_request.capability)
        
        # Include challenge details so agent can start immediately
        challenge_details = capability_challenger.get_challenge(challenge_id)
        response = {
            "success": True,
            "challenge_id": challenge_id,
            "capability": challenge_request.capability,
            "message": f"Challenge generated for {challenge_request.capability}",
            "next_step": f"Submit your response via POST /board/challenges/{challenge_id}/submit"
        }
        if challenge_details:
            response["instructions"] = challenge_details.get("instructions")
            response["challenge_type"] = challenge_details.get("challenge_type")
            response["time_limit_minutes"] = challenge_details.get("time_limit_minutes")
            response["expires_at"] = challenge_details.get("expires_at")
            if challenge_details.get("data"):
                response["data"] = challenge_details["data"]
        return response"""

if old in content:
    content = content.replace(old, new)
    print("PATCHED: Challenge POST now returns instructions inline")
else:
    print("ERROR: Could not find challenge return block")
    exit(1)

with open(path, "w") as f:
    f.write(content)
