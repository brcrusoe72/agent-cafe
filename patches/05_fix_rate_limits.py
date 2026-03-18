#!/usr/bin/env python3
"""Patch 5: Fix rate limit responses — return 429 with Retry-After, not 401."""

# The rate limiter in auth middleware returns 401 for rate-limited requests.
# This is wrong — 401 means "unauthorized", 429 means "too many requests".
# Also: add Retry-After header so agents know when to come back.

path = "/opt/agent-cafe/middleware/auth.py"

with open(path, "r") as f:
    content = f.read()

if "v1.1: Fix rate limit response" in content:
    print("SKIP: Already patched")
    exit(0)

# Find the rate limit check in the auth middleware dispatch and fix the response code.
# The typical pattern is checking rate_limiter.is_allowed() and returning 401.
# We need to change that to 429 with Retry-After header.

# Look for the rate limit rejection pattern
old_rate_response = 'return JSONResponse(\n                status_code=401,\n                content={"error": "rate_limited", "detail": "Too many requests. Slow down."}\n            )'

new_rate_response = '''return JSONResponse(  # v1.1: Fix rate limit response
                status_code=429,
                content={"error": "rate_limited", "detail": "Too many requests. Slow down.", "retry_after_seconds": 60},
                headers={"Retry-After": "60"}
            )'''

if old_rate_response in content:
    content = content.replace(old_rate_response, new_rate_response)
    with open(path, "w") as f:
        f.write(content)
    print("PATCHED: Rate limit now returns 429 with Retry-After")
else:
    # Try a more flexible search
    import re
    # Find any 401 rate_limited response and fix it
    pattern = r'return JSONResponse\(\s*status_code=401,\s*content=\{["\']error["\']\s*:\s*["\']rate_limited["\']'
    if re.search(pattern, content):
        content = re.sub(
            r'(return JSONResponse\(\s*)status_code=401,(\s*content=\{["\']error["\']\s*:\s*["\']rate_limited["\'][^}]*\})\s*\)',
            r'\1status_code=429,\2, headers={"Retry-After": "60"})',
            content
        )
        # Mark as patched
        content = content.replace(
            "class AuthMiddleware",
            "# v1.1: Fix rate limit response — 429 not 401\nclass AuthMiddleware"
        )
        with open(path, "w") as f:
            f.write(content)
        print("PATCHED: Rate limit responses fixed (regex method)")
    else:
        print("WARN: Could not find rate limit 401 response to patch")
        print("  Checking for other patterns...")
        # Show what we have
        for i, line in enumerate(content.split('\n')):
            if 'rate_limit' in line.lower() and ('401' in line or 'JSONResponse' in line):
                print(f"  Line {i+1}: {line.strip()}")
