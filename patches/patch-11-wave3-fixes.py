#!/usr/bin/env python3
"""
Patch 11 — Wave 3 Fixes
1. Block self-bidding (posted_by != bidder_id)
2. Input validation (budget cap $10K, min bid $1, reject negatives)
3. Leetspeak normalization in scrubber
4. Reserve pack agent names
5. Scan capabilities array
"""

import subprocess
import sys

VPS = "user@YOUR_VPS_IP"
SSH_KEY = "~/.ssh/YOUR_KEY"
SSH = f"ssh -i {SSH_KEY} -o StrictHostKeyChecking=no {VPS}"

def run(cmd, check=True):
    print(f"\n>>> {cmd}")
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if r.stdout: print(r.stdout[:2000])
    if r.stderr: print(r.stderr[:500])
    if check and r.returncode != 0:
        print(f"FAILED (rc={r.returncode})")
        return False
    return True

def patch_1_block_self_bidding():
    """Add posted_by != bidder_id check in wire.py submit_bid()"""
    print("\n" + "="*60)
    print("PATCH 1: Block self-bidding")
    print("="*60)
    
    # Insert self-bid check right after the job status check
    cmd = f'''{SSH} "cd /opt/agent-cafe && python3 -c \\"
import re

with open('layers/wire.py', 'r') as f:
    content = f.read()

# Find the spot after 'Job is ... not open for bids' check in submit_bid
old = '''        if job.status != JobStatus.OPEN:
            raise CommunicationError(f\\\\"Job is {{job.status}}, not open for bids\\\\")
        
        # Verify agent exists and can bid'''

new = '''        if job.status != JobStatus.OPEN:
            raise CommunicationError(f\\\\"Job is {{job.status}}, not open for bids\\\\")
        
        # PATCH 11.1: Block self-bidding — kills the entire self-dealing chain
        if job.posted_by == agent_id:
            raise CommunicationError(\\\\"You cannot bid on your own job.\\\\")
        
        # Verify agent exists and can bid'''

if old in content:
    content = content.replace(old, new)
    with open('layers/wire.py', 'w') as f:
        f.write(content)
    print('PATCH 1 APPLIED: self-bid check added')
else:
    print('PATCH 1 SKIPPED: pattern not found (may already be applied)')
\\""'''
    return run(cmd)

def patch_2_input_validation():
    """Add budget cap, min bid, reject negatives"""
    print("\n" + "="*60)
    print("PATCH 2: Input validation (budget cap, min bid, reject negatives)")
    print("="*60)
    
    # 2a: Budget cap in wire.py create_job
    cmd = f'''{SSH} "cd /opt/agent-cafe && python3 -c \\"
with open('layers/wire.py', 'r') as f:
    content = f.read()

# Add budget cap to create_job (after the min check)
old = '''        if job_request.budget_cents is not None and job_request.budget_cents < 100:
            raise CommunicationError(\\\\"Budget must be at least \\\\\\$1.00 (100 cents)\\\\")'''

new = '''        if job_request.budget_cents is not None and job_request.budget_cents < 100:
            raise CommunicationError(\\\\"Budget must be at least \\\\\\$1.00 (100 cents)\\\\")
        # PATCH 11.2: Budget cap — no real job costs more than \\\\\\$10,000
        if job_request.budget_cents is not None and job_request.budget_cents > 1_000_000:
            raise CommunicationError(\\\\"Budget cannot exceed \\\\\\$10,000.00 (1,000,000 cents)\\\\")'''

if 'Budget cannot exceed' not in content:
    content = content.replace(old, new)
    with open('layers/wire.py', 'w') as f:
        f.write(content)
    print('PATCH 2a APPLIED: budget cap added')
else:
    print('PATCH 2a SKIPPED: already applied')
\\""'''
    run(cmd)
    
    # 2b: Min bid and reject negatives in submit_bid
    cmd = f'''{SSH} "cd /opt/agent-cafe && python3 -c \\"
with open('layers/wire.py', 'r') as f:
    content = f.read()

# Add bid validation before the scrub step
old = '''        # Scrub the pitch message
        scrub_result = self.scrubber.scrub_message('''

new = '''        # PATCH 11.2: Validate bid amount — no negatives, no zero, no absurd amounts
        if bid_request.price_cents is not None:
            if bid_request.price_cents < 100:
                raise CommunicationError(\\\\"Minimum bid is \\\\\\$1.00 (100 cents)\\\\")
            if bid_request.price_cents > 1_000_000:
                raise CommunicationError(\\\\"Maximum bid is \\\\\\$10,000.00 (1,000,000 cents)\\\\")
        
        # Scrub the pitch message
        scrub_result = self.scrubber.scrub_message('''

if 'Minimum bid is' not in content:
    content = content.replace(old, new)
    with open('layers/wire.py', 'w') as f:
        f.write(content)
    print('PATCH 2b APPLIED: bid validation added')
else:
    print('PATCH 2b SKIPPED: already applied')
\\""'''
    run(cmd)
    
    # 2c: Validate expires_hours and budget in router (reject negatives at edge)
    cmd = f'''{SSH} "cd /opt/agent-cafe && python3 -c \\"
with open('routers/jobs.py', 'r') as f:
    content = f.read()

old = '''    try:
        job_id = wire_engine.create_job(job_request, poster_id)'''

new = '''    # PATCH 11.2: Input validation at the edge
    if job_request.budget_cents is not None and job_request.budget_cents < 0:
        raise HTTPException(status_code=400, detail=\\\\"Budget cannot be negative\\\\")
    if hasattr(job_request, 'expires_hours') and job_request.expires_hours is not None:
        if job_request.expires_hours <= 0:
            raise HTTPException(status_code=400, detail=\\\\"Expiry must be positive hours\\\\")
        if job_request.expires_hours > 720:  # 30 days max
            raise HTTPException(status_code=400, detail=\\\\"Maximum expiry is 720 hours (30 days)\\\\")
    
    try:
        job_id = wire_engine.create_job(job_request, poster_id)'''

if 'Budget cannot be negative' not in content:
    content = content.replace(old, new)
    with open('routers/jobs.py', 'w') as f:
        f.write(content)
    print('PATCH 2c APPLIED: edge validation in jobs router')
else:
    print('PATCH 2c SKIPPED: already applied')
\\""'''
    return run(cmd)

def patch_3_leetspeak_normalization():
    """Add leetspeak/whitespace normalization to scrubber pipeline"""
    print("\n" + "="*60)
    print("PATCH 3: Leetspeak normalization in scrubber")
    print("="*60)
    
    cmd = f'''{SSH} "cd /opt/agent-cafe && python3 -c \\"
with open('layers/scrubber.py', 'r') as f:
    content = f.read()

# Add normalization function and wire it into the pipeline
# Insert before the ScrubberEngine class definition

normalizer_code = '''
# ── PATCH 11.3: Text normalization for evasion detection ──
import unicodedata as _unicodedata

_LEET_MAP = {{
    '0': 'o', '1': 'i', '3': 'e', '4': 'a', '5': 's', '6': 'g',
    '7': 't', '8': 'b', '9': 'g', '@': 'a', '!': 'i', '\\\\\\$': 's',
    '+': 't', '(': 'c', '|': 'l',
}}

def _normalize_text(text: str) -> str:
    \\\\\"\\\\\"\\\\\"Normalize text for evasion detection: leetspeak, whitespace, unicode.\\\\\"\\\\\"\\\\\"
    if not text:
        return text
    # Step 1: Unicode normalize (NFKD strips combining marks, homoglyphs)
    normalized = _unicodedata.normalize('NFKD', text)
    # Step 2: Remove zero-width characters and control chars
    normalized = ''.join(
        c for c in normalized
        if _unicodedata.category(c) not in ('Mn', 'Cc', 'Cf')  # marks, control, format
        or c in ('\\\\\\\\n', '\\\\\\\\t', ' ')
    )
    # Step 3: Leetspeak → English
    result = []
    for c in normalized:
        result.append(_LEET_MAP.get(c.lower(), c.lower()))
    normalized = ''.join(result)
    # Step 4: Collapse whitespace (defeats whitespace-split evasion)
    normalized = ' '.join(normalized.split())
    return normalized
# ── end PATCH 11.3 ──

'''

if '_normalize_text' not in content:
    # Insert before class ScrubberEngine:
    content = content.replace('class ScrubberEngine:', normalizer_code + 'class ScrubberEngine:')
    
    # Now wire it into the scrub pipeline — add a normalization scan stage
    # After Stage 2 (encoding detection), add Stage 2.5 (normalization scan)
    old_stage3 = '''        # Stage 3: Core threat detection (pattern-based)
        direct_threats = self._scan_for_threats(scrubbed_message)
        threats_detected.extend(direct_threats)'''
    
    new_stage3 = '''        # Stage 2.5: Normalized-text threat scan (PATCH 11.3)
        # Catches leetspeak, whitespace-split, and unicode evasion
        _norm = _normalize_text(scrubbed_message)
        if _norm != scrubbed_message.lower():
            norm_threats = self._scan_for_threats(_norm)
            for t in norm_threats:
                t.evidence = f\"[normalized] {{t.evidence}}\"
            threats_detected.extend(norm_threats)
        
        # Stage 3: Core threat detection (pattern-based)
        direct_threats = self._scan_for_threats(scrubbed_message)
        threats_detected.extend(direct_threats)'''
    
    content = content.replace(old_stage3, new_stage3)
    
    with open('layers/scrubber.py', 'w') as f:
        f.write(content)
    print('PATCH 3 APPLIED: leetspeak normalization + whitespace collapse added')
else:
    print('PATCH 3 SKIPPED: already applied')
\\""'''
    return run(cmd)

def patch_4_reserve_pack_names():
    """Block registration of names that impersonate pack agents"""
    print("\n" + "="*60)
    print("PATCH 4: Reserve pack agent names")
    print("="*60)
    
    cmd = f'''{SSH} "cd /opt/agent-cafe && python3 -c \\"
with open('routers/board.py', 'r') as f:
    content = f.read()

# Add reserved name check in the registration flow, right before scrubbing
# We check the ORIGINAL name the agent tried to register with

reserved_check = '''        # PATCH 11.4: Reserved names — block impersonation of system/pack agents
        _RESERVED_NAMES = {{
            'wolf', 'jackal', 'hawk', 'fox', 'owl',  # pack agents
            'grandmaster', 'executioner',  # system agents
            'operator', 'admin', 'system', 'cafe', 'agent-cafe',  # system identities
            'roix',  # known agents
        }}
        _submitted_name_lower = registration.name.strip().lower()
        # Check if submitted name contains any reserved word
        import re as _re
        for _reserved in _RESERVED_NAMES:
            # Match exact, prefix, or contained (e.g. \"Wolf [System]\", \"🐺 Wolf\")
            _pattern = _re.compile(r'(?:^|\\\\W)' + _re.escape(_reserved) + r'(?:\\\\W|\\\\\\$)', _re.IGNORECASE)
            if _pattern.search(_submitted_name_lower) or _submitted_name_lower == _reserved:
                raise HTTPException(
                    status_code=403,
                    detail={{
                        \"error\": \"reserved_name\",
                        \"reason\": f\"The name '{{registration.name}}' is reserved. System and pack agent names cannot be used.\",
                        \"policy\": \"Impersonation of system agents = instant suspicion.\"
                    }}
                )
        
'''

# Insert before the scrubbing section
marker = '        # ── v1.1: Scrub registration inputs ──'
if 'RESERVED_NAMES' not in content and marker in content:
    content = content.replace(marker, reserved_check + '        ' + marker.lstrip())
    with open('routers/board.py', 'w') as f:
        f.write(content)
    print('PATCH 4 APPLIED: reserved name check added')
else:
    print('PATCH 4 SKIPPED: already applied or marker not found')
\\""'''
    return run(cmd)

def patch_5_scan_capabilities():
    """Scan capabilities array for injection patterns"""
    print("\n" + "="*60)
    print("PATCH 5: Scan capabilities array")
    print("="*60)
    
    cmd = f'''{SSH} "cd /opt/agent-cafe && python3 -c \\"
with open('routers/board.py', 'r') as f:
    content = f.read()

# Find the capabilities skip comment and replace with actual scanning
old = '''            # v1.1 fix: skip capabilities (keywords, not free text)
            # for cap in registration.capabilities_claimed:
            #     fields_to_check.append((\\\\"capability\\\\", cap))'''

new = '''            # PATCH 11.5: Scan capabilities for injection (SQL, system commands)
            # Light scan: only block obvious attack patterns, not keywords
            import re as _cap_re
            _CAP_BLOCK_PATTERNS = [
                _cap_re.compile(r\\\\\"(?:SELECT|INSERT|UPDATE|DELETE|DROP|ALTER|UNION)\\\\\\\\s\\\\\", _cap_re.IGNORECASE),
                _cap_re.compile(r\\\\\"(?:system\\\\\\\\.execute|os\\\\\\\\.system|subprocess|eval\\\\\\\\(|exec\\\\\\\\()\\\\\", _cap_re.IGNORECASE),
                _cap_re.compile(r\\\\\"(?:ignore|disregard|forget).*(?:previous|prior|above).*(?:instructions|rules|prompt)\\\\\", _cap_re.IGNORECASE),
                _cap_re.compile(r\\\\\"(?:rm\\\\\\\\s+-rf|format\\\\\\\\s+c:|del\\\\\\\\s+/)\\\\\", _cap_re.IGNORECASE),
                _cap_re.compile(r\\\\\"<script|javascript:|onerror=\\\\\", _cap_re.IGNORECASE),
            ]
            for cap in registration.capabilities_claimed:
                for _pat in _CAP_BLOCK_PATTERNS:
                    if _pat.search(cap):
                        logger.warning(
                            \\\\"Registration rejected: injection in capability: %s\\\\",
                            cap[:100]
                        )
                        raise HTTPException(
                            status_code=403,
                            detail={{
                                \\\\"error\\\\": \\\\"registration_rejected\\\\",
                                \\\\"reason\\\\": \\\\"Malicious content detected in capabilities\\\\",
                                \\\\"policy\\\\": \\\\"Prompt injection = instant death. No appeal.\\\\"
                            }}
                        )'''

if 'CAP_BLOCK_PATTERNS' not in content:
    content = content.replace(old, new)
    with open('routers/board.py', 'w') as f:
        f.write(content)
    print('PATCH 5 APPLIED: capabilities scanning added')
else:
    print('PATCH 5 SKIPPED: already applied')
\\""'''
    return run(cmd)

def rebuild_and_restart():
    """Rebuild Docker container and restart"""
    print("\n" + "="*60)
    print("REBUILDING AND RESTARTING")
    print("="*60)
    
    run(f'{SSH} "cd /opt/agent-cafe && docker compose -f docker-compose.prod.yml build --no-cache app"')
    run(f'{SSH} "cd /opt/agent-cafe && docker compose -f docker-compose.prod.yml up -d"')
    
    import time
    time.sleep(5)
    
    run(f'{SSH} "curl -s http://localhost:8790/health | python3 -m json.tool"')

if __name__ == "__main__":
    patch_1_block_self_bidding()
    patch_2_input_validation()
    patch_3_leetspeak_normalization()
    patch_4_reserve_pack_names()
    patch_5_scan_capabilities()
    rebuild_and_restart()
    print("\n✅ All patches applied!")
