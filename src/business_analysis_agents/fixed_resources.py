"""Paths and loading helpers for fixed ontology and SHACL resources."""

from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SCENARIO_ONTOLOGY = PROJECT_ROOT / "ontology" / "scenario_ontology.ttl"
DEFAULT_WORKFLOW_ONTOLOGY = PROJECT_ROOT / "ontology" / "workflow_ontology.ttl"
DEFAULT_DATA_ONTOLOGY = PROJECT_ROOT / "ontology" / "data_ontology.ttl"
DEFAULT_RULE_ONTOLOGY = PROJECT_ROOT / "ontology" / "rule_ontology.ttl"


def load_fixed_turtle(path: Path | str, resource_name: str) -> tuple[Path, str]:
    """Load a required fixed Turtle file without an AI-generated fallback."""

    resource_path = Path(path)
    if not resource_path.is_file():
        raise FileNotFoundError(
            f"Required {resource_name} Turtle file was not found: {resource_path}"
        )
    return resource_path.resolve(), resource_path.read_text(encoding="utf-8")
