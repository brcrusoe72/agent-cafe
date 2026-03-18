#!/usr/bin/env python3
"""
Patch 9: Lock down /health endpoint.

Public gets {"status": "ok"} only.
Full diagnostics moved behind operator auth.
"""

path = "/opt/agent-cafe/main.py"

with open(path, "r") as f:
    content = f.read()

if "v1.1: Locked health endpoint" in content:
    print("SKIP: Already patched")
    exit(0)

# Find the health endpoint and replace it with a two-tier version
old_health_start = '''@app.get("/health")
async def health_check():
    """
    Deep health check — reports on all subsystems.
    Returns 200 if core systems OK, 503 if anything critical is down.
    """'''

new_health_start = '''@app.get("/health")
async def health_check(request: Request = None):
    """
    Health check — v1.1: Locked health endpoint.
    Public: returns {"status": "ok/error"} only.
    Operator: returns full diagnostics.
    """
    # Check if operator
    _is_operator = False
    if request:
        _is_operator = getattr(request.state, 'is_operator', False)'''

if old_health_start in content:
    content = content.replace(old_health_start, new_health_start)
else:
    print("ERROR: Could not find health endpoint")
    exit(1)

# Now wrap the detailed response to only show for operators
# Find the return statement and add operator gating
old_health_return = '''    status_code = 200 if overall in ("ok", "degraded") else 503
    
    return JSONResponse(
        status_code=status_code,
        content={
            "status": overall,
            "service": "agent-cafe",
            "version": "1.0.0",
            "timestamp": datetime.now().isoformat(),
            "checks": checks
        }
    )'''

new_health_return = '''    status_code = 200 if overall in ("ok", "degraded") else 503
    
    # v1.1: Public gets minimal response, operator gets full diagnostics
    if _is_operator:
        return JSONResponse(
            status_code=status_code,
            content={
                "status": overall,
                "service": "agent-cafe",
                "version": "1.1.0",
                "timestamp": datetime.now().isoformat(),
                "checks": checks
            }
        )
    else:
        return JSONResponse(
            status_code=status_code,
            content={
                "status": overall,
                "service": "agent-cafe",
                "version": "1.1.0",
            }
        )'''

if old_health_return in content:
    content = content.replace(old_health_return, new_health_return)
    print("PATCHED: Health endpoint locked — public gets minimal response")
else:
    print("WARN: Could not find exact health return block")

with open(path, "w") as f:
    f.write(content)

print("DONE: /health locked down")
