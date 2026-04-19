"""Tests for the artifact detection logic (nasiko.app.ingestion.detector).

Ported from R1/tests/ingestion/test_detector.py to use the main nasiko
package import paths.  Tests enforce the expected agent project structure
contract: src/main.py, Dockerfile, docker-compose.yml must all be present.
"""

import os
import pytest

from nasiko.app.ingestion.detector import detect_artifact_type
from nasiko.app.ingestion.models import ArtifactType, DetectionConfidence
from nasiko.app.ingestion.exceptions import AmbiguousArtifactError, MissingStructureError


def _create_project(tmp_path, py_files: dict, extra_files=None):
    """Helper: create a valid project structure with the contract files."""
    # Always create the contract files
    (tmp_path / "src").mkdir(exist_ok=True)
    (tmp_path / "Dockerfile").write_text("FROM python:3.11-slim")
    (tmp_path / "docker-compose.yml").write_text("version: '3.8'\nservices:\n  app:\n    build: .")

    for path, content in py_files.items():
        fp = tmp_path / path
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(content)

    if extra_files:
        for path, content in extra_files.items():
            fp = tmp_path / path
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_text(content)


def test_detects_mcp_from_fastmcp_import(tmp_path):
    _create_project(tmp_path, {"src/main.py": "from fastmcp import FastMCP"})
    result = detect_artifact_type(str(tmp_path))
    assert result.artifact_type == ArtifactType.MCP_SERVER
    assert result.detected_framework == "mcp"


def test_detects_mcp_from_mcp_server_import(tmp_path):
    _create_project(tmp_path, {"src/main.py": "from mcp.server.fastmcp import FastMCP"})
    result = detect_artifact_type(str(tmp_path))
    assert result.artifact_type == ArtifactType.MCP_SERVER


def test_detects_langchain_agent(tmp_path):
    _create_project(tmp_path, {"src/main.py": "from langchain_core.tools import tool"})
    result = detect_artifact_type(str(tmp_path))
    assert result.artifact_type == ArtifactType.LANGCHAIN_AGENT
    assert result.detected_framework == "langchain"


def test_detects_crewai_agent(tmp_path):
    _create_project(tmp_path, {"src/main.py": "from crewai import Agent, Task, Crew"})
    result = detect_artifact_type(str(tmp_path))
    assert result.artifact_type == ArtifactType.CREWAI_AGENT
    assert result.detected_framework == "crewai"


def test_raises_on_zero_frameworks(tmp_path):
    _create_project(tmp_path, {"src/main.py": "import os\nimport sys"})
    with pytest.raises(AmbiguousArtifactError):
        detect_artifact_type(str(tmp_path))


def test_raises_on_multiple_frameworks(tmp_path):
    _create_project(tmp_path, {
        "src/main.py": "from fastmcp import FastMCP\nfrom crewai import Agent"
    })
    with pytest.raises(AmbiguousArtifactError, match="Multiple frameworks"):
        detect_artifact_type(str(tmp_path))


def test_missing_src_main_raises(tmp_path):
    """Missing src/main.py → MissingStructureError."""
    (tmp_path / "Dockerfile").write_text("FROM python:3.11")
    (tmp_path / "docker-compose.yml").write_text("version: '3.8'")
    (tmp_path / "server.py").write_text("from mcp import Server")
    with pytest.raises(MissingStructureError, match="src/main.py"):
        detect_artifact_type(str(tmp_path))


def test_missing_dockerfile_raises(tmp_path):
    """Missing Dockerfile → MissingStructureError."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("from mcp import Server")
    (tmp_path / "docker-compose.yml").write_text("version: '3.8'")
    with pytest.raises(MissingStructureError, match="Dockerfile"):
        detect_artifact_type(str(tmp_path))


def test_missing_docker_compose_raises(tmp_path):
    """Missing docker-compose.yml → MissingStructureError."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("from mcp import Server")
    (tmp_path / "Dockerfile").write_text("FROM python:3.11")
    with pytest.raises(MissingStructureError, match="docker-compose"):
        detect_artifact_type(str(tmp_path))


def test_agentcard_exists_true(tmp_path):
    _create_project(tmp_path, {"src/main.py": "from crewai import Agent"},
                    extra_files={"agentcard.json": '{"name": "test"}'})
    result = detect_artifact_type(str(tmp_path))
    assert result.agentcard_exists is True


def test_agentcard_exists_false(tmp_path):
    _create_project(tmp_path, {"src/main.py": "from crewai import Agent"})
    result = detect_artifact_type(str(tmp_path))
    assert result.agentcard_exists is False


def test_requirements_path_present(tmp_path):
    _create_project(tmp_path, {"src/main.py": "from fastmcp import FastMCP"},
                    extra_files={"requirements.txt": "fastmcp==0.1.0"})
    result = detect_artifact_type(str(tmp_path))
    assert result.requirements_path is not None
    assert "requirements.txt" in result.requirements_path


def test_requirements_path_absent(tmp_path):
    _create_project(tmp_path, {"src/main.py": "from fastmcp import FastMCP"})
    result = detect_artifact_type(str(tmp_path))
    assert result.requirements_path is None


def test_entry_point_always_src_main(tmp_path):
    """Entry point is always src/main.py per the contract."""
    _create_project(tmp_path, {"src/main.py": "from fastmcp import FastMCP"})
    result = detect_artifact_type(str(tmp_path))
    assert result.entry_point == os.path.join("src", "main.py")


def test_artifact_id_unique_per_call(tmp_path):
    _create_project(tmp_path, {"src/main.py": "from fastmcp import FastMCP"})
    r1 = detect_artifact_type(str(tmp_path))
    r2 = detect_artifact_type(str(tmp_path))
    assert r1.artifact_id != r2.artifact_id


def test_detection_confidence_high(tmp_path):
    _create_project(tmp_path, {"src/main.py": "from fastmcp import FastMCP"})
    result = detect_artifact_type(str(tmp_path))
    assert result.confidence == DetectionConfidence.HIGH


def test_skips_unparseable_py_file(tmp_path):
    _create_project(tmp_path, {
        "src/main.py": "from fastmcp import FastMCP",
        "broken.py": "def (((broken syntax",
    })
    result = detect_artifact_type(str(tmp_path))
    assert result.artifact_type == ArtifactType.MCP_SERVER


def test_all_contract_fields_present(tmp_path):
    _create_project(tmp_path, {"src/main.py": "from fastmcp import FastMCP"})
    result = detect_artifact_type(str(tmp_path))
    d = result.model_dump()
    required = [
        "artifact_id",
        "artifact_type",
        "confidence",
        "source_path",
        "entry_point",
        "detected_framework",
        "requirements_path",
        "created_at",
        "agentcard_exists",
    ]
    for field in required:
        assert field in d, f"Missing field: {field}"
