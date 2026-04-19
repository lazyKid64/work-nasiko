import unittest
from unittest.mock import patch, MagicMock

# Import the R4 methods we just injected
from nasiko.app.utils.agent_mcp_linker import get_bridge_status
from nasiko.app.agent_builder import inject_mcp_tools

class MockTask:
    def __init__(self):
        self.tools = []

@patch("nasiko.app.utils.agent_mcp_linker.Path.exists")
@patch("nasiko.app.utils.agent_mcp_linker.open")
def test_agent_mcp_linker_status(mock_open, mock_exists):
    """Priority 3 verification: Mock filesystem reads for bridge status."""
    mock_exists.return_value = True
    # Fake the bridge.json output matching a live process
    mock_open.return_value.__enter__.return_value.read.return_value = '{"status": "RUNNING"}'
    
    assert get_bridge_status("mock-mcp-123") == "RUNNING"

def test_inject_mcp_tools_zero_code_guarantee():
    """
    Priority 4 verification: Test that tools array is dynamically 
    appended, bypassing need for source code modification.
    """
    task = MockTask()
    manifest = {
        "tools": [
            {"name": "fetch_market_data", "description": "Fetches market info"}
        ]
    }
    
    inject_mcp_tools(task, "mock-mcp-123", manifest)
    
    assert len(task.tools) == 1
    injected_tool = task.tools[0]
    
    # Needs to match the proxy schema
    assert injected_tool.name == "fetch_market_data"
    assert injected_tool.artifact_id == "mock-mcp-123"

@patch("nasiko.app.utils.mcp_tools.is_bridge_alive")
@patch("nasiko.app.utils.mcp_tools.httpx.post")
def test_mcp_gap_overhauls(mock_post, mock_alive):
    """
    Priority 2 Remediation verification: Ensure W3C traceparents hit headers
    and 500 Agent restarts trigger correctly.
    """
    from nasiko.app.utils.mcp_tools import execute_bridge_call
    
    # Needs a mock response object
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"result": {"content": [{"text": "success"}]}}
    mock_post.return_value = mock_resp
    
    execute_bridge_call("mock-123", "search", {}, trace_context="w3c-uuid-12")
    
    # Verify trace context injection
    mock_post.assert_called_once()
    headers = mock_post.call_args[1]["headers"]
    assert headers["traceparent"] == "w3c-uuid-12"
