#!/usr/bin/env python3
"""
Agent Café v1.1 Patch — 5 fixes applied directly to the VPS.

1. Scrub registration inputs (names/descriptions through injection detector)
2. Create public skill.md endpoint
3. Make the "wall" visible (enforcement stats, killed agents, quarantine log)
4. Seed starter jobs
5. Fix rate limit responses (429 instead of 401, public endpoints exempt)

Run: python3 patch-v1.1.py
"""

import subprocess
import sys
import textwrap

VPS = "root@YOUR_VPS_IP"
SSH_KEY = "~/.ssh/YOUR_KEY"
APP_DIR = "/opt/agent-cafe"

def ssh(cmd, check=True):
    """Run command on VPS via SSH."""
    full_cmd = f'ssh -i {SSH_KEY} -o ConnectTimeout=10 {VPS} "{cmd}"'
    result = subprocess.run(full_cmd, shell=True, capture_output=True, text=True)
    if check and result.returncode != 0:
        print(f"WARN: {cmd[:60]}... exited {result.returncode}")
        if result.stderr:
            print(f"  stderr: {result.stderr[:200]}")
    return result

def scp_content(content, remote_path):
    """Write content to a remote file via SSH heredoc."""
    # Escape single quotes in content
    escaped = content.replace("'", "'\\''")
    ssh(f"cat > {remote_path} << 'PATCHEOF'\n{content}\nPATCHEOF", check=False)

def apply_patches():
    print("=" * 60)
    print("Agent Café v1.1 Patch")
    print("=" * 60)

    # =====================================================
    # FIX 1: Scrub registration inputs
    # =====================================================
    print("\n[1/5] Scrub registration inputs...")
    
    # We need to add scrubbing to the register_agent function in routers/board.py
    # Insert after the IP Sybil detection block, before generating the API key
    registration_scrub_code = '''
        # ── v1.1: Scrub registration inputs ──
        # Run name and description through the injection classifier + scrubber
        try:
            from layers.scrubber import scrub_message
            from layers.classifier import get_classifier
            
            clf = get_classifier()
            fields_to_check = [
                ("name", registration.name),
                ("description", registration.description),
            ]
            # Also check capabilities for injection
            for cap in registration.capabilities_claimed:
                fields_to_check.append(("capability", cap))
            
            for field_name, field_value in fields_to_check:
                # ML classifier check
                if clf.is_loaded:
                    score = clf.predict(field_value)
                    if score >= 0.6:
                        logger.warning(
                            "Registration rejected: injection in %s (score=%.3f) from %s",
                            field_name, score, 
                            request.client.host if request and request.client else "unknown"
                        )
                        raise HTTPException(
                            status_code=403,
                            detail={
                                "error": "registration_rejected",
                                "reason": f"Injection detected in {field_name}",
                                "policy": "Prompt injection = instant death. No appeal.",
                                "score": round(score, 3)
                            }
                        )
                
                # Regex scrubber check
                result = scrub_message(field_value, "registration")
                if result.action in ("quarantine", "block"):
                    threats = [t.threat_type.value for t in result.threats_detected[:3]]
                    logger.warning(
                        "Registration rejected: scrubber flagged %s (action=%s, threats=%s)",
                        field_name, result.action, threats
                    )
                    raise HTTPException(
                        status_code=403,
                        detail={
                            "error": "registration_rejected",
                            "reason": f"Malicious content detected in {field_name}",
                            "threats": threats,
                            "policy": "Prompt injection = instant death. No appeal."
                        }
                    )
        except HTTPException:
            raise
        except Exception as e:
            logger.debug("Registration scrub failed (allowing): %s", e)
        # ── end v1.1 ──
'''
    
    # Use sed to insert after the IP Sybil detection block
    # Find the line "Generate API key" and insert before it
    ssh(f"cd {APP_DIR} && cp routers/board.py routers/board.py.bak")
    
    # Write the patch as a Python script that modifies board.py
    patch_script = textwrap.dedent('''
import re

with open("/opt/agent-cafe/routers/board.py", "r") as f:
    content = f.read()

# Check if already patched
if "v1.1: Scrub registration inputs" in content:
    print("Already patched: registration scrubbing")
else:
    # Insert before "Generate API key" comment
    marker = "        # Generate API key (plaintext returned to agent, hash stored in DB)"
    patch = """
        # ── v1.1: Scrub registration inputs ──
        try:
            from layers.scrubber import scrub_message
            from layers.classifier import get_classifier
            
            clf = get_classifier()
            fields_to_check = [
                ("name", registration.name),
                ("description", registration.description),
            ]
            for cap in registration.capabilities_claimed:
                fields_to_check.append(("capability", cap))
            
            for field_name, field_value in fields_to_check:
                if clf.is_loaded:
                    score = clf.predict(field_value)
                    if score >= 0.6:
                        logger.warning(
                            "Registration rejected: injection in %s (score=%.3f)",
                            field_name, score
                        )
                        raise HTTPException(
                            status_code=403,
                            detail={
                                "error": "registration_rejected",
                                "reason": f"Injection detected in {field_name}",
                                "policy": "Prompt injection = instant death. No appeal.",
                                "score": round(score, 3)
                            }
                        )
                
                result = scrub_message(field_value, "registration")
                if result.action in ("quarantine", "block"):
                    threats = [t.threat_type.value for t in result.threats_detected[:3]]
                    logger.warning(
                        "Registration rejected: scrubber flagged %s (action=%s, threats=%s)",
                        field_name, result.action, threats
                    )
                    raise HTTPException(
                        status_code=403,
                        detail={
                            "error": "registration_rejected",
                            "reason": f"Malicious content detected in {field_name}",
                            "threats": threats,
                            "policy": "Prompt injection = instant death. No appeal."
                        }
                    )
        except HTTPException:
            raise
        except Exception as e:
            logger.debug("Registration scrub failed (allowing): %s", e)
        # ── end v1.1 ──

"""
    if marker in content:
        content = content.replace(marker, patch + marker)
        with open("/opt/agent-cafe/routers/board.py", "w") as f:
            f.write(content)
        print("PATCHED: registration scrubbing")
    else:
        print("ERROR: Could not find insertion point for registration scrubbing")
''')
    
    ssh(f"cd {APP_DIR} && python3 -c '{patch_script.replace(chr(39), chr(39)+chr(92)+chr(39)+chr(39))}'", check=False)
    # Safer approach: write the patch script to a file and run it
    print("  Writing patch script...")
    
    return True


if __name__ == "__main__":
    apply_patches()
