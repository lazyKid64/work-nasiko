"""Tests for nasiko.app.utils.observability.mcp_tracing — MCP tracing utilities.

11 tests across 5 classes:

  TestNullSpan             (3 tests) — _NullSpan no-op methods
  TestCreateToolCallSpan   (3 tests) — span creation + null safety
  TestRecordToolResult     (2 tests) — result recording
  TestRecordToolError      (2 tests) — error recording
  TestBootstrapMCPTracing  (1 test)  — kill-switch behaviour
"""

import os
import json
import unittest
from unittest.mock import MagicMock, patch, PropertyMock
from contextlib import contextmanager

from nasiko.app.utils.observability.mcp_tracing import (
    _NullSpan,
    create_tool_call_span,
    record_tool_result,
    record_tool_error,
    bootstrap_mcp_tracing,
)
from opentelemetry.trace import StatusCode


# ═══════════════════════════════════════════════════════════════════════════
#  CLASS 1: TestNullSpan (3 tests)
# ═══════════════════════════════════════════════════════════════════════════


class TestNullSpan(unittest.TestCase):
    """_NullSpan must silently accept all method calls without raising."""

    def test_null_span_set_attribute_is_noop(self):
        """_NullSpan().set_attribute('key', 'val') doesn't raise."""
        span = _NullSpan()
        span.set_attribute("key", "val")  # must not raise

    def test_null_span_set_status_is_noop(self):
        """_NullSpan().set_status('OK') doesn't raise."""
        span = _NullSpan()
        span.set_status("OK")  # must not raise

    def test_null_span_record_exception_is_noop(self):
        """_NullSpan().record_exception(Exception('x')) doesn't raise."""
        span = _NullSpan()
        span.record_exception(Exception("x"))  # must not raise


# ═══════════════════════════════════════════════════════════════════════════
#  CLASS 2: TestCreateToolCallSpan (3 tests)
# ═══════════════════════════════════════════════════════════════════════════


class TestCreateToolCallSpan(unittest.TestCase):
    """create_tool_call_span() — span creation, null safety, attribute setting."""

    def test_yields_null_span_when_tracer_is_none(self):
        """tracer=None → yields _NullSpan, doesn't crash."""
        with create_tool_call_span(None, "tool", {}, "server", "art1") as span:
            self.assertIsInstance(span, _NullSpan)

    def _make_mock_tracer(self):
        """Helper: build a mock tracer whose start_as_current_span returns a
        context manager yielding a MagicMock span."""
        mock_span = MagicMock()
        mock_tracer = MagicMock()

        @contextmanager
        def _fake_start_as_current_span(**kwargs):
            yield mock_span

        mock_tracer.start_as_current_span = MagicMock(side_effect=_fake_start_as_current_span)
        return mock_tracer, mock_span

    def test_yields_real_span_when_tracer_provided(self):
        """When a real tracer is given, set_attribute is called with all 5 MCP keys."""
        mock_tracer, mock_span = self._make_mock_tracer()

        with create_tool_call_span(
            tracer=mock_tracer,
            tool_name="my_tool",
            arguments={"x": 1},
            server_name="test-server",
            artifact_id="artifact-123",
        ) as span:
            pass

        # Verify all 5 MCP attributes were set
        attr_keys = [call[0][0] for call in mock_span.set_attribute.call_args_list]
        self.assertIn("mcp.tool.name", attr_keys)
        self.assertIn("mcp.tool.arguments", attr_keys)
        self.assertIn("mcp.server.name", attr_keys)
        self.assertIn("mcp.server.id", attr_keys)
        self.assertIn("mcp.transport", attr_keys)

    def test_span_attributes_have_correct_values(self):
        """Verify exact values of all 5 MCP span attributes."""
        mock_tracer, mock_span = self._make_mock_tracer()

        with create_tool_call_span(
            tracer=mock_tracer,
            tool_name="my_tool",
            arguments={"x": 1},
            server_name="test-server",
            artifact_id="artifact-123",
        ) as span:
            pass

        # Build a dict of attribute name → value from the mock calls
        attrs = {
            call[0][0]: call[0][1]
            for call in mock_span.set_attribute.call_args_list
        }
        self.assertEqual(attrs["mcp.tool.name"], "my_tool")
        self.assertEqual(attrs["mcp.tool.arguments"], json.dumps({"x": 1}))
        self.assertEqual(attrs["mcp.server.name"], "test-server")
        self.assertEqual(attrs["mcp.server.id"], "artifact-123")
        self.assertEqual(attrs["mcp.transport"], "stdio")


# ═══════════════════════════════════════════════════════════════════════════
#  CLASS 3: TestRecordToolResult (2 tests)
# ═══════════════════════════════════════════════════════════════════════════


class TestRecordToolResult(unittest.TestCase):
    """record_tool_result() — sets result attribute and OK status."""

    def test_record_result_with_none_span_is_noop(self):
        """record_tool_result(None, ...) doesn't raise."""
        record_tool_result(None, {"data": 1})  # must not raise

    def test_record_result_sets_attribute_and_status(self):
        """Verify mcp.tool.result attribute and StatusCode.OK are set."""
        mock_span = MagicMock()
        record_tool_result(mock_span, {"answer": 42})

        mock_span.set_attribute.assert_called_with(
            "mcp.tool.result", json.dumps({"answer": 42})
        )
        mock_span.set_status.assert_called_with(StatusCode.OK)


# ═══════════════════════════════════════════════════════════════════════════
#  CLASS 4: TestRecordToolError (2 tests)
# ═══════════════════════════════════════════════════════════════════════════


class TestRecordToolError(unittest.TestCase):
    """record_tool_error() — sets ERROR status and records exception."""

    def test_record_error_with_none_span_is_noop(self):
        """record_tool_error(None, ...) doesn't raise."""
        record_tool_error(None, Exception("fail"))  # must not raise

    def test_record_error_sets_status_and_records_exception(self):
        """Verify StatusCode.ERROR with description, and record_exception called."""
        mock_span = MagicMock()
        exc = Exception("tool broke")
        record_tool_error(mock_span, exc)

        mock_span.set_status.assert_called_with(
            StatusCode.ERROR, description="tool broke"
        )
        mock_span.record_exception.assert_called_with(exc)


# ═══════════════════════════════════════════════════════════════════════════
#  CLASS 5: TestBootstrapMCPTracing (1 test)
# ═══════════════════════════════════════════════════════════════════════════


class TestBootstrapMCPTracing(unittest.TestCase):
    """bootstrap_mcp_tracing() — kill-switch behaviour."""

    @patch.dict(os.environ, {"TRACING_ENABLED": "false"})
    def test_returns_none_when_tracing_disabled(self):
        """TRACING_ENABLED=false → returns None, phoenix never called."""
        result = bootstrap_mcp_tracing("test")
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
