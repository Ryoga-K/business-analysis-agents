"""RDFLib and pySHACL validation for Workflow RDF."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from pyshacl import validate as pyshacl_validate
from rdflib import Graph, OWL, RDF, RDFS, SH, URIRef

from business_analysis_agents.models import (
    OntologyValidationResult,
    RdfKind,
    RdfParseError,
    Severity,
    ShaclValidationResult,
    ShaclViolation,
    VocabularyValidationResult,
    WorkflowRdfValidationResult,
)


STANDARD_NAMESPACES = (
    str(RDF),
    str(RDFS),
    "http://www.w3.org/2001/XMLSchema#",
    "http://www.w3.org/ns/prov#",
    str(SH),
)


@dataclass(frozen=True)
class ParsedGraph:
    """Parsed graph and the original Turtle hash."""

    graph: Graph
    turtle_hash: str


def content_hash(text: str) -> str:
    """Return a stable SHA-256 hash for text content."""

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def parse_turtle(turtle_text: str, iteration: int = 0) -> tuple[Graph | None, list[RdfParseError]]:
    """Parse Turtle text with RDFLib and return parse errors instead of raising."""

    graph = Graph()
    try:
        graph.parse(data=turtle_text, format="turtle")
    except Exception as error:  # RDFLib exposes several parser exception classes.
        return None, [
            RdfParseError(
                error_type=type(error).__name__,
                error_message=str(error),
                iteration=iteration,
            )
        ]
    return graph, []


def _defined_terms(ontology_graph: Graph) -> set[str]:
    terms: set[str] = set()
    for subject in ontology_graph.subjects():
        if isinstance(subject, URIRef):
            terms.add(str(subject))
    for term in ontology_graph.objects(None, RDFS.domain):
        if isinstance(term, URIRef):
            terms.add(str(term))
    for term in ontology_graph.objects(None, RDFS.range):
        if isinstance(term, URIRef):
            terms.add(str(term))
    return terms


def _is_standard_term(uri: str) -> bool:
    return any(uri.startswith(namespace) for namespace in STANDARD_NAMESPACES)


def _declared_classes(ontology_graph: Graph) -> set[str]:
    return {
        str(subject)
        for class_type in (RDFS.Class, OWL.Class)
        for subject in ontology_graph.subjects(RDF.type, class_type)
        if isinstance(subject, URIRef)
    }


def _declared_properties(ontology_graph: Graph) -> set[str]:
    property_types = (
        RDF.Property,
        OWL.ObjectProperty,
        OWL.DatatypeProperty,
        OWL.AnnotationProperty,
    )
    properties = {
        str(subject)
        for property_type in property_types
        for subject in ontology_graph.subjects(RDF.type, property_type)
        if isinstance(subject, URIRef)
    }
    for relation in (RDFS.domain, RDFS.range):
        properties.update(
            str(subject)
            for subject in ontology_graph.subjects(relation, None)
            if isinstance(subject, URIRef)
        )
    return properties


def validate_vocabulary(workflow_graph: Graph, ontology_graph: Graph) -> VocabularyValidationResult:
    """Check that RDF uses only classes and properties declared in the fixed ontology."""

    declared_classes = _declared_classes(ontology_graph)
    declared_properties = _declared_properties(ontology_graph)
    unauthorized: set[str] = set()

    for predicate in workflow_graph.predicates():
        uri = str(predicate)
        if predicate != RDF.type and uri not in declared_properties:
            unauthorized.add(uri)

    for rdf_type in workflow_graph.objects(None, RDF.type):
        if isinstance(rdf_type, URIRef):
            uri = str(rdf_type)
            if uri not in declared_classes:
                unauthorized.add(uri)

    terms = sorted(unauthorized)
    return VocabularyValidationResult(
        vocabulary_conforms=not terms,
        unauthorized_term_count=len(terms),
        unauthorized_terms=terms,
    )


def _shacl_violations(report_graph: Graph, rdf_kind: RdfKind) -> list[ShaclViolation]:
    violations: list[ShaclViolation] = []
    for result in report_graph.subjects(RDF.type, SH.ValidationResult):
        message = next(report_graph.objects(result, SH.resultMessage), None)
        focus_node = next(report_graph.objects(result, SH.focusNode), None)
        path = next(report_graph.objects(result, SH.resultPath), None)
        source_shape = next(report_graph.objects(result, SH.sourceShape), None)
        severity = next(report_graph.objects(result, SH.resultSeverity), None)
        violations.append(
            ShaclViolation(
                focus_node=str(focus_node or ""),
                message=str(message or "SHACL violation"),
                path=str(path) if path else None,
                source_shape=str(source_shape) if source_shape else None,
                severity=Severity.ERROR if severity != SH.Warning else Severity.WARNING,
                rdf_kind=rdf_kind,
            )
        )
    return violations


def run_pyshacl(
    data_graph: Graph,
    ontology_graph: Graph,
    shapes_graph: Graph,
    rdf_kind: RdfKind = RdfKind.WORKFLOW,
) -> ShaclValidationResult:
    """Validate RDF data with pySHACL."""

    conforms, report_graph, report_text = pyshacl_validate(
        data_graph,
        shacl_graph=shapes_graph,
        ont_graph=ontology_graph,
        inference="rdfs",
        abort_on_first=False,
    )
    return ShaclValidationResult(
        conforms=bool(conforms),
        violations=_shacl_violations(report_graph, rdf_kind),
        report_text=str(report_text),
    )


def validate_ontology_and_shapes(
    ontology_turtle: str,
    shacl_turtle: str,
) -> OntologyValidationResult:
    """Validate generated ontology and shapes before fixing them for the run."""

    ontology_graph, ontology_errors = parse_turtle(ontology_turtle)
    shapes_graph, shapes_errors = parse_turtle(shacl_turtle)
    parse_errors = ontology_errors + shapes_errors
    if ontology_graph is None or shapes_graph is None:
        return OntologyValidationResult(
            ontology_turtle_valid=ontology_graph is not None,
            shacl_turtle_valid=shapes_graph is not None,
            prefixes_defined=False,
            domain_range_references_defined=False,
            shapes_references_defined_terms=False,
            no_duplicate_uri_definitions=False,
            ontology_hash=content_hash(ontology_turtle),
            shapes_hash=content_hash(shacl_turtle),
            parse_errors=parse_errors,
        )

    defined = _defined_terms(ontology_graph)
    undefined: set[str] = set()
    for term in list(ontology_graph.objects(None, RDFS.domain)) + list(
        ontology_graph.objects(None, RDFS.range)
    ):
        if isinstance(term, URIRef) and not _is_standard_term(str(term)) and str(term) not in defined:
            undefined.add(str(term))

    for term in shapes_graph.objects():
        if isinstance(term, URIRef):
            uri = str(term)
            if _is_standard_term(uri):
                continue
            if uri.startswith(str(SH)):
                continue
            if uri not in defined and not uri.endswith("Shape"):
                undefined.add(uri)

    declared_types: dict[str, set[str]] = {}
    for subject, rdf_type in ontology_graph.subject_objects(RDF.type):
        if isinstance(subject, URIRef) and isinstance(rdf_type, URIRef):
            declared_types.setdefault(str(subject), set()).add(str(rdf_type))
    duplicate_kind_definitions = [
        uri
        for uri, types in declared_types.items()
        if str(RDFS.Class) in types and str(RDF.Property) in types
    ]
    has_prefixes = bool(dict(ontology_graph.namespaces())) and bool(dict(shapes_graph.namespaces()))
    return OntologyValidationResult(
        ontology_turtle_valid=True,
        shacl_turtle_valid=True,
        prefixes_defined=has_prefixes,
        domain_range_references_defined=not undefined,
        shapes_references_defined_terms=not undefined,
        no_duplicate_uri_definitions=not duplicate_kind_definitions,
        ontology_hash=content_hash(ontology_turtle),
        shapes_hash=content_hash(shacl_turtle),
        undefined_references=sorted(undefined | set(duplicate_kind_definitions)),
    )


def validate_rdf(
    rdf_turtle: str,
    ontology_turtle: str,
    shacl_turtle: str,
    iteration: int = 0,
    rdf_kind: RdfKind = RdfKind.WORKFLOW,
    additional_data_turtle: str | None = None,
) -> WorkflowRdfValidationResult:
    """Validate RDF syntax, vocabulary, and SHACL constraints."""

    rdf_graph, rdf_errors = parse_turtle(rdf_turtle, iteration=iteration)
    ontology_graph, ontology_errors = parse_turtle(ontology_turtle, iteration=iteration)
    shapes_graph, shapes_errors = parse_turtle(shacl_turtle, iteration=iteration)
    parse_errors = rdf_errors + ontology_errors + shapes_errors
    if additional_data_turtle:
        additional_graph, additional_errors = parse_turtle(
            additional_data_turtle,
            iteration=iteration,
        )
        parse_errors += additional_errors
    else:
        additional_graph = None

    if rdf_graph is None or ontology_graph is None or shapes_graph is None:
        return WorkflowRdfValidationResult(
            structure_conforms=False,
            shacl_conforms=False,
            parse_errors=parse_errors,
        )
    if additional_data_turtle and additional_graph is None:
        return WorkflowRdfValidationResult(
            structure_conforms=False,
            shacl_conforms=False,
            parse_errors=parse_errors,
        )

    validation_graph = rdf_graph
    if additional_graph is not None:
        validation_graph = rdf_graph + additional_graph

    vocabulary = validate_vocabulary(rdf_graph, ontology_graph)
    shacl_result = run_pyshacl(
        validation_graph,
        ontology_graph,
        shapes_graph,
        rdf_kind=rdf_kind,
    )
    return WorkflowRdfValidationResult(
        structure_conforms=True,
        shacl_conforms=shacl_result.conforms,
        vocabulary=vocabulary,
        shacl_result=shacl_result,
    )


def validate_workflow_rdf(
    workflow_turtle: str,
    ontology_turtle: str,
    shacl_turtle: str,
    iteration: int = 0,
) -> WorkflowRdfValidationResult:
    """Validate Workflow RDF syntax, vocabulary, and SHACL constraints."""

    return validate_rdf(
        workflow_turtle,
        ontology_turtle,
        shacl_turtle,
        iteration=iteration,
        rdf_kind=RdfKind.WORKFLOW,
    )


def validation_feedback(validation: WorkflowRdfValidationResult) -> list[str]:
    """Create compact feedback messages for workflow_revision mode."""

    messages: list[str] = []
    for error in validation.parse_errors:
        messages.append(f"RDF parse error: {error.error_type}: {error.error_message}")
    for term in validation.vocabulary.unauthorized_terms:
        messages.append(f"Unauthorized vocabulary term: {term}")
    if validation.shacl_result:
        for violation in validation.shacl_result.violations:
            messages.append(
                f"SHACL violation focus={violation.focus_node} path={violation.path}: {violation.message}"
            )
    return messages
