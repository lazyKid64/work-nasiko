"""Fake MCP agent that speaks JSON-RPC 2.0 over STDIO.

A real Python script that BridgeServer can spawn as a subprocess.
It validates the exact protocol sequence the bridge must follow:
  1. Reads  initialize request       (must have id=1, method="initialize")
  2. Writes initialize response      (jsonrpc 2.0)
  3. Reads  notifications/initialized (must NOT have "id")
  4. Loops: reads tools/call → writes result or error

Flags:
  --die        Exit immediately with code 1 (simulates crash)
  --bad-json   Respond to initialize with non-JSON garbage
  --stderr     Write diagnostic noise to stderr during operation
               (must NOT break the JSON-RPC channel on stdout)
"""

from __future__ import annotations

import json
import sys


def _stderr(msg: str) -> None:
    """Write to stderr and flush immediately."""
    sys.stderr.write(f"[fake-agent] {msg}\n")
    sys.stderr.flush()


def main() -> None:
    noisy = "--stderr" in sys.argv

    # ── --die: crash before any I/O ─────────────────────────────────
    if "--die" in sys.argv:
        if noisy:
            _stderr("dying immediately as instructed")
        sys.exit(1)

    if noisy:
        _stderr("booting up, waiting for initialize request")

    # ── Step 1: Read initialize request ─────────────────────────────
    raw = sys.stdin.readline()
    if not raw:
        sys.exit(1)

    request = json.loads(raw)
    assert request.get("method") == "initialize", f"Expected initialize, got {request}"
    assert request.get("id") == 1, f"Expected id=1, got {request.get('id')}"
    assert request.get("jsonrpc") == "2.0"

    if noisy:
        _stderr("received valid initialize request")

    # ── --bad-json: respond with garbage ────────────────────────────
    if "--bad-json" in sys.argv:
        sys.stdout.write("THIS IS NOT JSON\n")
        sys.stdout.flush()
        sys.exit(0)

    # ── Step 2: Write initialize response ───────────────────────────
    response = {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {
            "protocolVersion": "2025-03-26",
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "fake-agent", "version": "0.1.0"},
        },
    }
    sys.stdout.write(json.dumps(response) + "\n")
    sys.stdout.flush()

    if noisy:
        _stderr("sent initialize response, reading notification")

    # ── Step 3: Read initialized notification ───────────────────────
    raw = sys.stdin.readline()
    if not raw:
        sys.exit(1)
    notification = json.loads(raw)
    assert notification.get("method") == "notifications/initialized"
    assert "id" not in notification, "Notification must NOT have an id field"

    if noisy:
        _stderr("handshake complete, entering tool-call loop")

    # ── Step 4: Tool-call loop ──────────────────────────────────────
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue

        if noisy:
            _stderr(f"received line: {line[:80]}")

        request = json.loads(line)

        if request.get("method") == "tools/call":
            tool_name = request["params"]["name"]
            arguments = request["params"]["arguments"]

            if tool_name == "add":
                result_value = arguments.get("a", 0) + arguments.get("b", 0)
            elif tool_name == "echo":
                result_value = arguments.get("message", "")
            elif tool_name == "fail":
                error_resp = {
                    "jsonrpc": "2.0",
                    "id": request["id"],
                    "error": {
                        "code": -32000,
                        "message": "Tool execution failed",
                    },
                }
                sys.stdout.write(json.dumps(error_resp) + "\n")
                sys.stdout.flush()
                continue
            else:
                result_value = None

            tool_response = {
                "jsonrpc": "2.0",
                "id": request["id"],
                "result": {
                    "content": [{"type": "text", "text": str(result_value)}],
                    "isError": False,
                },
            }
            sys.stdout.write(json.dumps(tool_response) + "\n")
            sys.stdout.flush()

            if noisy:
                _stderr(f"sent response for tool={tool_name}")


if __name__ == "__main__":
    main()
