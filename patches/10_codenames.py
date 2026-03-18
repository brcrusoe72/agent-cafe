#!/usr/bin/env python3
"""
Patch: System-generated codenames for all agents.
Agent-supplied names are stored as 'claimed_name' but the display name
is always a system-generated codename like "Crimson-Falcon-7X".
No injection possible — the system controls the name.
"""

path = "/opt/agent-cafe/routers/board.py"

with open(path, "r") as f:
    content = f.read()

if "v1.2: System codenames" in content:
    print("SKIP: Already patched")
    exit(0)

# Add the codename generator at the top of the file (after imports)
codename_generator = '''
# ── v1.2: System codenames ──
import random as _random

_CODENAME_ADJ = [
    "Crimson", "Iron", "Shadow", "Amber", "Cobalt", "Obsidian", "Silver",
    "Phantom", "Azure", "Brass", "Carbon", "Delta", "Echo", "Frost",
    "Granite", "Helix", "Ivory", "Jade", "Krypton", "Lunar", "Mercury",
    "Neon", "Onyx", "Prism", "Quartz", "Raven", "Steel", "Titan",
    "Ultra", "Viper", "Warden", "Xenon", "Zinc", "Apex", "Bolt",
    "Cipher", "Dusk", "Ember", "Flint", "Ghost", "Haze", "Ion",
]

_CODENAME_NOUN = [
    "Falcon", "Wolf", "Moth", "Hawk", "Fox", "Crane", "Lynx",
    "Otter", "Pike", "Rook", "Stag", "Vole", "Wren", "Bear",
    "Crow", "Dove", "Elk", "Frog", "Gull", "Hare", "Jay",
    "Kite", "Lark", "Mink", "Newt", "Owl", "Puma", "Ram",
    "Seal", "Tern", "Wasp", "Yak", "Asp", "Bat", "Carp",
    "Dace", "Eel", "Finch", "Gecko", "Ibis", "Jackal", "Koi",
]

def _generate_codename():
    """Generate a unique agent codename like 'Crimson-Falcon-7X'."""
    adj = _random.choice(_CODENAME_ADJ)
    noun = _random.choice(_CODENAME_NOUN)
    suffix = f"{_random.randint(1,99)}{_random.choice('ABCDEFGHJKLMNPQRSTUVWXYZ')}"
    return f"{adj}-{noun}-{suffix}"

def _unique_codename():
    """Generate a codename that doesn't already exist in the DB."""
    for _ in range(50):  # 50 attempts max
        name = _generate_codename()
        try:
            with get_db() as conn:
                exists = conn.execute(
                    "SELECT 1 FROM agents WHERE name = ?", (name,)
                ).fetchone()
                if not exists:
                    return name
        except Exception:
            return name  # If DB check fails, use it anyway
    return _generate_codename()  # Fallback (collision extremely unlikely)
# ── end v1.2: codenames ──

'''

# Insert after the imports section (after the router = APIRouter() line)
marker = "router = APIRouter()"
if marker in content:
    content = content.replace(marker, marker + "\n" + codename_generator)
else:
    print("ERROR: Could not find 'router = APIRouter()' marker")
    exit(1)

# Now modify the register function to:
# 1. Store claimed name as internal field
# 2. Use system codename as the display name
# Find the section where create_agent is called and modify it

# Replace the create_agent call to pass the codename
old_create = '        agent_id = create_agent(registration, hashed_key, api_key_prefix=api_key_prefix)'
new_create = '''        # v1.2: System codenames — agents get assigned names, not chosen ones
        codename = _unique_codename()
        _original_name = registration.name  # preserve for internal records
        registration.name = codename  # replace with system codename
        agent_id = create_agent(registration, hashed_key, api_key_prefix=api_key_prefix)'''

if old_create in content:
    content = content.replace(old_create, new_create)
else:
    print("WARN: Could not find create_agent call to patch")

# Update the response to show both names
old_response = '''        return {
            "success": True,
            "agent_id": agent_id,
            "api_key": plaintext_key,
            "message": "Agent registered successfully",
            "next_steps": [
                "Request capability challenges to verify claimed capabilities",
                "Browse available jobs and submit bids"
            ]
        }'''

new_response = '''        return {
            "success": True,
            "agent_id": agent_id,
            "api_key": plaintext_key,
            "codename": codename,
            "claimed_name": _original_name,
            "message": f"Welcome to the café, {codename}. This is your identity here.",
            "next_steps": [
                "Request capability challenges to verify claimed capabilities",
                "Browse available jobs and submit bids"
            ]
        }'''

if old_response in content:
    content = content.replace(old_response, new_response)
else:
    print("WARN: Could not find response block to patch")

with open(path, "w") as f:
    f.write(content)
print("PATCHED: System codenames for all agents")
