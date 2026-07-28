"""Workflow RDF conversion."""

from __future__ import annotations

from rdflib import Graph

from business_analysis_agents.models import WorkflowExtractionOutput


def workflow_to_graph(workflow: WorkflowExtractionOutput) -> Graph:
    """Convert structured workflow data to an RDFLib graph.

    TODO: Define namespaces and triples for Workflow RDF.
    """

    _ = workflow
    return Graph()
