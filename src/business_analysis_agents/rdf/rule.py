"""Rule RDF conversion."""

from __future__ import annotations

from rdflib import Graph

from business_analysis_agents.models import DataRuleExtractionOutput


def rules_to_graph(data_rules: DataRuleExtractionOutput) -> Graph:
    """Convert structured business rules to an RDFLib graph.

    TODO: Define namespaces and triples for Rule RDF.
    """

    _ = data_rules
    return Graph()
