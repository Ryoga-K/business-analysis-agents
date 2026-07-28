"""Data RDF conversion."""

from __future__ import annotations

from rdflib import Graph

from business_analysis_agents.models import DataRuleExtractionOutput


def data_to_graph(data_rules: DataRuleExtractionOutput) -> Graph:
    """Convert structured data entities to an RDFLib graph.

    TODO: Define namespaces and triples for Data RDF.
    """

    _ = data_rules
    return Graph()
