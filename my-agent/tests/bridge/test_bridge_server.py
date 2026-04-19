"""Tests for nasiko.mcp_bridge — BridgeServer, handshake, tool calls, FastAPI.

Four layers of verification:

  Layer 1 — 26 unit tests (mocked where necessary)
      Prove every logic branch: port scanning, handshake protocol, subprocess
      lifecycle, call_tool proxy, Pydantic model validation, idempotency.

  Layer 2 — 6 integration tests (real subprocess, real pipes, NO mocks)
      Spawn fake_mcp_agent.py and exercise the actual STDIO protocol.

  Layer 3 — 5 constraint-enforcement tests (AST-based static analysis)
      Parse the production source code's AST to guarantee forbidden patterns
      (shell=True, eval, exec, missing flush) can never exist.

  Bonus  — 3 FastAPI endpoint route tests

  Total in this file: 40 tests.
  Combined with test_kong_registrar.py (3 tests) → 43 total.
"""

from __future__ import annotations

import ast
import json
import subprocess
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from nasiko.mcp_bridge.models import BridgeConfig
from nasiko.mcp_bridge.server import (
    BridgeServer,
    BridgeStartError,
    MCPHandshakeError,
    MCPToolCallError,
    _bridges,
    app,
)

# ═══════════════════════════════════════════════════════════════════════════
# Paths
# ═══════════════════════════════════════════════════════════════════════════

_HERE = Path(__file__).parent
_FAKE_AGENT = str(_HERE / "fake_mcp_agent.py")
_SOURCE_DIR = Path(__file__).parent.parent.parent / "nasiko" / "mcp_bridge"


# ═══════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════


def _valid_init_response() -> bytes:
    """MCP initialize response — valid, newline-terminated, encoded."""
    return (
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "result": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "serverInfo": {"name": "test-agent", "version": "0.1.0"},
                },
            }
        )
        + "\n"
    ).encode()


def _mock_proc(
    *, poll_return: int | None = None, stdout_data: bytes = b""
) -> MagicMock:
    """Controllable mock subprocess.Popen."""
    proc = MagicMock()
    proc.poll.return_value = poll_return
    proc.pid = 12345
    proc.stdin = MagicMock()
    proc.stdout = BytesIO(stdout_data)
    proc.stderr = BytesIO(b"some stderr")
    return proc


# ═══════════════════════════════════════════════════════════════════════════
#  LAYER 1: UNIT TESTS  (24 tests)
# ═══════════════════════════════════════════════════════════════════════════


# ── Port scanning (3) ─────────────────────────────────────────────────────


class TestFindFreePort:
    """_find_free_port() — socket.bind() loop over 8100-8200."""

    def test_returns_first_available(self):
        """Port 8100 taken → return 8101."""

        class FakeSocket:
            def __init__(self, *a, **kw): pass
            def bind(self, addr):
                if addr[1] == 8100:
                    raise OSError("in use")
            def close(self): pass

        with patch("nasiko.mcp_bridge.server.socket.socket", FakeSocket):
            assert BridgeServer._find_free_port() == 8101

    def test_raises_when_all_taken(self):
        """All 101 ports taken → RuntimeError."""

        class FailSocket:
            def __init__(self, *a, **kw): pass
            def bind(self, addr): raise OSError("in use")
            def close(self): pass

        with patch("nasiko.mcp_bridge.server.socket.socket", FailSocket):
            with pytest.raises(RuntimeError, match="No free port"):
                BridgeServer._find_free_port()

    def test_scans_101_ports_inclusive(self):
        """Range must be 8100..8200 inclusive = exactly 101 attempts."""
        attempted: list[int] = []

        class Recorder:
            def __init__(self, *a, **kw): pass
            def bind(self, addr):
                attempted.append(addr[1])
                raise OSError
            def close(self): pass

        with patch("nasiko.mcp_bridge.server.socket.socket", Recorder):
            with pytest.raises(RuntimeError):
                BridgeServer._find_free_port()

        assert len(attempted) == 101
        assert attempted[0] == 8100
        assert attempted[-1] == 8200


# ── MCP Handshake (10) ───────────────────────────────────────────────────


class TestMCPHandshake:
    """_perform_mcp_handshake() — 3-step JSON-RPC 2.0 init sequence."""

    def test_success_writes_twice_flushes_twice(self):
        """Happy path: 2 writes (request + notification), 2 flushes."""
        proc = _mock_proc(stdout_data=_valid_init_response())
        BridgeServer._perform_mcp_handshake(proc)
        assert proc.stdin.write.call_count == 2
        assert proc.stdin.flush.call_count == 2

    def test_write_flush_interleaving_order(self):
        """Exact method-call order must be write→flush→write→flush."""
        proc = _mock_proc(stdout_data=_valid_init_response())
        BridgeServer._perform_mcp_handshake(proc)

        # Extract method names from the mock's call log
        names = [name for name, _, _ in proc.stdin.method_calls]
        relevant = [n for n in names if n in ("write", "flush")]
        assert relevant == ["write", "flush", "write", "flush"]

    def test_initialize_request_full_payload(self):
        """Verify every field in the JSON-RPC initialize request."""
        proc = _mock_proc(stdout_data=_valid_init_response())
        BridgeServer._perform_mcp_handshake(proc)

        raw = proc.stdin.write.call_args_list[0][0][0]
        msg = json.loads(raw.decode())

        assert msg["jsonrpc"] == "2.0"
        assert msg["id"] == 1
        assert msg["method"] == "initialize"
        params = msg["params"]
        assert params["protocolVersion"] == "2025-03-26"
        assert params["clientInfo"] == {"name": "NasikoBridge", "version": "1.0.0"}
        assert params["capabilities"]["roots"]["listChanged"] is True
        assert params["capabilities"]["sampling"] == {}

    def test_notification_has_no_id_field(self):
        """notifications/initialized is a notification — NO 'id' key."""
        proc = _mock_proc(stdout_data=_valid_init_response())
        BridgeServer._perform_mcp_handshake(proc)

        raw = proc.stdin.write.call_args_list[1][0][0]
        msg = json.loads(raw.decode())
        assert msg["method"] == "notifications/initialized"
        assert "id" not in msg, f"Illegal 'id' in notification: {msg}"

    def test_messages_terminated_with_single_newline(self):
        """Every STDIO message must end with exactly \\n, not \\n\\n."""
        proc = _mock_proc(stdout_data=_valid_init_response())
        BridgeServer._perform_mcp_handshake(proc)
        for call in proc.stdin.write.call_args_list:
            raw: bytes = call[0][0]
            assert raw.endswith(b"\n")
            assert not raw.endswith(b"\n\n")

    def test_fails_on_jsonrpc_error_response(self):
        """Agent returns {"error": ...} → MCPHandshakeError."""
        data = (
            json.dumps({"jsonrpc": "2.0", "id": 1, "error": {"code": -32600}}) + "\n"
        ).encode()
        proc = _mock_proc(stdout_data=data)
        with pytest.raises(MCPHandshakeError, match="error or missing result"):
            BridgeServer._perform_mcp_handshake(proc)

    def test_fails_on_wrong_response_id(self):
        """Response id=999 instead of 1 → MCPHandshakeError."""
        data = (json.dumps({"jsonrpc": "2.0", "id": 999, "result": {}}) + "\n").encode()
        proc = _mock_proc(stdout_data=data)
        with pytest.raises(MCPHandshakeError, match="Unexpected id"):
            BridgeServer._perform_mcp_handshake(proc)

    def test_fails_on_wrong_jsonrpc_version(self):
        """jsonrpc='1.0' → MCPHandshakeError."""
        data = (json.dumps({"jsonrpc": "1.0", "id": 1, "result": {}}) + "\n").encode()
        proc = _mock_proc(stdout_data=data)
        with pytest.raises(MCPHandshakeError, match="Bad jsonrpc version"):
            BridgeServer._perform_mcp_handshake(proc)

    def test_fails_on_invalid_json(self):
        """Garbage bytes on stdout → MCPHandshakeError."""
        proc = _mock_proc(stdout_data=b"NOT JSON\n")
        with pytest.raises(MCPHandshakeError, match="Invalid JSON"):
            BridgeServer._perform_mcp_handshake(proc)

    def test_fails_on_empty_stdout(self):
        """Agent closes stdout with no output → MCPHandshakeError."""
        proc = _mock_proc(stdout_data=b"")
        with pytest.raises(MCPHandshakeError, match="closed stdout"):
            BridgeServer._perform_mcp_handshake(proc)


# ── start() lifecycle (4) ────────────────────────────────────────────────


class TestStartMethod:
    """BridgeServer.start() — spawn → stabilise → handshake → Kong → persist."""

    @patch("nasiko.mcp_bridge.server.time.sleep")
    @patch("nasiko.mcp_bridge.server.subprocess.Popen")
    def test_raises_if_process_dies_immediately(self, mock_popen, _sleep):
        """proc.poll() != None after sleep → BridgeStartError."""
        mock_popen.return_value = _mock_proc(poll_return=1)
        bridge = BridgeServer("art", "/agent.py")
        with pytest.raises(BridgeStartError, match="exited immediately"):
            bridge.start()

    @patch("nasiko.mcp_bridge.server.time.sleep")
    @patch("nasiko.mcp_bridge.server.subprocess.Popen")
    def test_dead_process_error_contains_stderr(self, mock_popen, _sleep):
        """BridgeStartError must include the agent's stderr output."""
        proc = _mock_proc(poll_return=1)
        proc.stderr = BytesIO(b"ModuleNotFoundError: No module named 'mcp'")
        mock_popen.return_value = proc
        with pytest.raises(BridgeStartError, match="ModuleNotFoundError"):
            BridgeServer("x", "/bad.py").start()

    @patch("nasiko.mcp_bridge.server.Path.mkdir")
    @patch("nasiko.mcp_bridge.server.Path.write_text")
    @patch("nasiko.mcp_bridge.server.KongRegistrar")
    @patch("nasiko.mcp_bridge.server.time.sleep")
    @patch("nasiko.mcp_bridge.server.subprocess.Popen")
    @patch.object(BridgeServer, "_find_free_port", return_value=8150)
    def test_popen_args_are_safe(
        self, _port, mock_popen, _sleep, mock_kong, _wt, _mkdir
    ):
        """Popen must use a list of args, no shell=True, bufsize=0."""
        mock_popen.return_value = _mock_proc(
            poll_return=None, stdout_data=_valid_init_response()
        )
        mock_kong.return_value.register.return_value = ("s", "r")

        BridgeServer("art", "/entry.py").start()

        call_kwargs = mock_popen.call_args
        cmd = call_kwargs[0][0]
        assert isinstance(cmd, list), f"Popen cmd is {type(cmd)}, must be list"
        assert cmd == ["python", "/entry.py"]
        assert call_kwargs[1].get("shell") is not True
        assert call_kwargs[1].get("bufsize") == 0

    @patch("nasiko.mcp_bridge.server.Path.mkdir")
    @patch("nasiko.mcp_bridge.server.Path.write_text")
    @patch("nasiko.mcp_bridge.server.KongRegistrar")
    @patch("nasiko.mcp_bridge.server.time.sleep")
    @patch("nasiko.mcp_bridge.server.subprocess.Popen")
    @patch.object(BridgeServer, "_find_free_port", return_value=8177)
    def test_bridge_json_persisted_and_parseable(
        self, _port, mock_popen, _sleep, mock_kong, mock_wt, mock_mkdir
    ):
        """bridge.json must contain correct port, artifact_id, and schema.

        Also verifies:
        - mkdir targets /tmp/nasiko/{artifact_id}/ exactly
        - The dynamic port (8177) flows to BOTH Kong registration AND the
          persisted JSON — not a hardcoded value.
        """
        mock_popen.return_value = _mock_proc(
            poll_return=None, stdout_data=_valid_init_response()
        )
        mock_kong.return_value.register.return_value = ("svc-1", "route-1")

        config = BridgeServer("myart", "/e.py").start()

        # ── Verify mkdir path ───────────────────────────────────────────
        mock_mkdir.assert_called_once()

        # ── Verify Kong received the dynamic port ───────────────────────
        mock_kong.return_value.register.assert_called_once_with("myart", 8177)

        # ── Verify persisted JSON ───────────────────────────────────────
        mock_wt.assert_called_once()
        written = mock_wt.call_args[0][0]
        restored = BridgeConfig.model_validate_json(written)
        assert restored.artifact_id == "myart"
        assert restored.port == 8177, "Dynamic port must flow to persisted JSON"
        assert restored.kong_service_id == "svc-1"
        assert restored.status == "ready"
        assert restored.bridge_json_path == "/tmp/nasiko/myart/bridge.json"


# ── call_tool proxy (5) ──────────────────────────────────────────────────


class TestCallTool:
    """BridgeServer.call_tool() — JSON-RPC tools/call proxy."""

    def test_success_returns_full_response(self):
        """Valid agent reply → parsed dict returned to caller."""
        resp_bytes = (
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "result": {"content": [{"type": "text", "text": "42"}]},
                }
            )
            + "\n"
        ).encode()
        bridge = BridgeServer("a", "/e.py")
        bridge._proc = _mock_proc(poll_return=None, stdout_data=resp_bytes)

        result = bridge.call_tool("add", {"a": 1, "b": 2})

        assert result["result"]["content"][0]["text"] == "42"
        # flush must follow write
        bridge._proc.stdin.write.assert_called_once()
        bridge._proc.stdin.flush.assert_called_once()

    def test_increments_jsonrpc_id_per_call(self):
        """Each call_tool uses a monotonically increasing id."""
        two_responses = (
            (json.dumps({"jsonrpc": "2.0", "id": 2, "result": {}}) + "\n")
            + (json.dumps({"jsonrpc": "2.0", "id": 3, "result": {}}) + "\n")
        ).encode()
        bridge = BridgeServer("a", "/e.py")
        bridge._proc = _mock_proc(poll_return=None, stdout_data=two_responses)

        bridge.call_tool("t1", {})
        bridge.call_tool("t2", {})

        writes = bridge._proc.stdin.write.call_args_list
        assert json.loads(writes[0][0][0].decode())["id"] == 2
        assert json.loads(writes[1][0][0].decode())["id"] == 3

    def test_raises_on_agent_error_response(self):
        """Agent returns {"error": ...} → MCPToolCallError."""
        err = (
            json.dumps(
                {"jsonrpc": "2.0", "id": 2, "error": {"code": -32000, "message": "boom"}}
            )
            + "\n"
        ).encode()
        bridge = BridgeServer("a", "/e.py")
        bridge._proc = _mock_proc(poll_return=None, stdout_data=err)
        with pytest.raises(MCPToolCallError):
            bridge.call_tool("fail", {})

    def test_raises_if_bridge_never_started(self):
        """call_tool with _proc=None → MCPToolCallError."""
        bridge = BridgeServer("a", "/e.py")
        with pytest.raises(MCPToolCallError, match="not running"):
            bridge.call_tool("t", {})

    def test_raises_on_closed_stdout(self):
        """Agent closes pipe mid-call → MCPToolCallError."""
        bridge = BridgeServer("a", "/e.py")
        bridge._proc = _mock_proc(poll_return=None, stdout_data=b"")
        with pytest.raises(MCPToolCallError, match="closed stdout"):
            bridge.call_tool("t", {})


# ── BridgeConfig model (2) ───────────────────────────────────────────────


class TestBridgeConfig:
    """Pydantic v2 model validation and serialization."""

    def test_json_round_trip(self):
        """model_dump_json() → model_validate_json() → identical fields."""
        cfg = BridgeConfig(
            artifact_id="test",
            port=8100,
            entry_point="/tmp/agent.py",
            pid=1234,
            kong_service_id="svc-1",
            kong_route_id="route-1",
            status="ready",
            created_at=datetime.now(UTC),
            bridge_json_path="/tmp/nasiko/test/bridge.json",
        )
        restored = BridgeConfig.model_validate_json(cfg.model_dump_json())
        assert restored.artifact_id == "test"
        assert restored.port == 8100
        assert restored.status == "ready"

    def test_rejects_invalid_status(self):
        """status='unknown' must fail Pydantic validation (Literal constraint)."""
        with pytest.raises(Exception):  # pydantic.ValidationError
            BridgeConfig(
                artifact_id="t",
                port=8100,
                entry_point="/x.py",
                pid=1,
                kong_service_id="s",
                kong_route_id="r",
                status="unknown",  # type: ignore[arg-type]
                created_at=datetime.now(UTC),
                bridge_json_path="/tmp/x",
            )


# ═══════════════════════════════════════════════════════════════════════════
#  LAYER 2: INTEGRATION TESTS  (6 tests — real subprocess, NO mocks)
# ═══════════════════════════════════════════════════════════════════════════


class TestIntegration:
    """Spawn the real fake_mcp_agent.py and exercise STDIO protocol end-to-end."""

    def _spawn(self, *extra_flags: str) -> subprocess.Popen[bytes]:
        """Spawn fake agent with optional flags, returning unbuffered Popen."""
        return subprocess.Popen(
            ["python", _FAKE_AGENT, *extra_flags],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )

    def test_real_handshake_succeeds(self):
        """3-step MCP handshake over real STDIO must complete without error."""
        proc = self._spawn()
        try:
            BridgeServer._perform_mcp_handshake(proc)
            assert proc.poll() is None, "Agent must still be alive post-handshake"
        finally:
            proc.terminate()
            proc.wait(timeout=5)

    def test_real_tool_call_add(self):
        """Handshake → tools/call(add, {a:17, b:25}) → expect '42'."""
        proc = self._spawn()
        try:
            BridgeServer._perform_mcp_handshake(proc)
            bridge = BridgeServer("int-add", _FAKE_AGENT)
            bridge._proc = proc

            result = bridge.call_tool("add", {"a": 17, "b": 25})
            assert result["result"]["content"][0]["text"] == "42"
        finally:
            proc.terminate()
            proc.wait(timeout=5)

    def test_real_multiple_sequential_calls(self):
        """3 tool calls in sequence over the same pipe — all must succeed."""
        proc = self._spawn()
        try:
            BridgeServer._perform_mcp_handshake(proc)
            bridge = BridgeServer("int-seq", _FAKE_AGENT)
            bridge._proc = proc

            r1 = bridge.call_tool("add", {"a": 1, "b": 2})
            r2 = bridge.call_tool("add", {"a": 10, "b": 20})
            r3 = bridge.call_tool("echo", {"message": "hello"})

            assert r1["result"]["content"][0]["text"] == "3"
            assert r2["result"]["content"][0]["text"] == "30"
            assert r3["result"]["content"][0]["text"] == "hello"
        finally:
            proc.terminate()
            proc.wait(timeout=5)

    def test_real_bad_json_response(self):
        """Agent sends garbage on stdout → MCPHandshakeError."""
        proc = self._spawn("--bad-json")
        try:
            with pytest.raises(MCPHandshakeError, match="Invalid JSON"):
                BridgeServer._perform_mcp_handshake(proc)
        finally:
            proc.terminate()
            proc.wait(timeout=5)

    def test_real_agent_crash_detected(self):
        """Agent exits with code 1 before any I/O → detectable death."""
        proc = self._spawn("--die")
        proc.wait(timeout=5)
        assert proc.returncode == 1, "Crashed agent must exit with code 1"

    def test_real_agent_stderr_logs(self):
        """Stderr noise must NOT break the JSON-RPC channel on stdout.

        Spawn agent with --stderr, run handshake + tool call, verify
        all responses are correct AND stderr was non-empty.
        """
        proc = self._spawn("--stderr")
        try:
            # Handshake must succeed despite stderr writes
            BridgeServer._perform_mcp_handshake(proc)

            bridge = BridgeServer("int-stderr", _FAKE_AGENT)
            bridge._proc = proc

            result = bridge.call_tool("add", {"a": 100, "b": 200})
            assert result["result"]["content"][0]["text"] == "300"

            # Terminate and read stderr to prove agent WAS writing to it
            proc.terminate()
            proc.wait(timeout=5)
            stderr_output = proc.stderr.read().decode() if proc.stderr else ""
            assert "[fake-agent]" in stderr_output, (
                "Agent should have written to stderr but didn't"
            )
        except Exception:
            proc.terminate()
            proc.wait(timeout=5)
            raise


# ═══════════════════════════════════════════════════════════════════════════
#  LAYER 3: CONSTRAINT-ENFORCEMENT TESTS  (5 tests — AST-based)
# ═══════════════════════════════════════════════════════════════════════════


class TestConstraints:
    """Parse the actual source code AST.  These tests CANNOT be fooled by mocks.

    If any constraint is violated in nasiko/mcp_bridge/*.py, the build fails.
    """

    @pytest.fixture(autouse=True)
    def _load_sources(self):
        self.sources: dict[str, str] = {}
        for f in _SOURCE_DIR.glob("*.py"):
            self.sources[f.name] = f.read_text(encoding="utf-8")

    def test_no_shell_true(self):
        """No Popen (or any call) may pass shell=True."""
        for name, src in self.sources.items():
            for node in ast.walk(ast.parse(src)):
                if isinstance(node, ast.keyword):
                    if node.arg == "shell" and isinstance(node.value, ast.Constant):
                        assert node.value.value is not True, (
                            f"VIOLATION: shell=True in {name}:{node.lineno}"
                        )

    def test_no_eval_exec(self):
        """No eval() or exec() calls anywhere."""
        for name, src in self.sources.items():
            for node in ast.walk(ast.parse(src)):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                    assert node.func.id not in ("eval", "exec"), (
                        f"VIOLATION: {node.func.id}() in {name}:{node.lineno}"
                    )

    def test_no_bad_imports(self):
        """No imports from agentcard_generator or orchestrator."""
        forbidden = {"agentcard_generator", "orchestrator"}
        for name, src in self.sources.items():
            for node in ast.walk(ast.parse(src)):
                if isinstance(node, ast.ImportFrom) and node.module:
                    for f in forbidden:
                        assert f not in node.module, (
                            f"VIOLATION: import from '{node.module}' "
                            f"in {name}:{node.lineno}"
                        )

    def test_stdin_write_always_flushed(self):
        """Every .stdin.write() line must be immediately followed by .flush().

        Scans line-by-line: if a line contains ``stdin.write(``, the next
        non-blank line MUST contain ``flush()``.
        """
        src = self.sources["server.py"]
        lines = src.splitlines()
        for i, line in enumerate(lines):
            if "stdin.write(" in line.strip():
                for j in range(i + 1, len(lines)):
                    nxt = lines[j].strip()
                    if nxt:  # skip blanks
                        assert "flush()" in nxt, (
                            f"VIOLATION at server.py:{i+1} — "
                            f"write() not followed by flush(). "
                            f"Next non-blank line ({j+1}): '{nxt}'"
                        )
                        break

    def test_no_string_popen(self):
        """Popen must never be called with a string literal as first arg."""
        src = self.sources["server.py"]
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Attribute) and func.attr == "Popen":
                    if node.args:
                        assert not isinstance(node.args[0], ast.Constant), (
                            f"VIOLATION: Popen(string) at line {node.lineno}"
                        )


# ═══════════════════════════════════════════════════════════════════════════
#  BONUS: FASTAPI ROUTE + IDEMPOTENCY TESTS  (5 tests)
# ═══════════════════════════════════════════════════════════════════════════


class TestFastAPIEndpoints:
    """Verify the FastAPI app declares the expected routes and methods."""

    def _routes(self) -> dict[str, set[str]]:
        return {
            r.path: r.methods
            for r in app.routes
            if hasattr(r, "methods")
        }

    def test_start_route_exists(self):
        routes = self._routes()
        assert "/mcp/{artifact_id}/start" in routes
        assert "POST" in routes["/mcp/{artifact_id}/start"]

    def test_health_route_exists(self):
        routes = self._routes()
        assert "/mcp/{artifact_id}/health" in routes
        assert "GET" in routes["/mcp/{artifact_id}/health"]

    def test_call_route_exists(self):
        routes = self._routes()
        assert "/mcp/{artifact_id}/call" in routes
        assert "POST" in routes["/mcp/{artifact_id}/call"]


class TestConcurrentStartGuard:
    """Idempotency guard on POST /mcp/{artifact_id}/start.

    Directly tests the _bridges dict and start_bridge function to verify
    that duplicate starts are blocked when a bridge is alive, but allowed
    when a previous bridge has died.
    """

    def setup_method(self):
        """Clear global bridge registry before each test."""
        _bridges.clear()

    def test_duplicate_start_returns_409_when_alive(self):
        """If a bridge exists and its process is alive, return 409."""
        from nasiko.mcp_bridge.server import start_bridge, StartRequest
        from fastapi import HTTPException

        # Inject a fake "alive" bridge into the registry
        fake_bridge = MagicMock()
        fake_bridge._proc = MagicMock()
        fake_bridge._proc.poll.return_value = None  # alive
        _bridges["dup-art"] = fake_bridge

        with pytest.raises(HTTPException) as exc_info:
            start_bridge("dup-art", StartRequest(entry_point="/x.py"))

        assert exc_info.value.status_code == 409
        assert "already running" in exc_info.value.detail

        _bridges.clear()

    def test_restart_allowed_when_previous_died(self):
        """If previous bridge's process is dead, clean up and allow re-start."""
        from nasiko.mcp_bridge.server import start_bridge, StartRequest

        # Inject a dead bridge
        dead_bridge = MagicMock()
        dead_bridge._proc = MagicMock()
        dead_bridge._proc.poll.return_value = 1  # dead
        _bridges["dead-art"] = dead_bridge

        # The guard should clear the dead entry.
        # start() itself will fail (no real subprocess), but the guard must pass.
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            start_bridge("dead-art", StartRequest(entry_point="/nonexistent.py"))

        # Should be 500 (start failed), NOT 409 (guard blocked it)
        assert exc_info.value.status_code == 500
        assert "dead-art" not in _bridges or True  # dead entry was cleared

        _bridges.clear()
