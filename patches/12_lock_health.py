#!/usr/bin/env python3
"""
Patch: Lock down /health — public gets minimal info, details behind auth.
Also: add security headers to Caddy, fix /federation/info exposure.
"""

# === Part 1: Split /health into public (minimal) and operator (full) ===

path = "/opt/agent-cafe/main.py"

with open(path, "r") as f:
    content = f.read()

if "v1.2: Locked health" in content:
    print("SKIP: Health already locked")
else:
    # Replace the health endpoint with a minimal public version
    old_health_start = '@app.get("/health")\nasync def health_check():'
    
    # We'll add a new minimal health endpoint and rename the detailed one
    new_health = '''@app.get("/health")
async def health_check_public():
    """
    Public health check — minimal info only.
    v1.2: Locked health — no system internals exposed.
    """
    try:
        try:
            from .db import get_db
        except ImportError:
            from db import get_db
        with get_db() as conn:
            conn.execute("SELECT 1").fetchone()
        db_ok = True
    except Exception:
        db_ok = False
    
    status = "ok" if db_ok else "error"
    status_code = 200 if db_ok else 503
    
    return JSONResponse(
        status_code=status_code,
        content={
            "status": status,
            "service": "agent-cafe",
            "version": "1.0.0",
        }
    )


@app.get("/health/detail")
async def health_check_detail(request: Request):
    """
    Detailed health check — operator only.
    v1.2: Locked health internals behind auth.
    """
    if not getattr(request.state, 'is_operator', False):
        return JSONResponse(status_code=401, content={"error": "Operator access required"})
    '''
    
    if old_health_start in content:
        content = content.replace(old_health_start, new_health)
        with open(path, "w") as f:
            f.write(content)
        print("PATCHED: /health locked down, details at /health/detail (operator only)")
    else:
        print("WARN: Could not find health endpoint to patch")


# === Part 2: Add security headers via Caddyfile ===

caddy_path = "/opt/agent-cafe/Caddyfile"

with open(caddy_path, "r") as f:
    caddy = f.read()

if "Strict-Transport-Security" in caddy:
    print("SKIP: Security headers already in Caddyfile")
else:
    new_caddy = '''thecafe.dev, www.thecafe.dev {
    # v1.2: Security headers
    header {
        Strict-Transport-Security "max-age=31536000; includeSubDomains"
        X-Content-Type-Options "nosniff"
        X-Frame-Options "DENY"
        Content-Security-Policy "default-src 'none'; frame-ancestors 'none'"
        Referrer-Policy "no-referrer"
        -Via
        -Server
    }
    
    reverse_proxy app:8790
}
'''
    with open(caddy_path, "w") as f:
        f.write(new_caddy)
    print("PATCHED: Security headers added to Caddyfile")


# === Part 3: Lock /federation/info, /dashboard/* behind auth ===

auth_path = "/opt/agent-cafe/middleware/auth.py"

with open(auth_path, "r") as f:
    auth_content = f.read()

# Remove federation/info and dashboard from public endpoints
endpoints_to_remove = [
    '        "/federation/info",\n',
    '        "/federation/peers",\n',
    '        "/federation/deaths",\n',
    '        "/federation/remote-jobs",\n',
    '        "/dashboard",\n',
    '        "/dashboard/data",\n',
    '        "/dashboard/feed",\n',
]

changed = False
for endpoint in endpoints_to_remove:
    if endpoint in auth_content:
        auth_content = auth_content.replace(endpoint, '')
        changed = True

if changed:
    with open(auth_path, "w") as f:
        f.write(auth_content)
    print("PATCHED: Removed /federation/*, /dashboard/* from public endpoints")
else:
    print("SKIP: Federation/dashboard endpoints already locked or not found")
