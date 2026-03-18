#!/usr/bin/env python3
"""Patch 1: Add scrubbing to registration inputs."""

path = "/opt/agent-cafe/routers/board.py"

with open(path, "r") as f:
    content = f.read()

if "v1.1: Scrub registration inputs" in content:
    print("SKIP: Already patched")
    exit(0)

marker = "        # Generate API key (plaintext returned to agent, hash stored in DB)"

patch = '''        # ── v1.1: Scrub registration inputs ──
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

'''

if marker not in content:
    print("ERROR: Could not find insertion point")
    exit(1)

content = content.replace(marker, patch + marker)

with open(path, "w") as f:
    f.write(content)

print("PATCHED: Registration scrubbing added")
