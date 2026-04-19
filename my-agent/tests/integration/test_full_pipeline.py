"""Full pipeline integration tests.

Tests the complete R1→R3→R4 data flow without requiring
a running MCP subprocess (no R2 bridge process needed).
"""

import json
import os
import shutil
import tempfile
import unittest
import zipfile
from io import BytesIO
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient


# The MCP server source that will be zipped and uploaded
MCP_SERVER_SOURCE = '''
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("calculator")

@mcp.tool()
def add(a: int, b: int) -> int:
    """Add two numbers."""
    return a + b

@mcp.tool()
def multiply(x: float, y: float) -> float:
    """Multiply two numbers."""
    return x * y

@mcp.resource("config://settings")
def get_settings() -> str:
    """Get application settings."""
    return '{"debug": true}'

@mcp.prompt()
def explain(topic: str, level: str = "beginner") -> str:
    """Explain a topic at a given level."""
    return f"Explain {topic} for {level}"
'''


def _create_zip(files: dict) -> BytesIO:
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    buf.seek(0)
    return buf


def _build_client() -> TestClient:
    from nasiko.api.v1.ingest import router
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


class TestFullPipelineE2E(unittest.TestCase):
    """Test the complete upload → detect → manifest → link pipeline."""

    def test_upload_mcp_server_generates_manifest_with_all_types(self):
        """Upload zip → R1 detects MCP_SERVER → R3 generates manifest with tools + resources + prompts."""
        client = _build_client()
        zip_buf = _create_zip({"src/main.py": MCP_SERVER_SOURCE, "Dockerfile": "F", "docker-compose.yml": "V"})

        resp = client.post(
            "/ingest",
            files={"file": ("calculator.zip", zip_buf, "application/zip")},
        )

        self.assertEqual(resp.status_code, 200)
        body = resp.json()

        # R1 detection
        self.assertEqual(body["artifact_type"], "MCP_SERVER")
        self.assertEqual(body["detected_framework"], "mcp")
        self.assertEqual(body["confidence"], "HIGH")

        # R3 manifest auto-generated
        self.assertTrue(body.get("manifest_generated", False))
        manifest = body["manifest"]

        # Tools
        tool_names = [t["name"] for t in manifest["tools"]]
        self.assertIn("add", tool_names)
        self.assertIn("multiply", tool_names)
        self.assertEqual(len(manifest["tools"]), 2)

        # Resources
        self.assertEqual(len(manifest["resources"]), 1)
        self.assertEqual(manifest["resources"][0]["uri"], "config://settings")

        # Prompts
        self.assertEqual(len(manifest["prompts"]), 1)
        self.assertEqual(manifest["prompts"][0]["name"], "explain")

    def test_manifest_tool_schemas_are_correct(self):
        """Tool input_schema has correct types and required fields."""
        client = _build_client()
        zip_buf = _create_zip({"src/main.py": MCP_SERVER_SOURCE, "Dockerfile": "F", "docker-compose.yml": "V"})

        resp = client.post(
            "/ingest",
            files={"file": ("calc.zip", zip_buf, "application/zip")},
        )

        manifest = resp.json()["manifest"]
        add_tool = [t for t in manifest["tools"] if t["name"] == "add"][0]

        schema = add_tool["input_schema"]
        self.assertEqual(schema["properties"]["a"]["type"], "integer")
        self.assertEqual(schema["properties"]["b"]["type"], "integer")
        self.assertIn("a", schema["required"])
        self.assertIn("b", schema["required"])

    def test_manifest_prompt_has_optional_params(self):
        """Prompt with default parameter marks it as not required."""
        client = _build_client()
        zip_buf = _create_zip({"src/main.py": MCP_SERVER_SOURCE, "Dockerfile": "F", "docker-compose.yml": "V"})

        resp = client.post(
            "/ingest",
            files={"file": ("calc.zip", zip_buf, "application/zip")},
        )

        manifest = resp.json()["manifest"]
        explain_prompt = manifest["prompts"][0]

        required = explain_prompt["input_schema"]["required"]
        self.assertIn("topic", required)
        self.assertNotIn("level", required)

    def test_manifest_persisted_to_disk(self):
        """After upload, manifest.json exists at /tmp/nasiko/{artifact_id}/manifest.json."""
        client = _build_client()
        zip_buf = _create_zip({"src/main.py": MCP_SERVER_SOURCE, "Dockerfile": "F", "docker-compose.yml": "V"})

        resp = client.post(
            "/ingest",
            files={"file": ("calc.zip", zip_buf, "application/zip")},
        )

        artifact_id = resp.json()["artifact_id"]
        manifest_path = f"/tmp/nasiko/{artifact_id}/manifest.json"
        self.assertTrue(os.path.exists(manifest_path))

        with open(manifest_path) as f:
            disk_manifest = json.load(f)
        self.assertEqual(len(disk_manifest["tools"]), 2)

    def test_code_persisted_for_bridge(self):
        """After upload, code is persisted to /tmp/nasiko/{artifact_id}/code/."""
        client = _build_client()
        zip_buf = _create_zip({"src/main.py": MCP_SERVER_SOURCE, "Dockerfile": "F", "docker-compose.yml": "V"})

        resp = client.post(
            "/ingest",
            files={"file": ("calc.zip", zip_buf, "application/zip")},
        )

        body = resp.json()
        code_path = body.get("code_path")
        self.assertIsNotNone(code_path)
        self.assertTrue(os.path.exists(code_path))
        self.assertTrue(os.path.exists(os.path.join(code_path, "src", "main.py")))

    def test_linker_can_read_generated_manifest(self):
        """R4 linker can load the manifest that R1→R3 generated."""
        client = _build_client()
        zip_buf = _create_zip({"src/main.py": MCP_SERVER_SOURCE, "Dockerfile": "F", "docker-compose.yml": "V"})

        resp = client.post(
            "/ingest",
            files={"file": ("calc.zip", zip_buf, "application/zip")},
        )

        artifact_id = resp.json()["artifact_id"]

        # R4's linker reads manifest from disk
        from nasiko.app.utils.agent_mcp_linker import get_manifest
        manifest = get_manifest(artifact_id)
        self.assertEqual(len(manifest["tools"]), 2)

    def test_linker_rejects_before_bridge_ready(self):
        """R4 linker returns 400 when bridge.json doesn't exist (bridge not started)."""
        client = _build_client()
        zip_buf = _create_zip({"src/main.py": MCP_SERVER_SOURCE, "Dockerfile": "F", "docker-compose.yml": "V"})

        resp = client.post(
            "/ingest",
            files={"file": ("calc.zip", zip_buf, "application/zip")},
        )

        artifact_id = resp.json()["artifact_id"]

        # Linker checks bridge status — should be UNKNOWN (no bridge.json yet)
        from nasiko.app.utils.agent_mcp_linker import get_bridge_status
        status = get_bridge_status(artifact_id)
        self.assertEqual(status, "UNKNOWN")

    def test_linker_accepts_after_bridge_ready(self):
        """R4 linker succeeds after bridge.json is written with status=ready."""
        client = _build_client()
        zip_buf = _create_zip({"src/main.py": MCP_SERVER_SOURCE, "Dockerfile": "F", "docker-compose.yml": "V"})

        resp = client.post(
            "/ingest",
            files={"file": ("calc.zip", zip_buf, "application/zip")},
        )

        artifact_id = resp.json()["artifact_id"]

        # Simulate R2 writing bridge.json after successful start
        bridge_dir = f"/tmp/nasiko/{artifact_id}"
        with open(os.path.join(bridge_dir, "bridge.json"), "w") as f:
            json.dump({"status": "ready", "port": 8100}, f)

        from nasiko.app.utils.agent_mcp_linker import get_bridge_status
        status = get_bridge_status(artifact_id)
        self.assertEqual(status, "ready")

    def test_r5_tracing_span_created_on_tool_call(self):
        """R5 tracing creates proper spans when a tool call goes through the bridge."""
        from nasiko.app.utils.observability.mcp_tracing import (
            create_tool_call_span, record_tool_result, _NullSpan
        )

        # With no tracer (tracing disabled), should still work via _NullSpan
        with create_tool_call_span(
            tracer=None,
            tool_name="add",
            arguments={"a": 1, "b": 2},
            server_name="calculator",
            artifact_id="test-123",
        ) as span:
            self.assertIsInstance(span, _NullSpan)
            record_tool_result(span, {"result": 3})  # should not crash


if __name__ == "__main__":
    unittest.main()
