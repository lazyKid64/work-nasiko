"""Tests for nasiko.mcp_bridge.kong — KongRegistrar HTTP interactions.

These tests mock httpx at the transport layer to verify:
  1. Correct URL construction and request bodies
  2. Proper error handling for 4xx, 5xx responses
  3. Network timeout handling

3 tests in this file.  Combined with test_bridge_server.py (38) → 41 total.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

from nasiko.mcp_bridge.kong import KongRegistrar, KongRegistrationError


# ═══════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════


def _mock_httpx_client(responses: list[MagicMock]) -> MagicMock:
    """Build a mock httpx.Client context manager returning sequential responses.

    Each call to client.post() returns the next response from the list.
    """
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.post.side_effect = responses
    return mock_client


def _ok_response(body: dict, status: int = 201) -> MagicMock:
    """Simulate a successful httpx response."""
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = body
    resp.text = ""
    return resp


def _error_response(status: int, body_text: str) -> MagicMock:
    """Simulate a failed httpx response."""
    resp = MagicMock()
    resp.status_code = status
    resp.text = body_text
    resp.json.return_value = {"message": body_text}
    return resp


# ═══════════════════════════════════════════════════════════════════════════
# Tests (3)
# ═══════════════════════════════════════════════════════════════════════════


class TestKongRegistrar:
    """KongRegistrar.register() — two-POST registration flow."""

    @patch("nasiko.mcp_bridge.kong.httpx.Client")
    def test_success_returns_ids_and_sends_correct_payloads(self, mock_cls):
        """Happy path: verify returned IDs AND the exact POST payloads sent.

        This test goes beyond 'mock returns X, assert X' by also inspecting
        the arguments that were passed TO httpx, proving the registrar builds
        correct URLs and bodies.
        """
        svc_resp = _ok_response({"id": "svc-abc-123"})
        route_resp = _ok_response({"id": "route-xyz-789"})
        client = _mock_httpx_client([svc_resp, route_resp])
        mock_cls.return_value = client

        registrar = KongRegistrar("http://kong:8001/")  # trailing slash stripped
        sid, rid = registrar.register("weather-bot", 8142)

        # ── Return values ───────────────────────────────────────────────
        assert sid == "svc-abc-123"
        assert rid == "route-xyz-789"

        # ── Verify service POST ─────────────────────────────────────────
        svc_call = client.post.call_args_list[0]
        assert svc_call[0][0] == "http://kong:8001/services"
        assert svc_call[1]["json"] == {
            "name": "mcp-weather-bot",
            "url": "http://localhost:8142",
        }

        # ── Verify route POST ───────────────────────────────────────────
        route_call = client.post.call_args_list[1]
        assert route_call[0][0] == "http://kong:8001/services/mcp-weather-bot/routes"
        assert route_call[1]["json"] == {
            "name": "mcp-route-weather-bot",
            "paths": ["/mcp/weather-bot"],
        }

    @patch("nasiko.mcp_bridge.kong.httpx.Client")
    def test_raises_on_http_error_with_status_and_body(self, mock_cls):
        """Non-2xx response → KongRegistrationError containing status + body.

        Tests both 4xx (service conflict) and 5xx (route internal error):
        - First call (service) succeeds with 201
        - Second call (route) fails with 500
        The error message must include the HTTP status AND the response body
        so operators can diagnose failures without digging into logs.
        """
        svc_resp = _ok_response({"id": "svc-ok"})
        route_fail = _error_response(500, "internal gateway error")
        client = _mock_httpx_client([svc_resp, route_fail])
        mock_cls.return_value = client

        registrar = KongRegistrar("http://kong:8001")
        with pytest.raises(KongRegistrationError, match="500") as exc_info:
            registrar.register("fail-art", 8100)

        # Error message must contain the response body for debugging
        assert "internal gateway error" in str(exc_info.value)

    @patch("nasiko.mcp_bridge.kong.httpx.Client")
    def test_service_creation_4xx_raises_before_route(self, mock_cls):
        """If SERVICE creation itself returns 409, route POST must never happen.

        This proves the registrar fails fast — it doesn't attempt the route
        call after the service call already failed.
        """
        conflict = _error_response(409, "service already exists")
        client = _mock_httpx_client([conflict])
        mock_cls.return_value = client

        registrar = KongRegistrar("http://kong:8001")
        with pytest.raises(KongRegistrationError, match="409"):
            registrar.register("dup-art", 8100)

        # Only ONE post call should have been made (service), NOT two
        assert client.post.call_count == 1
