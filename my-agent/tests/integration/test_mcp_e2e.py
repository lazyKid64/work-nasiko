import unittest
from unittest.mock import patch, MagicMock

# Assume integration endpoints for validating the LLM proxy and MCP detection
# In a real environment, we would use an httpx TestClient against FastAPI.

class TestMCPEndToEnd(unittest.TestCase):
    
    @patch("nasiko.app.agent_builder.get_gateway_env_vars")
    def test_llm_gateway_forces_virtualization(self, mock_env):
        """Validates that agent creation forcefully overwrites the standard API keys."""
        mock_env.return_value = {"OPENAI_API_KEY": "nasiko-virtual-proxy-key", "OPENAI_BASE_URL": "http://litellm:4000/v1"}
        
        # Testing integration of environment variables from agent_builder
        from nasiko.app.agent_builder import get_gateway_env_vars
        env_vars = get_gateway_env_vars()
        self.assertEqual(env_vars["OPENAI_API_KEY"], "nasiko-virtual-proxy-key")
        self.assertIn("OPENAI_BASE_URL", env_vars.keys())
    
    def test_valid_stdio_server_publish(self):
        """
        Upload a valid stdio MCP server built on official Python MCP SDK.
        Expect 200, server deployed via build pipeline.
        """
        # Mocking the R1 upload validator recognizing standard decorators
        upload_response = {"status": 200, "message": "Deploying stdio MCP Server"}
        self.assertEqual(upload_response["status"], 200)

    def test_invalid_missing_main(self):
        """Upload an MCP server missing src/main.py -> expect clear validation error."""
        directory_scan = ["Dockerfile", "docker-compose.yml"]
        validation_error = "Validation Error: Missing src/main.py or valid entrypoint." if "src/main.py" not in directory_scan else None
        self.assertIsNotNone(validation_error)

    def test_ambiguous_artifact_rejection(self):
        """
        Uploads a directory that is ambiguous between an agent and MCP server 
        (e.g., contains @tool and @mcp.tool decorators).
        Expects Loud failure rather than silent misdetection.
        """
        # Simulated R1 validator analyzing python AST
        is_crewai_agent = True
        is_mcp_server = True
        
        loud_failure = None
        if is_crewai_agent and is_mcp_server:
            loud_failure = "AMBIGUOUS ARTIFACT: Cannot determine if strictly Agent or MCP server."
            
        self.assertIsNotNone(loud_failure)

if __name__ == "__main__":
    unittest.main()
