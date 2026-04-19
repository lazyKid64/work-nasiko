import sys
import os

# Ensure the root of stack-up is in the path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

from tests.orchestration.test_mcp_linker import test_agent_mcp_linker_status, test_inject_mcp_tools_zero_code_guarantee, test_mcp_gap_overhauls

if __name__ == "__main__":
    try:
        print("Running test_agent_mcp_linker_status...")
        test_agent_mcp_linker_status()
        print("[OK]")
        
        print("Running test_inject_mcp_tools_zero_code_guarantee...")
        test_inject_mcp_tools_zero_code_guarantee()
        print("[OK]")
        
        print("Running test_mcp_gap_overhauls...")
        test_mcp_gap_overhauls()
        print("[OK]")
        
        print("\nAll orchestration validation tests complete successfully.")
    except Exception as e:
        print(f"Test failed: {e}")
        sys.exit(1)
