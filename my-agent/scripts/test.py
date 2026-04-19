"""
test.py — End-to-end test of the deployed agent through Kong gateway.

Usage:  py -3 scripts/test.py
"""
import requests
import json
import uuid
import sys


KONG_URL = "http://localhost:9100/agents/agent-mcpcalc"


def test_agentcard():
    print("=== Test 1: GET AgentCard ===")
    r = requests.get(KONG_URL, timeout=10)
    print(f"Status: {r.status_code}")
    if r.status_code == 200:
        card = r.json()
        print(f"Name: {card.get('name')}")
        print(f"Skills: {len(card.get('skills', []))}")
        return True
    else:
        print(f"FAIL: {r.text[:200]}")
        return False


def test_message_send():
    print("\n=== Test 2: message/send (A2A v0.2.9) ===")
    payload = {
        "jsonrpc": "2.0",
        "id": "test-1",
        "method": "message/send",
        "params": {
            "message": {
                "role": "user",
                "parts": [{"kind": "text", "text": "add 42 and 58"}],
                "messageId": str(uuid.uuid4()),
            },
        },
    }

    r = requests.post(KONG_URL, json=payload, timeout=10)
    print(f"Status: {r.status_code}")
    data = r.json()
    result = data.get("result", {})
    print(f"result.kind = {result.get('kind')!r}")
    parts = result.get("parts", [])
    if parts:
        print(f"part.kind = {parts[0].get('kind')!r}")
        print(f"part.text = {parts[0].get('text')!r}")
        ok = result.get("kind") == "message" and parts[0].get("kind") == "text"
        print(f"\n{'PASS' if ok else 'FAIL'}: A2A format {'matches' if ok else 'DOES NOT match'} platform expectations")
        return ok
    else:
        print(f"FAIL: No parts in response")
        print(f"Raw: {json.dumps(data, indent=2)}")
        return False


def test_tasks_send():
    print("\n=== Test 3: tasks/send (legacy) ===")
    payload = {
        "jsonrpc": "2.0",
        "id": "test-2",
        "method": "tasks/send",
        "params": {
            "message": {
                "role": "user",
                "parts": [{"kind": "text", "text": "multiply 7 by 8"}],
                "messageId": str(uuid.uuid4()),
            },
        },
    }

    r = requests.post(KONG_URL, json=payload, timeout=10)
    print(f"Status: {r.status_code}")
    result = r.json().get("result", {})
    parts = result.get("parts", [])
    if parts:
        print(f"Answer: {parts[0].get('text')}")
        return True
    return False


if __name__ == "__main__":
    results = []
    try:
        results.append(test_agentcard())
        results.append(test_message_send())
        results.append(test_tasks_send())
    except requests.exceptions.ConnectionError:
        print("\nERROR: Cannot reach agent. Is it running?")
        print("  Check:   docker ps --filter name=agent-mcpcalc")
        print("  Restart: docker start agent-mcpcalc")
        sys.exit(1)

    print(f"\n{'='*40}")
    print(f"Results: {sum(results)}/{len(results)} passed")
    sys.exit(0 if all(results) else 1)
