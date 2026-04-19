"""Tests for the MCP manifest parser and generator.

Covers the nasiko.app.utils.mcp_manifest_generator package:
  - parser.py: tool/resource/prompt decorator extraction (AST-based)
  - generator.py: manifest file generation and loading
"""

import json
import os
import tempfile
import unittest

from nasiko.app.utils.mcp_manifest_generator.parser import (
    parse_tools,
    parse_all,
)
from nasiko.app.utils.mcp_manifest_generator.generator import (
    generate_manifest,
    load_manifest,
)


# ═══════════════════════════════════════════════════════════════════════════
#  Parser Tests
# ═══════════════════════════════════════════════════════════════════════════

class TestParseTools(unittest.TestCase):
    """parse_tools() — extract @<host>.tool() decorated functions."""

    def test_basic_mcp_tool(self):
        src = '''
from mcp.server.fastmcp import FastMCP
mcp = FastMCP("test")

@mcp.tool()
def add(a: int, b: int) -> int:
    """Add two numbers."""
    return a + b
'''
        tools = parse_tools(src)
        self.assertEqual(len(tools), 1)
        self.assertEqual(tools[0]["name"], "add")
        self.assertEqual(tools[0]["docstring"], "Add two numbers.")

    def test_tool_name_override(self):
        src = '''
@mcp.tool(name="custom_name")
def internal_func(x: str) -> str:
    """Does something."""
    return x
'''
        tools = parse_tools(src)
        self.assertEqual(tools[0]["name"], "custom_name")

    def test_server_host_variant(self):
        src = '''
@server.tool()
def echo(msg: str) -> str:
    return msg
'''
        tools = parse_tools(src)
        self.assertEqual(len(tools), 1)

    def test_bare_decorator_no_parens(self):
        src = '''
@app.tool
def bare(x: int) -> int:
    return x
'''
        tools = parse_tools(src)
        self.assertEqual(len(tools), 1)

    def test_empty_source_returns_empty(self):
        self.assertEqual(parse_tools(""), [])
        self.assertEqual(parse_tools("   "), [])

    def test_invalid_python_raises(self):
        with self.assertRaises(ValueError):
            parse_tools("def (((broken")

    def test_parameter_types_mapped(self):
        src = '''
@mcp.tool()
def typed(a: int, b: float, c: str, d: bool):
    pass
'''
        tools = parse_tools(src)
        params = {p["name"]: p["json_schema"]["type"] for p in tools[0]["parameters"]}
        self.assertEqual(params["a"], "integer")
        self.assertEqual(params["b"], "number")
        self.assertEqual(params["c"], "string")
        self.assertEqual(params["d"], "boolean")

    def test_optional_params_not_required(self):
        src = '''
@mcp.tool()
def optional_tool(required: str, optional: str = "default"):
    pass
'''
        tools = parse_tools(src)
        req = [p for p in tools[0]["parameters"] if p["required"]]
        opt = [p for p in tools[0]["parameters"] if not p["required"]]
        self.assertEqual(len(req), 1)
        self.assertEqual(req[0]["name"], "required")
        self.assertEqual(len(opt), 1)
        self.assertEqual(opt[0]["name"], "optional")


class TestParseAll(unittest.TestCase):
    """parse_all() — extract tools, resources, and prompts."""

    FULL_SOURCE = '''
from mcp.server.fastmcp import FastMCP
mcp = FastMCP("test")

@mcp.tool()
def add(a: int, b: int) -> int:
    """Add two numbers."""
    return a + b

@mcp.resource("config://settings")
def get_settings() -> str:
    """Get settings."""
    return "{}"

@mcp.prompt()
def helper(problem: str) -> str:
    """Help with a problem."""
    return problem
'''

    def test_extracts_all_three_types(self):
        tools, resources, prompts = parse_all(self.FULL_SOURCE)
        self.assertEqual(len(tools), 1)
        self.assertEqual(len(resources), 1)
        self.assertEqual(len(prompts), 1)

    def test_resource_uri_captured(self):
        _, resources, _ = parse_all(self.FULL_SOURCE)
        self.assertEqual(resources[0]["uri"], "config://settings")

    def test_prompt_name_captured(self):
        _, _, prompts = parse_all(self.FULL_SOURCE)
        self.assertEqual(prompts[0]["name"], "helper")


# ═══════════════════════════════════════════════════════════════════════════
#  Generator Tests
# ═══════════════════════════════════════════════════════════════════════════

class TestGenerateManifest(unittest.TestCase):
    """generate_manifest() — end-to-end manifest generation."""

    def setUp(self):
        """Create a temp source file for each test."""
        self.tmpdir = tempfile.mkdtemp()
        self.source_file = os.path.join(self.tmpdir, "main.py")
        with open(self.source_file, "w") as f:
            f.write('''
from mcp.server.fastmcp import FastMCP
mcp = FastMCP("test")

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
    """Get settings."""
    return "{}"

@mcp.prompt()
def helper(problem: str, show_steps: bool = True) -> str:
    """Help with a problem."""
    return problem
''')
        # Set source root to allow the temp dir
        os.environ["NASIKO_SOURCE_ROOT"] = self.tmpdir

    def tearDown(self):
        os.environ.pop("NASIKO_SOURCE_ROOT", None)

    def test_generates_manifest_with_correct_counts(self):
        manifest = generate_manifest("test-123", self.source_file)
        self.assertEqual(len(manifest["tools"]), 2)
        self.assertEqual(len(manifest["resources"]), 1)
        self.assertEqual(len(manifest["prompts"]), 1)

    def test_manifest_persisted_to_disk(self):
        manifest = generate_manifest("test-disk", self.source_file)
        path = "/tmp/nasiko/test-disk/manifest.json"
        self.assertTrue(os.path.exists(path))
        with open(path) as f:
            loaded = json.load(f)
        self.assertEqual(loaded["artifact_id"], "test-disk")

    def test_tool_input_schema_correct(self):
        manifest = generate_manifest("test-schema", self.source_file)
        add_tool = [t for t in manifest["tools"] if t["name"] == "add"][0]
        schema = add_tool["input_schema"]
        self.assertEqual(schema["properties"]["a"]["type"], "integer")
        self.assertIn("a", schema["required"])
        self.assertIn("b", schema["required"])

    def test_empty_artifact_id_raises(self):
        with self.assertRaises(ValueError):
            generate_manifest("", self.source_file)

    def test_invalid_artifact_id_raises(self):
        with self.assertRaises(ValueError):
            generate_manifest("../../etc", self.source_file)

    def test_load_manifest_round_trip(self):
        generate_manifest("test-load", self.source_file)
        loaded = load_manifest("test-load")
        self.assertEqual(loaded["artifact_id"], "test-load")
        self.assertEqual(len(loaded["tools"]), 2)

    def test_load_manifest_not_found(self):
        with self.assertRaises(FileNotFoundError):
            load_manifest("nonexistent-id")


if __name__ == "__main__":
    unittest.main()
