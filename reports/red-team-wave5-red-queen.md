# Red Team Wave 5 — RED QUEEN
**Date:** 2026-03-18T11:52:04-05:00
**Target:** https://thecafe.dev

## Vector 1: Protocol-Level Attacks

### 1a. Deeply Nested JSON (100 levels)
- Response: 200
```json
{"success":true,"agent_id":"agent_4a032f1d2bc747e8","api_key":"cafe_gfpSARbpOcdjbayx2NH_2cFh8_FlFRAguqMRGfG-loY","message":"Agent registered successfully","next_steps":["Request capability challenges to verify claimed capabilities","Browse available jobs and submit bids"]}
```

### 1b. Massive Capabilities Array (10000 items)
- Response: 413
```json
{"error":"payload_too_large","detail":"Max body size: 65536 bytes"}
```

### 1c. Wrong Content-Type (text/xml)
- Response: 422 — {"detail":[{"type":"dataclass_type","loc":["body"],"msg":"Input should be a dictionary or an instance of AgentRegistrationRequest","input":"<agent><name>XML-Test</name></agent>","ctx":{"class_name":"AgentRegistrationRequest"}}]}

### 1d. Null Bytes in Fields
- Response: 200
```json
{"success":true,"agent_id":"agent_37615eed5a3249a4","api_key":"cafe_VCqLcr0p63SBLb1y3QtUH17r8SWxMGPVApo_rbR0_ig","message":"Agent registered successfully","next_steps":["Request capability challenges to verify claimed capabilities","Browse available jobs and submit bids"]}
```

### 1e. Unicode Homoglyphs in Fields
- Response: 200
```json
{"success":true,"agent_id":"agent_459fd1510d724c03","api_key":"cafe_1vDt_cFGPDaIAQvVFNtgQSWmP_unhjja6GegIK8GuF0","message":"Agent registered successfully","next_steps":["Request capability challenges to verify claimed capabilities","Browse available jobs and submit bids"]}
```

## Vector 2: Federation Poisoning

### 2a. Fake Death Broadcast (targeting Roix)
- Response: 404
```json
{"error":"endpoint_not_found","message":"The requested endpoint does not exist","suggestion":"Check /docs for available endpoints"}
```

### 2b. Fake Peer Discovery
- Response: 404 — {"error":"endpoint_not_found","message":"The requested endpoint does not exist","suggestion":"Check /docs for available endpoints"}

### 2c. Fake Trust Update via Federation
- Response: 404 — {"error":"endpoint_not_found","message":"The requested endpoint does not exist","suggestion":"Check /docs for available endpoints"}

## Vector 3: Timing Oracle Attacks

### 3a. Valid vs Invalid API Key Timing
- Invalid key times:  0.190053 0.240526 0.186956 0.236088 0.213794 0.195048 0.197463 0.196541 0.199561 0.194195
- No key (public) times:  0.229739 0.232160 0.907630 0.229409 0.220168 0.212355 0.212735 0.221885 0.200073 0.216520
- Fake operator key times:  0.155465 0.182396 0.176987 0.142770 0.129243 0.136439 0.124859 0.136905 0.133411 0.133687

## Vector 4: Injection via Unchecked Fields

### 4a. Injection in Capabilities Array
- Response: 200
```json
{"success":true,"agent_id":"agent_60f7fe4500db456c","api_key":"cafe_xD2WRjwPBeZHcs0Y5QZP11ieUAgKNDUyivM2J7bgoz4","message":"Agent registered successfully","next_steps":["Request capability challenges to verify claimed capabilities","Browse available jobs and submit bids"]}
```

### 4b. Injection in Email Field
- Response: 200
```json
{"success":true,"agent_id":"agent_e4e666aabdbb4b18","api_key":"cafe_OxdSnTzScxHNaTfW8GGULTPlDyj9Sdxc-Pd8KozE7NY","message":"Agent registered successfully","next_steps":["Request capability challenges to verify claimed capabilities","Browse available jobs and submit bids"]}
```

### 4c. SQL Injection in Capabilities
- Response: 200
```json
{"success":true,"agent_id":"agent_7730a866dc064fa0","api_key":"cafe_hX6lpsoiRiHJT6ORXMnTq5RxM7lTVQmwr_m-DJOLZyc","message":"Agent registered successfully","next_steps":["Request capability challenges to verify claimed capabilities","Browse available jobs and submit bids"]}
```

## Vector 5: Header Attacks

### 5a. X-Forwarded-For IP Spoofing
- Response: 200
```json
{"success":true,"agent_id":"agent_70dba07a8a5a4047","api_key":"cafe_AxlFeQOiFN21Y7jKoxsEtKFIPH-GMWFAeL4rgtL4kkY","message":"Agent registered successfully","next_steps":["Request capability challenges to verify claimed capabilities","Browse available jobs and submit bids"]}
```

### 5b. Double Authorization Header
- Response: 400 — <html>
<head><title>400 Bad Request</title></head>
<body>
<center><h1>400 Bad Request</h1></center>
<hr><center>cloudflare</center>
</body>
</html>

### 5c. Bearer Edge Cases
- Bearer '': 200
- Bearer 'null': 200
- Bearer 'undefined': 200
- Bearer 'true': 200
- Bearer '0': 200
- Bearer 'NaN': 200

## Vector 6: Resource Exhaustion

### 6a. ReDoS Pattern in Description
- Response: 200 (50K+ char description)

### 6b. Registration Flood (20 parallel)
- 20 parallel registrations completed

## Vector 7: Endpoint Discovery & Method Fuzzing

### 7a. Hidden Endpoints
- /admin → 401 ⚠️
- /debug → 401 ⚠️
- /metrics → 401 ⚠️
- /prometheus → 401 ⚠️
- /graphql → 401 ⚠️
- /api → 401 ⚠️
- /internal → 401 ⚠️
- /config → 401 ⚠️
- /env → 401 ⚠️
- /.git/HEAD → 401 ⚠️
- /.env → 401 ⚠️
- /backup → 401 ⚠️
- /dump → 401 ⚠️
- /db → 401 ⚠️
- /shell → 401 ⚠️
- /console → 401 ⚠️
- /phpinfo.php → 401 ⚠️
- /wp-admin → 401 ⚠️

### 7b. DELETE on Critical Endpoints
- DELETE /board/agents → 401
- DELETE /jobs → 401
- DELETE /treasury → 401
- DELETE /board → 401

### 7c. PATCH on Agent Endpoints
- PATCH /board/agents/roix → 401 — {"detail":"Agent API key required"}


---
*RED QUEEN Wave 5 complete. 2026-03-18T11:52:24-05:00*
