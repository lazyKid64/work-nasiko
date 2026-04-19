"""
deploy.py — Full deploy pipeline: build zip, extract to agents dir, trigger via Redis.
All source reads from my-agent/. Writes to nasiko/agents/ at runtime (required by platform).

Usage:  py -3 scripts/deploy.py
"""
import zipfile
import os
import sys
import shutil
import json
from datetime import datetime, timezone

# Resolve paths relative to my-agent/
MY_AGENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NASIKO_ROOT = os.path.dirname(MY_AGENT_DIR)
AGENT_SRC = os.path.join(MY_AGENT_DIR, "examples", "mcp-calculator-server-v2")
DIST_DIR = os.path.join(MY_AGENT_DIR, "dist")
ZIP_PATH = os.path.join(DIST_DIR, "mcpcalc.zip")

# Platform directories (outside my-agent, written at runtime only)
AGENTS_DIR = os.path.join(NASIKO_ROOT, "agents", "mcpcalc", "v1.0.0")

OWNER_ID = "1e05d931-2958-46da-85be-0f7af203f1a7"


def step_build_zip():
    """Step 1: Build the zip from agent source."""
    print("=== Step 1: Building mcpcalc.zip ===")
    os.makedirs(DIST_DIR, exist_ok=True)
    if os.path.exists(ZIP_PATH):
        os.remove(ZIP_PATH)

    with zipfile.ZipFile(ZIP_PATH, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(AGENT_SRC):
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            for f in files:
                full = os.path.join(root, f)
                arc = os.path.relpath(full, AGENT_SRC).replace(os.sep, "/")
                zf.writestr(arc, open(full, "rb").read())
                print(f"  Added: {arc}")
    print(f"  Built: {ZIP_PATH}")


def step_extract_to_agents():
    """Step 2: Extract zip to nasiko/agents/ (platform requirement)."""
    print("\n=== Step 2: Extracting to agents directory ===")
    parent = os.path.dirname(AGENTS_DIR)
    if os.path.exists(parent):
        shutil.rmtree(parent, ignore_errors=True)
    os.makedirs(AGENTS_DIR, exist_ok=True)

    with zipfile.ZipFile(ZIP_PATH, "r") as zf:
        zf.extractall(AGENTS_DIR)

    for f in ["src/main.py", "Dockerfile", "Agentcard.json"]:
        full = os.path.join(AGENTS_DIR, f)
        if os.path.exists(full):
            print(f"  {f}: OK ({os.path.getsize(full)} bytes)")


def step_trigger_redis():
    """Step 3: Publish deploy command to Redis stream."""
    print("\n=== Step 3: Triggering deployment via Redis ===")
    try:
        import redis
    except ImportError:
        print("  ERROR: 'redis' package not installed. Run: pip install redis")
        sys.exit(1)

    r = redis.Redis(host="localhost", port=6379, db=0, decode_responses=True)
    r.ping()

    msg = {
        "command": "deploy_agent",
        "agent_name": "mcpcalc",
        "agent_path": "/app/agents/mcpcalc/v1.0.0",
        "base_url": "http://nasiko-backend:8000",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source": "nasiko-backend",
        "owner_id": OWNER_ID,
        "upload_id": f"manual-{datetime.now().strftime('%H%M%S')}",
        "upload_type": "zip",
        "version": "v1.0.0",
    }

    msg_id = r.xadd("orchestration:commands", msg)
    print(f"  Published: {msg_id}")
    print("\n  Deployment triggered! Docker build takes ~2 minutes.")
    print("  Monitor with:  docker logs nasiko-redis-listener -f")
    print("  Verify with:   docker logs agent-mcpcalc --tail 10")


if __name__ == "__main__":
    step_build_zip()
    step_extract_to_agents()
    step_trigger_redis()
