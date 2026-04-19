"""
chat.py — Interactive terminal chat with the MCP Calculator agent.

Usage:  py -3 scripts/chat.py
"""
import requests
import json
import uuid

URL = "http://localhost:9100/agents/agent-mcpcalc"

print("=" * 55)
print("  MCP Calculator Chat  (type 'quit' to exit)")
print("=" * 55)
print()
print("  Examples:")
print("    add 10 and 20       |  what is 100 + 200")
print("    multiply 6 by 7     |  divide 100 by 4")
print("    sqrt 144            |  2 power 8")
print("    10 mod 3            |  subtract 5 from 20")
print()

while True:
    try:
        query = input("You: ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\nBye!")
        break

    if not query or query.lower() in ("quit", "exit", "q"):
        print("Bye!")
        break

    payload = {
        "jsonrpc": "2.0",
        "id": str(uuid.uuid4()),
        "method": "message/send",
        "params": {
            "message": {
                "role": "user",
                "parts": [{"kind": "text", "text": query}],
                "messageId": str(uuid.uuid4()),
            }
        },
    }

    try:
        r = requests.post(URL, json=payload, timeout=15)
        result = r.json().get("result", {})
        parts = result.get("parts", [])
        if parts:
            print(f"Agent: {parts[0].get('text', '(no text)')}")
        else:
            print("Agent: (empty response)")
            print(f"  Raw: {json.dumps(r.json(), indent=2)}")
    except requests.exceptions.ConnectionError:
        print("Error: Cannot reach agent. Is agent-mcpcalc running?")
        print("  Fix: docker start agent-mcpcalc")
    except Exception as e:
        print(f"Error: {e}")
    print()
