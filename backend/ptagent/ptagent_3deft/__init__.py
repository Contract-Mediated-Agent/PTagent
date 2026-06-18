"""Independent 3DEFT to PhaseTracer pipeline for PTagent."""

from __future__ import annotations

from .approval import ApprovalResult, approve_contract_gate
from .contract import (
    CONTRACT_MARKER,
    CONTRACT_SCHEMA,
    REVIEW_GATE_NAME,
    ThreeDeftBlocked,
    build_contract_template,
    build_review_gate_report,
    parse_contract,
    validate_contract_template,
    write_resolved_artifacts,
)
from .environment import check_wolfram_and_dralgo, ensure_dralgo_ready, find_wolframscript, install_dralgo
from .extraction import EXTRACTION_REPORT_NAME, SourceExtractionResult, extract_source_to_template
from .mathematica_compare import MathematicaComparisonResult, compare_mathematica_fixed_v3d
from .mathematica_expr import (
    ConvertedMathematicaExpression,
    classify_mathematica_expression,
    convert_mathematica_inputform_expression,
    postprocess_cform_expression,
)
from .phasetracer import PhaseTracerRenderResult, compile_phasetracer_template
from .runner import run_dralgo_template

__all__ = [
    "CONTRACT_MARKER",
    "CONTRACT_SCHEMA",
    "ApprovalResult",
    "ConvertedMathematicaExpression",
    "EXTRACTION_REPORT_NAME",
    "MathematicaComparisonResult",
    "PhaseTracerRenderResult",
    "REVIEW_GATE_NAME",
    "SourceExtractionResult",
    "ThreeDeftBlocked",
    "build_contract_template",
    "build_review_gate_report",
    "approve_contract_gate",
    "check_wolfram_and_dralgo",
    "classify_mathematica_expression",
    "compile_phasetracer_template",
    "compare_mathematica_fixed_v3d",
    "convert_mathematica_inputform_expression",
    "ensure_dralgo_ready",
    "extract_source_to_template",
    "find_wolframscript",
    "install_dralgo",
    "parse_contract",
    "postprocess_cform_expression",
    "run_dralgo_template",
    "validate_contract_template",
    "write_resolved_artifacts",
]
