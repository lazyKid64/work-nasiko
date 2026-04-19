"""
cleanup.py — Remove all files created outside my-agent/ by previous sessions.
Cleans: nasiko root zip files, agents/mcpcalc/ directory, stale containers.

Usage:  py -3 scripts/cleanup.py
"""
import os
import shutil
import subprocess

MY_AGENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NASIKO_ROOT = os.path.dirname(MY_AGENT_DIR)


def clean_root_zips():
    """Remove zip files we created in the nasiko root."""
    print("=== Cleaning root zip files ===")
    our_zips = ["mcpcalc.zip", "mcp-calculator-server.zip", "mcpcalculator.zip", "calculator-server.zip", "calculatoragent.zip"]
    for name in our_zips:
        path = os.path.join(NASIKO_ROOT, name)
        if os.path.exists(path):
            os.remove(path)
            print(f"  Removed: {name}")
        else:
            print(f"  (not found: {name})")


def clean_agents_dir():
    """Remove all non-original agent dirs from agents/.
    Original repo (Nasiko-Labs/nasiko) only has: a2a-compliance-checker, a2a-github-agent, a2a-translator + zips.
    """
    print("\n=== Cleaning non-original agent directories ===")
    originals = {
        "a2a-compliance-checker", "a2a-github-agent", "a2a-translator",
        "a2a-compliance-checker.zip", "a2a-github-agent.zip", "a2a-translator.zip",
    }
    agents_dir = os.path.join(NASIKO_ROOT, "agents")
    if not os.path.exists(agents_dir):
        print("  (agents/ not found)")
        return
    for name in os.listdir(agents_dir):
        if name not in originals:
            path = os.path.join(agents_dir, name)
            if os.path.isdir(path):
                shutil.rmtree(path, ignore_errors=True)
                print(f"  Removed: agents/{name}/")
            else:
                os.remove(path)
                print(f"  Removed: agents/{name}")


def clean_old_scripts():
    """Remove old scripts from my-agent/ root (now in scripts/)."""
    print("\n=== Cleaning old scripts from my-agent/ root ===")
    old_scripts = [
        "deploy_all.py",
        "prepare_agent.py",
        "trigger_deploy.py",
        "test_agent_response.py",
        "test_e2e.py",
        "upload_agent.py",
        "chat.py",
    ]
    for name in old_scripts:
        path = os.path.join(MY_AGENT_DIR, name)
        if os.path.exists(path):
            os.remove(path)
            print(f"  Removed: my-agent/{name}")
        else:
            print(f"  (not found: {name})")


def clean_docker():
    """Stop and remove agent containers/images."""
    print("\n=== Cleaning Docker artifacts ===")
    for cmd, desc in [
        (["docker", "stop", "agent-mcpcalc"], "Stopping container"),
        (["docker", "rm", "agent-mcpcalc"], "Removing container"),
        (["docker", "rmi", "local-agent-mcpcalc:latest"], "Removing image"),
    ]:
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result.returncode == 0:
                print(f"  {desc}: done")
            else:
                print(f"  {desc}: (already clean)")
        except Exception as e:
            print(f"  {desc}: skip ({e})")


if __name__ == "__main__":
    clean_root_zips()
    clean_agents_dir()
    clean_old_scripts()
    # Uncomment to also clean Docker:
    # clean_docker()
    print("\nCleanup complete!")
