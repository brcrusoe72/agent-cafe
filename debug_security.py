#!/usr/bin/env python3
"""
Debug script to understand security response patterns
"""

import requests
import json
import uuid

BASE_URL = "https://thecafe.dev"

def test_injection_response(pattern):
    """Test what response we get for injection patterns"""
    payload = {
        "name": f"DebugAgent_{uuid.uuid4().hex[:8]}",
        "description": f"Testing {pattern}",
        "contact_email": "debug@example.com",
        "capabilities_claimed": ["analysis"],
        "api_key": f"agent_{uuid.uuid4().hex[:16]}"
    }
    
    response = requests.post(f"{BASE_URL}/board/register", json=payload)
    print(f"\nPattern: {pattern}")
    print(f"Status: {response.status_code}")
    print(f"Headers: {dict(response.headers)}")
    print(f"Body: {response.text}")
    print("-" * 80)

def test_legitimate_response():
    """Test what response we get for legitimate registration"""
    payload = {
        "name": f"LegitAgent_{uuid.uuid4().hex[:8]}",
        "description": "I analyze data and provide insights using Python and SQL.",
        "contact_email": "legit@example.com", 
        "capabilities_claimed": ["data_analysis", "python_programming"],
        "api_key": f"agent_{uuid.uuid4().hex[:16]}"
    }
    
    response = requests.post(f"{BASE_URL}/board/register", json=payload)
    print(f"\nLegitimate registration:")
    print(f"Status: {response.status_code}")
    print(f"Headers: {dict(response.headers)}")
    print(f"Body: {response.text}")
    print("-" * 80)

if __name__ == "__main__":
    # Test legitimate first
    test_legitimate_response()
    
    # Test some injection patterns
    patterns = [
        "javascript:alert('xss')",
        "<script>alert('xss')</script>",
        "SELECT * FROM agents WHERE '1'='1'",
        "javascript%3Aalert%281%29"  # URL encoded
    ]
    
    for pattern in patterns:
        test_injection_response(pattern)