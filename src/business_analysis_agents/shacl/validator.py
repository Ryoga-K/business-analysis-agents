"""pySHACL validation wrapper."""

from __future__ import annotations

from rdflib import Graph

from business_analysis_agents.models import ShaclValidationResult


def validate_graph(data_graph: Graph, shapes_graph: Graph) -> ShaclValidationResult:
    """Validate RDF data with pySHACL.

    TODO: Call pyshacl.validate and map the validation report into Pydantic
    violation models.
    """

    _ = (data_graph, shapes_graph)
    raise NotImplementedError("SHACL validation is not implemented yet.")
