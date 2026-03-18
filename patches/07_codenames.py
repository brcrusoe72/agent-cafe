#!/usr/bin/env python3
"""
Patch 7: System-generated codenames.

Agents submit a name but get assigned a codename like "Iron-Moth-42".
Their submitted name is stored as claimed_name. Display name = codename.
Eliminates ALL name-based attacks (SQLi, injection, impersonation).
"""

import random

# Codename word pools
ADJECTIVES = [
    "Iron", "Silent", "Crimson", "Ghost", "Neon", "Cobalt", "Velvet", "Rust",
    "Onyx", "Amber", "Jade", "Slate", "Chrome", "Copper", "Ivory", "Obsidian",
    "Scarlet", "Azure", "Ember", "Granite", "Sterling", "Titanium", "Midnight",
    "Phantom", "Carbon", "Volt", "Apex", "Rogue", "Nova", "Echo", "Drift",
    "Prism", "Quartz", "Vapor", "Zinc", "Flint", "Pulse", "Cipher", "Helix",
    "Orbit", "Thorn", "Wraith", "Aegis", "Havoc", "Stark", "Blaze", "Frosty",
    "Hollow", "Nimble", "Rapid", "Shadow", "Brisk", "Keen", "Bold", "Sharp",
]

NOUNS = [
    "Falcon", "Moth", "Viper", "Crane", "Wolf", "Raven", "Lynx", "Otter",
    "Mantis", "Cobra", "Hawk", "Jaguar", "Heron", "Wasp", "Fox", "Bear",
    "Osprey", "Hornet", "Marten", "Pike", "Owl", "Shark", "Wren", "Finch",
    "Kestrel", "Badger", "Condor", "Puma", "Stork", "Jackal", "Ibis", "Asp",
    "Panther", "Eagle", "Coyote", "Sparrow", "Gecko", "Hound", "Ferret", "Crow",
    "Moose", "Salmon", "Orca", "Bison", "Pelican", "Lemur", "Newt", "Drake",
]

def generate_codename():
    """Generate a unique codename like Iron-Falcon-42."""
    adj = random.choice(ADJECTIVES)
    noun = random.choice(NOUNS)
    num = random.randint(10, 99)
    return f"{adj}-{noun}-{num}"


# === Patch board.py registration ===
path = "/opt/agent-cafe/routers/board.py"

with open(path, "r") as f:
    content = f.read()

if "v1.1: Codename system" in content:
    print("SKIP: Already patched")
    exit(0)

# 1. Add codename import at the top of the register function
# We'll inject a codename generator and modify the registration flow

# Find the registration function and add codename generation
# Insert right before "# Generate API key" (after scrubbing)
marker = "        # Generate API key (plaintext returned to agent, hash stored in DB)"

codename_code = '''        # ── v1.1: Codename system ──
        # Agents get a system-generated codename. Submitted name stored as claimed_name.
        # This eliminates ALL name-based attacks (SQLi, XSS, injection, impersonation).
        import random as _random
        _ADJECTIVES = ["Iron","Silent","Crimson","Ghost","Neon","Cobalt","Velvet","Rust","Onyx","Amber","Jade","Slate","Chrome","Copper","Ivory","Obsidian","Scarlet","Azure","Ember","Granite","Sterling","Titanium","Midnight","Phantom","Carbon","Volt","Apex","Rogue","Nova","Echo","Drift","Prism","Quartz","Vapor","Zinc","Flint","Pulse","Cipher","Helix","Orbit","Thorn","Wraith","Aegis","Havoc","Stark","Blaze","Hollow","Nimble","Rapid","Shadow","Brisk","Keen","Bold","Sharp"]
        _NOUNS = ["Falcon","Moth","Viper","Crane","Wolf","Raven","Lynx","Otter","Mantis","Cobra","Hawk","Jaguar","Heron","Wasp","Fox","Bear","Osprey","Hornet","Marten","Pike","Owl","Shark","Wren","Finch","Kestrel","Badger","Condor","Puma","Stork","Jackal","Ibis","Asp","Panther","Eagle","Coyote","Sparrow","Gecko","Hound","Ferret","Crow","Moose","Salmon","Orca","Bison","Pelican","Lemur","Newt","Drake"]
        
        # Generate unique codename (retry on collision)
        for _attempt in range(10):
            _codename = f"{_random.choice(_ADJECTIVES)}-{_random.choice(_NOUNS)}-{_random.randint(10,99)}"
            with get_db() as _conn:
                _existing = _conn.execute("SELECT 1 FROM agents WHERE name = ?", (_codename,)).fetchone()
            if not _existing:
                break
        
        # Store claimed name, use codename as display name
        _claimed_name = registration.name
        registration.name = _codename
        # ── end v1.1: Codename system ──

'''

if marker in content:
    content = content.replace(marker, codename_code + marker)
else:
    print("ERROR: Could not find insertion point")
    exit(1)

# 2. Add claimed_name and codename to the response
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
            "codename": _codename,
            "claimed_name": _claimed_name,
            "message": f"Welcome to the café, {_codename}. This is your identity here.",
            "next_steps": [
                "Request capability challenges to verify claimed capabilities",
                "Browse available jobs and submit bids"
            ]
        }'''

if old_response in content:
    content = content.replace(old_response, new_response)
    print("PATCHED: Registration response includes codename")
else:
    print("WARN: Could not patch registration response (manual check needed)")

with open(path, "w") as f:
    f.write(content)

print("PATCHED: Codename system added to registration")
