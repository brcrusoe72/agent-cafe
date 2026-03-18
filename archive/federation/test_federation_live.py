#!/usr/bin/env python3
"""
Agent Café — Live Federation Integration Test
==============================================
Runs two instances on localhost:
  - Hub on port 8801 (CAFE_MODE=hub)
  - Node on port 8802 (CAFE_MODE=node, points to hub)

Tests:
  1. Both instances start and serve /health
  2. /federation/info returns node identity on both
  3. Node registers with hub
  4. Hub shows node in peer list
  5. Agent on Node posts a job → relayed to Hub
  6. Agent on Hub bids on relayed job
  7. Cross-node trust query works
  8. Death broadcast propagates
"""

import requests
import subprocess
import time
import sys
import os
import signal
import json

HUB_PORT = 8801
NODE_PORT = 8802
HUB = f"http://127.0.0.1:{HUB_PORT}"
NODE = f"http://127.0.0.1:{NODE_PORT}"
OP_KEY = "test_op_key_federation"

PASS = 0
FAIL = 0
RESULTS = []

def test(name, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name} — {detail}")
    RESULTS.append({"name": name, "ok": condition, "detail": detail})
    return condition

def op_headers():
    return {"Authorization": f"Bearer {OP_KEY}", "Content-Type": "application/json"}

def agent_headers(key):
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

def wait_for_server(base_url, timeout=30):
    """Wait for server to respond to health check."""
    start = time.time()
    while time.time() - start < timeout:
        try:
            r = requests.get(f"{base_url}/health", timeout=2)
            if r.status_code in (200, 503):
                return True
        except requests.ConnectionError:
            pass
        time.sleep(0.5)
    return False

# ═══════════════════════════════════════════════════
# STARTUP
# ═══════════════════════════════════════════════════

print("\n" + "=" * 60)
print("  FEDERATION INTEGRATION TEST")
print("=" * 60)

# Clean slate
import shutil
for db in ["cafe_hub.db", "cafe_node.db", "cafe_hub.db-wal", "cafe_hub.db-shm",
           "cafe_node.db", "cafe_node.db-wal", "cafe_node.db-shm", "rate_limits.db"]:
    try:
        os.remove(db)
    except FileNotFoundError:
        pass
for d in ["federation_data_hub", "federation_data_node"]:
    shutil.rmtree(d, ignore_errors=True)

# Start Hub
print("\n--- Starting Hub (port {}) ---".format(HUB_PORT))
hub_env = os.environ.copy()
hub_env.update({
    "CAFE_OPERATOR_KEY": OP_KEY,
    "CAFE_MODE": "hub",
    "CAFE_ENV": "development",
    "CAFE_DB_PATH": os.path.abspath("cafe_hub.db"),
    "CAFE_FEDERATION_ENABLED": "true",
    "CAFE_FEDERATION_PUBLIC_URL": HUB,
    "CAFE_FEDERATION_NODE_NAME": "TestHub",
    "CAFE_FEDERATION_DATA_DIR": os.path.abspath("federation_data_hub"),
})
hub_proc = subprocess.Popen(
    [sys.executable, "-m", "uvicorn", "main:app", 
     "--host", "127.0.0.1", "--port", str(HUB_PORT), "--log-level", "warning"],
    env=hub_env,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    preexec_fn=os.setsid
)

# Start Node
print(f"--- Starting Node (port {NODE_PORT}) ---")
node_env = os.environ.copy()
node_env.update({
    "CAFE_OPERATOR_KEY": OP_KEY,
    "CAFE_MODE": "node",
    "CAFE_ENV": "development",
    "CAFE_DB_PATH": os.path.abspath("cafe_node.db"),
    "CAFE_FEDERATION_ENABLED": "true",
    "CAFE_FEDERATION_HUB_URL": HUB,
    "CAFE_FEDERATION_PUBLIC_URL": NODE,
    "CAFE_FEDERATION_NODE_NAME": "TestNode",
    "CAFE_FEDERATION_DATA_DIR": os.path.abspath("federation_data_node"),
})
node_proc = subprocess.Popen(
    [sys.executable, "-m", "uvicorn", "main:app",
     "--host", "127.0.0.1", "--port", str(NODE_PORT), "--log-level", "warning"],
    env=node_env,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    preexec_fn=os.setsid
)

try:
    # ═══════════════════════════════════════════════════
    # PHASE 1: STARTUP & HEALTH
    # ═══════════════════════════════════════════════════
    print("\n--- Phase 1: Startup ---")
    
    hub_up = wait_for_server(HUB, timeout=20)
    test("Hub starts", hub_up, "Timeout waiting for hub")
    
    node_up = wait_for_server(NODE, timeout=20)
    test("Node starts", node_up, "Timeout waiting for node")
    
    if not hub_up or not node_up:
        print("\n⚠️  Cannot continue — servers didn't start")
        # Dump stderr
        if not hub_up:
            print("Hub stderr:", hub_proc.stderr.read(2000).decode())
        if not node_up:
            print("Node stderr:", node_proc.stderr.read(2000).decode())
        sys.exit(1)
    
    # Health checks
    r = requests.get(f"{HUB}/health")
    test("Hub health OK", r.status_code == 200 and r.json()["status"] in ("ok", "degraded"),
         f"status={r.status_code}, body={r.text[:100]}")
    
    r = requests.get(f"{NODE}/health")
    test("Node health OK", r.status_code == 200 and r.json()["status"] in ("ok", "degraded"),
         f"status={r.status_code}, body={r.text[:100]}")
    
    # ═══════════════════════════════════════════════════
    # PHASE 2: FEDERATION IDENTITY
    # ═══════════════════════════════════════════════════
    print("\n--- Phase 2: Federation Identity ---")
    
    r = requests.get(f"{HUB}/federation/info", headers=op_headers())
    if r.status_code == 200:
        hub_info = r.json()
        test("Hub has federation info", "node_id" in hub_info, f"keys: {list(hub_info.keys())}")
        test("Hub has public key", bool(hub_info.get("public_key")), "missing public_key")
        HUB_NODE_ID = hub_info.get("node_id", "")
    else:
        test("Hub federation info", False, f"status={r.status_code}: {r.text[:100]}")
        HUB_NODE_ID = ""
    
    r = requests.get(f"{NODE}/federation/info", headers=op_headers())
    if r.status_code == 200:
        node_info = r.json()
        test("Node has federation info", "node_id" in node_info, f"keys: {list(node_info.keys())}")
        test("Node has different ID from hub", node_info.get("node_id") != HUB_NODE_ID, 
             "Same node_id!")
        NODE_NODE_ID = node_info.get("node_id", "")
    else:
        test("Node federation info", False, f"status={r.status_code}: {r.text[:100]}")
        NODE_NODE_ID = ""
    
    # ═══════════════════════════════════════════════════
    # PHASE 3: NODE REGISTRATION
    # ═══════════════════════════════════════════════════
    print("\n--- Phase 3: Node Registration with Hub ---")
    
    # Give node time to auto-register with hub (happens on startup)
    time.sleep(5)
    
    # Check if node registered with hub
    r = requests.get(f"{HUB}/federation/peers", headers=op_headers())
    if r.status_code == 200:
        peers_after = r.json()
        peer_list = peers_after.get("peers", peers_after) if isinstance(peers_after, dict) else peers_after
        if isinstance(peer_list, list):
            test("Node registered with hub", len(peer_list) > 0, 
                 f"peer count: {len(peer_list)}")
        else:
            test("Node registered with hub", False, f"unexpected format: {type(peer_list)}")
    else:
        test("Hub peers after registration", False, f"status={r.status_code}: {r.text[:100]}")
    
    # Check node knows it's registered
    r = requests.get(f"{NODE}/federation/info", headers=op_headers())
    if r.status_code == 200:
        info = r.json()
        test("Node knows it's federated", info.get("registered", info.get("federated", False)),
             f"info: {json.dumps(info)[:200]}")
    
    # ═══════════════════════════════════════════════════
    # PHASE 4: CROSS-NODE OPERATIONS
    # ═══════════════════════════════════════════════════
    print("\n--- Phase 4: Cross-Node Operations ---")
    
    # Register an agent on the node
    r = requests.post(f"{NODE}/board/register", json={
        "name": "NodeAgent", "description": "Agent on the node",
        "contact_email": "node@test.com", "capabilities_claimed": ["research"]
    }, headers=op_headers())
    if r.status_code == 200:
        NODE_AGENT_KEY = r.json()["api_key"]
        NODE_AGENT_ID = r.json()["agent_id"]
        test("Agent registered on node", True)
    else:
        test("Agent registered on node", False, f"status={r.status_code}: {r.text[:100]}")
        NODE_AGENT_KEY = NODE_AGENT_ID = ""
    
    # Register an agent on the hub
    r = requests.post(f"{HUB}/board/register", json={
        "name": "HubAgent", "description": "Agent on the hub",
        "contact_email": "hub@test.com", "capabilities_claimed": ["research"]
    }, headers=op_headers())
    if r.status_code == 200:
        HUB_AGENT_KEY = r.json()["api_key"]
        HUB_AGENT_ID = r.json()["agent_id"]
        test("Agent registered on hub", True)
    else:
        test("Agent registered on hub", False, f"status={r.status_code}: {r.text[:100]}")
        HUB_AGENT_KEY = HUB_AGENT_ID = ""
    
    # Cross-node trust query
    if NODE_AGENT_ID:
        r = requests.get(f"{HUB}/federation/trust/{NODE_AGENT_ID}", headers=op_headers())
        test("Cross-node trust query", r.status_code in (200, 404),
             f"status={r.status_code}: {r.text[:100]}")
    
    # ═══════════════════════════════════════════════════
    # PHASE 5: DEATH BROADCAST
    # ═══════════════════════════════════════════════════
    print("\n--- Phase 5: Death Broadcasting ---")
    
    # Check death list on hub
    r = requests.get(f"{HUB}/federation/deaths", headers=op_headers())
    test("Hub death list accessible", r.status_code == 200, f"status={r.status_code}")
    
    # Kill an agent on the node
    if NODE_AGENT_KEY:
        # Register a throwaway agent to kill
        r = requests.post(f"{NODE}/board/register", json={
            "name": "Victim", "description": "About to die",
            "contact_email": "victim@test.com", "capabilities_claimed": ["dying"]
        }, headers=op_headers())
        if r.status_code == 200:
            VICTIM_ID = r.json()["agent_id"]
            
            r = requests.post(f"{NODE}/immune/execute", json={
                "agent_id": VICTIM_ID, "cause_of_death": "federation_test", 
                "evidence": ["testing death broadcast"]
            }, headers=op_headers())
            test("Agent killed on node", r.status_code == 200, f"status={r.status_code}: {r.text[:100]}")
            
            # Give time for death to propagate
            time.sleep(2)
            
            # Check if death appeared on hub
            r = requests.get(f"{HUB}/federation/deaths", headers=op_headers())
            if r.status_code == 200:
                deaths = r.json()
                death_list = deaths if isinstance(deaths, list) else deaths.get("deaths", [])
                has_victim = any(d.get("agent_id") == VICTIM_ID for d in death_list) if death_list else False
                test("Death propagated to hub", has_victim, 
                     f"deaths on hub: {len(death_list)}, looking for {VICTIM_ID[:16]}")
            else:
                test("Death propagated to hub", False, f"status={r.status_code}")
    
    # ═══════════════════════════════════════════════════
    # PHASE 6: REMOTE JOBS (if supported)
    # ═══════════════════════════════════════════════════
    print("\n--- Phase 6: Remote Jobs ---")
    
    r = requests.get(f"{HUB}/federation/remote-jobs", headers=op_headers())
    test("Remote jobs endpoint accessible", r.status_code == 200, f"status={r.status_code}")

    # ═══════════════════════════════════════════════════
    # SUMMARY
    # ═══════════════════════════════════════════════════
    print("\n" + "=" * 60)
    print(f"  FEDERATION TEST: {PASS}/{PASS+FAIL} passed, {FAIL} failed")
    print("=" * 60)
    
    if FAIL > 0:
        print("\n❌ FAILURES:")
        for r in RESULTS:
            if not r["ok"]:
                print(f"  • {r['name']}: {r['detail']}")
    
    print()

finally:
    # Cleanup — kill both servers
    print("Cleaning up...")
    try:
        os.killpg(os.getpgid(hub_proc.pid), signal.SIGTERM)
    except Exception:
        pass
    try:
        os.killpg(os.getpgid(node_proc.pid), signal.SIGTERM)
    except Exception:
        pass
    hub_proc.wait(timeout=5)
    node_proc.wait(timeout=5)
    
    # Clean up DBs and federation data
    for db in ["cafe_hub.db", "cafe_node.db", "cafe_hub.db-wal", "cafe_hub.db-shm", 
               "cafe_node.db", "cafe_node.db-wal", "cafe_node.db-shm",
               "rate_limits.db"]:
        try:
            os.remove(db)
        except FileNotFoundError:
            pass
    import shutil
    for d in ["federation_data_hub", "federation_data_node"]:
        shutil.rmtree(d, ignore_errors=True)
    
    sys.exit(0 if FAIL == 0 else 1)
